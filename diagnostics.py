"""Offline configuration checks and an independently confirmed notification test."""
import argparse
import json
from pathlib import Path
import sqlite3

from environment_check import environment_report
from calendar_health import coverage_report
from market_calendar import CalendarService, UNKNOWN
from runtime import read_json, validate_config
from support import china_now, wecom_sender


def configuration_report(config, check_environment=True):
    issues = []
    if isinstance(config, dict) and isinstance(config.get('control_dir'), str):
        if (Path(config['control_dir'])/'configuration-pending.json').exists():
            return {'ok': False, 'issues': ['configuration_recovery_required'], 'message_sent': False}
    try:
        validate_config(config)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {'ok': False, 'issues': ['configuration_structure_or_paths'], 'message_sent': False}
    if config['account_id'].strip() in ('YOUR_ACCOUNT', 'CHANGE_ME'):
        issues.append('account_not_configured')
    qmt = Path(config['qmt_userdata'])
    if not qmt.is_dir() or qmt.name.lower() != 'userdata_mini':
        issues.append('mini_qmt_userdata_missing')
    try:
        wecom_sender(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip())
    except (OSError, ValueError):
        issues.append('webhook_missing_or_invalid')
    ledger = Path(config['state_dir'])/'state.sqlite3'
    if not ledger.is_file():
        issues.append('ledger_missing_do_not_reinitialize_on_upgrade')
    else:
        try:
            conn = sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'intents', 'outbox'} <= tables or conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    issues.append('ledger_schema_or_integrity')
            finally:
                conn.close()
        except sqlite3.Error:
            issues.append('ledger_unreadable')
    environment = environment_report() if check_environment else None
    if environment is not None:
        issues.extend(environment['issues'])
    calendar = CalendarService(config).decide(china_now().date().isoformat())
    if calendar.status == UNKNOWN:
        issues.append('calendar_unknown')
    return {'ok': not issues, 'issues': issues, 'execution_enabled': config['enable_execution'],
            'level': 'local_configuration_only', 'environment': environment,
            'webhook': '已配置（密钥不显示）' if 'webhook_missing_or_invalid' not in issues else '未就绪',
            'calendar': calendar.to_dict(),
            'calendar_coverage': coverage_report(config),
            'message_sent': False, 'account_connected': False,
            'submission_calls': 0,
            'account_permission': '未核验；配置、依赖版本和进程存在均不能证明交易接口权限'}


def test_notification(config, confirmed=False, sender_factory=wecom_sender):
    if not confirmed:
        raise ValueError('Notification test requires an explicit user send confirmation')
    sender = sender_factory(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip())
    sender('【QMT打新】用户主动点击的通知测试；未连接交易账户、未提交申购。')
    return {'ok': True, 'status': 'notification_test_sent', 'submission_calls': 0}


def main(argv=None):
    p = argparse.ArgumentParser(description='只校验配置，或经用户明确确认后测试通知')
    p.add_argument('action', choices=['check', 'test-webhook'])
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--confirm-send', action='store_true')
    args = p.parse_args(argv)
    try:
        config = read_json(args.config)
        result = configuration_report(config) if args.action == 'check' else test_notification(config, args.confirm_send)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 2
    except Exception as exc:
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__,
                          'remedy': '核对配置与私有文件；不要删除或重建既有账本。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
