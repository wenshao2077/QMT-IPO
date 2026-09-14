"""Read-only local maintenance summaries and explicitly requested support export.

Output is a constructed allow-list, not redacted raw files. No Webhook bytes,
account IDs/digests, QMT paths, order IDs/codes, messages, XML or logs are exported.
There are no repair, activation, network, broker or database-write operations.
SQLite read-only WAL access may manage -wal/-shm sidecars; never delete them.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
import zipfile

from calendar_health import coverage_report
from environment_check import environment_report
from notification_policy import combined_notification_stats
from release_info import VERSION
from runtime import validate_config
from support import CHINA, china_now

MAX_JSON_BYTES = 256 * 1024
TASKS = ('QmtIPO3-Cycle', 'QmtIPO3-Notify', 'QmtIPO3-Monitor', 'QmtIPO3-Backup')
STAGES = {'prepared', 'environment_ready', 'source_started', 'source_ready',
          'dependencies_ready', 'tasks_ready', 'complete'}
ISSUES = {
    'configuration_unreadable': ('critical', '配置不可读；保留现场，由维护人员核对原私有配置。'),
    'configuration_recovery_required': ('critical', '首次配置保存中断；不要删除阻断标记或启用任务。'),
    'source_maintenance_pending': ('critical', '源码维护未完成；核对原维护回执和代码备份，不盲目恢复任务。'),
    'installation_incomplete': ('warning', '使用原来确切的源码包运行 Plan；只有通过身份检查才可 ResumeNew。'),
    'installation_state_unreadable': ('critical', '安装回执不可读；不要通过 New 覆盖现有目录。'),
    'ledger_unavailable': ('critical', '委托账本不可读或缺失；禁止删库重建，先核对券商委托与本地备份。'),
    'unresolved_intents': ('warning', '存在未确认意图；通过单独授权的只读查询核对，不能自动重报。'),
    'notification_backlog': ('warning', '通知发送失败；本地检查网络与通知配置，测试发送需要单独确认。'),
    'monitor_ledger_unavailable': ('critical', '监控通知账本不可读；不能把状态未知显示为零条。'),
    'task_state_unavailable': ('warning', '无法确认四个任务的状态或归属；不自动修复、不启用任务。'),
    'task_plan_inconsistent': ('critical', '任务与配置开关不一致；在本机核对，不手动触发申购。'),
    'task_running': ('notice', '任务正在运行；升级前等待退出，不强杀进程或客户端。'),
    'sqlite_runtime_review': ('warning', 'SQLite 运行时未识别为已含官方 WAL-reset 修复的版本；安排运行时兼容验收，不自行替换 DLL、SDK 或账本模式。'),
    'local_environment_mismatch': ('warning', '当前环境不满足固定依赖组合；升级不能擅自变更 SDK。'),
    'calendar_coverage_missing': ('critical', '近期交易日日历未知；保持禁止提交，核对并导入年度公告包。'),
    'calendar_coverage_expiring': ('warning', '交易日历覆盖将到期；从可信维护渠道取得年度包，核对来源后导入。'),
    'calendar_conflict_ahead': ('critical', '日历来源存在冲突；不要强制把未知改成交易日。'),
    'calendar_check_failed': ('critical', '无法完成日历检查；保持原日历，人工核验。'),
    'backup_unverified': ('notice', '当前没有本轮核验过的备份；回滚源码不等于恢复数据库。'),
}


class MaintenanceError(ValueError):
    """All instances contain a stable code, never arbitrary file contents."""


def read_local_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise MaintenanceError('local_record_unreadable')
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise MaintenanceError('local_record_too_large')
    value = json.loads(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise MaintenanceError('local_record_structure')
    return value


def _version(value):
    return value if isinstance(value, str) and re.fullmatch(r'\d{1,5}(?:\.\d{1,12}){1,4}(?:[-+.][a-zA-Z0-9.]{1,30})?', value) else None


def _safe_environment(provider):
    try:
        raw = provider()
        # Never echo arbitrary distribution metadata, machine strings or issue text.
        packages = {}
        from release_info import PINNED_DEPENDENCIES
        for name, required in PINNED_DEPENDENCIES.items():
            packages[name] = {'required': required,
                              'installed': _version(raw.get('dependencies', {}).get(name, {}).get('installed'))}
        python = raw.get('python', [])
        return {'ok': raw.get('ok') is True,
                'system': raw.get('system') if raw.get('system') in ('win32', 'linux', 'darwin') else 'other',
                'python': list(python[:3]) if isinstance(python, (list, tuple)) and all(type(x) is int and 0 <= x <= 999 for x in python[:3]) else [],
                'bits': raw.get('bits') if raw.get('bits') in (32, 64) else None,
                'tkinter_module_present': raw.get('tkinter_module_present') is True,
                'dependencies': packages,
                'sqlite_runtime': {'version': _version(raw.get('sqlite_runtime',{}).get('version')),
                                   'review_required': raw.get('sqlite_runtime',{}).get('review_required') is not False}}
    except Exception:
        return {'ok': False, 'system': 'unknown', 'dependencies': {}}


def ledger_summary(path):
    """Query existing database only, with a time budget. Never initialize or migrate."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return {'status': 'unavailable', 'intent_count': None, 'unresolved_count': None}
    try:
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
        try:
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')  # One consistent read snapshot, including WAL.
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise MaintenanceError('ledger_integrity_failed')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'intents', 'outbox'} <= tables:
                raise MaintenanceError('ledger_schema_failed')
            count = db.execute('SELECT COUNT(*) FROM intents').fetchone()[0]
            # Unknown future states also require review; do not export arbitrary state text.
            unresolved = db.execute("SELECT COUNT(*) FROM intents WHERE state NOT IN ('REPORTED','SUCCEEDED','EXTERNAL_ACCEPTED')").fetchone()[0]
            return {'status': 'readable', 'intent_count': count, 'unresolved_count': unresolved}
        finally:
            db.close()
    except (sqlite3.Error, OSError, MaintenanceError):
        return {'status': 'unavailable', 'intent_count': None, 'unresolved_count': None}


def _task_summary(root, provider):
    if provider is None:
        if sys.platform != 'win32':
            return {'readable': False, 'plan_state': 'unknown', 'tasks': []}
        from panel_backend import WindowsController
        provider = lambda r: WindowsController(r).call('Snapshot')
    try:
        raw = provider(root)
        rows = raw.get('tasks', [])
        if raw.get('ok') is not True or not isinstance(rows, list) or len(rows) != 4 or {r.get('name') for r in rows} != set(TASKS):
            raise MaintenanceError('task_state_unavailable')
        safe = []
        for name in TASKS:
            row = next(r for r in rows if r['name'] == name)
            safe.append({'name': name, 'exists': row.get('exists') is True,
                         'enabled': row.get('enabled') if type(row.get('enabled')) is bool else None,
                         'state': row.get('state') if row.get('state') in ('Disabled','Ready','Running','Queued') else 'Unknown'})
        flag = raw.get('execution_enabled')
        if type(flag) is not bool or not all(r['exists'] and r['enabled'] is not None and r['state'] != 'Unknown' for r in safe):
            state = 'unknown'
        elif flag and all(r['enabled'] for r in safe):
            state = 'enabled'
        elif not flag and not safe[0]['enabled']:
            state = 'disabled'
        else:
            state = 'inconsistent'
        return {'readable': state != 'unknown', 'plan_state': state, 'tasks': safe}
    except Exception:
        return {'readable': False, 'plan_state': 'unknown', 'tasks': []}


def status_report(root, *, now=None, environment_provider=None, task_provider=None):
    """No mutation. Local file reads are part of an explicit maintenance operation."""
    root = Path(root).resolve()
    now = now or china_now()
    issues = []
    result = {'schema_version': 1, 'tool_version': VERSION, 'observed_at': now.astimezone(CHINA).isoformat(),
              'account_connected': False, 'message_sent': False, 'submission_calls': 0,
              'changes_applied': False, 'not_an_account_permission_check': True,
              'sqlite_readonly_may_manage_sidecars': True}
    result['environment'] = _safe_environment(environment_provider or environment_report)
    if not result['environment']['ok']:
        issues.append('local_environment_mismatch')
    if result['environment'].get('sqlite_runtime',{}).get('review_required',True):
        issues.append('sqlite_runtime_review')
    try:
        identity = read_local_json(root/'installed-source.json')
        result['installed_version'] = _version(identity.get('version'))
    except (OSError, ValueError, UnicodeError):
        result['installed_version'] = None
    result['maintenance_pending'] = (root/'maintenance.json').exists()
    if result['maintenance_pending']:
        issues.append('source_maintenance_pending')
    result['install_stage'] = None
    if (root/'new-install.json').exists():
        try:
            stage = read_local_json(root/'new-install.json').get('stage')
            result['install_stage'] = stage if stage in STAGES else 'unknown'
            if stage not in STAGES:
                issues.append('installation_state_unreadable')
            elif stage != 'complete':
                issues.append('installation_incomplete')
        except (OSError, ValueError, UnicodeError, TypeError):
            issues.append('installation_state_unreadable')
    try:
        # Diagnose interrupted writes without granting runtime permission.
        from runtime import validate_config_shape
        config = validate_config_shape(read_local_json(root/'config.json'))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        issues.append('configuration_unreadable')
    else:
        result['execution_enabled'] = config['enable_execution']
        result['configuration_pending'] = (Path(config['control_dir'])/'configuration-pending.json').exists()
        if result['configuration_pending']:
            issues.append('configuration_recovery_required')
        # Deliberately do not read the Webhook file or export any config field.
        result['ledger'] = ledger_summary(Path(config['state_dir'])/'state.sqlite3')
        if result['ledger']['status'] == 'unavailable':
            issues.append('ledger_unavailable')
        elif result['ledger']['unresolved_count']:
            issues.append('unresolved_intents')
        result['notifications'] = combined_notification_stats(config)
        queues = result['notifications']['queues']
        if queues['monitor']['status'] == 'unavailable':
            issues.append('monitor_ledger_unavailable')
        if any((r['failed'] or 0) > 0 for r in queues.values()):
            issues.append('notification_backlog')
        try:
            result['calendar_coverage'] = coverage_report(config, now.astimezone(CHINA).date().isoformat())
            code = result['calendar_coverage']['issue_code']
            if code:
                issues.append(code)
        except Exception:
            issues.append('calendar_check_failed')
    result['scheduler'] = _task_summary(root, task_provider)
    if not result['scheduler']['readable']:
        issues.append('task_state_unavailable')
    elif result['scheduler']['plan_state'] == 'inconsistent':
        issues.append('task_plan_inconsistent')
    if any(r['state'] == 'Running' for r in result['scheduler']['tasks']):
        issues.append('task_running')
    result['issues'] = [{'code': code, 'severity': ISSUES[code][0], 'next_action': ISSUES[code][1]}
                        for code in dict.fromkeys(issues)]
    result['status'] = 'attention' if issues else 'local_checks_clear'
    result['ok'] = not any(i['severity'] == 'critical' for i in result['issues'])
    result['backup_verified'] = False
    return result


def recovery_plan(root, **kwargs):
    report = status_report(root, **kwargs)
    # This is a PLAN, never an automatic repair. Readback can be used by human or AI.
    return {'schema_version': 1, 'tool_version': VERSION, 'operation': 'recovery-plan',
            'ok': True, 'needs_attention': bool(report['issues']),
            'actions': [{'issue': i['code'], 'instruction': i['next_action'],
                         'automatic_repair_available': False} for i in report['issues']],
            'current': report, 'changes_applied': False, 'ledger_restored': False,
            'requires_fresh_check_before_any_write': True,
            'forbidden': ['recreate_ledger', 'erase_uncertain_intent', 'restore_old_order_database',
                          'delete_maintenance_marker', 'kill_QMT', 'manual_resubmit', 'auto_enable']}


def export_support(root, output, *, confirmed=False, **kwargs):
    if not confirmed:
        raise MaintenanceError('explicit_export_confirmation_required')
    root, output = Path(root).resolve(), Path(output).absolute()
    # Refuse existing destinations including dangling symlinks. ZIP stays outside
    # private installation/state/QMT directories and is never automatically uploaded.
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise MaintenanceError('support_output_unavailable')
    resolved = output.resolve()
    protected = [root]
    try:
        config = validate_config(read_local_json(root/'config.json'))
        protected += [Path(config[k]).resolve() for k in ('state_dir','control_dir','qmt_userdata')]
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        pass
    if any(resolved == p or p in resolved.parents for p in protected):
        raise MaintenanceError('export_outside_private_directories_required')
    report = status_report(root, **kwargs)
    body = (json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    readme = ('本包只包含结构化状态摘要；不是备份，不可用于恢复委托账本。\n'
              '不包含账号、Webhook、证券/委托标识、私有路径、原始日志、消息正文或数据库。\n'
              '包含组件版本、任务状态、失败/未确认记录数量和时间；请在分享前本地审阅。\n'
              '程序没有上传本包。\n').encode('utf-8')
    content = {'status.json': body, '说明.txt': readme}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    content['MANIFEST.json'] = json.dumps(manifest, sort_keys=True).encode('ascii')
    # Exclusive creation avoids overwrites; remove only the output we created on error.
    created = False
    try:
        with output.open('xb') as stream:
            created = True
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in content.items():
                    archive.writestr(name, data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {'schema_version': 1, 'ok': True, 'code': 'support_exported_locally',
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'file_count': len(content),
            'uploaded': False, 'is_database_backup': False, 'account_connected': False,
            'message_sent': False, 'submission_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local maintenance only; no trading, repair or upload')
    parser.add_argument('operation', choices=['status', 'recovery-plan', 'export-support'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--confirm-export', action='store_true')
    args = parser.parse_args(argv)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if args.operation == 'export-support':
                if args.output is None:
                    raise MaintenanceError('support_output_required')
                result = export_support(args.root, args.output, confirmed=args.confirm_export)
            else:
                if args.output or args.confirm_export:
                    raise MaintenanceError('export_parameters_only_for_export')
                result = (status_report if args.operation == 'status' else recovery_plan)(args.root)
    except Exception as exc:
        result = {'ok': False, 'code': str(exc) if isinstance(exc, MaintenanceError) else 'maintenance_operation_failed',
                  'account_connected': False, 'message_sent': False, 'submission_calls': 0,
                  'changes_applied': False, 'uploaded': False}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
