"""Offline source installation. Does not install dependencies, schedule, connect or send.

Upgrades require an explicitly quiesced Windows host. Never reconstruct or restore
an order ledger. The caller must stop task wakeups and close the GUI first.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import uuid

from runtime import atomic_json, read_json, single_instance, validate_config
from support import Store

MANIFEST = 'DELIVERY_MANIFEST.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_root(root):
    root = Path(root).resolve()
    if any(x in str(root) for x in ('"', '\n', '\r')):
        raise ValueError('Install root contains unsupported quoting characters')
    return root


def destination(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe source path')
    if (len(p.parts) == 1 and p.suffix == '.py') or (p.parts[0] == 'calendars' and p.suffix == '.json'):
        return Path('code') / name
    if len(p.parts) == 1 and p.suffix in ('.ps1', '.vbs', '.md'):
        return Path(name)
    if name in ('requirements.txt', 'config.example.json'):
        return Path(name)
    if p.parts[0] == 'docs' and p.suffix == '.md':
        return Path(name)
    raise ValueError('File is outside the install whitelist')


def payload(source):
    source = Path(source).resolve()
    manifest = read_json(source/MANIFEST)
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Delivery manifest missing or invalid')
    result = {}
    for name, checksum in manifest['files'].items():
        dest = destination(name)
        path = source/name
        if not path.is_file() or path.is_symlink() or source not in path.resolve().parents or sha(path) != checksum:
            raise ValueError('Delivery file missing, linked or checksum mismatch: '+name)
        if dest in result:
            raise ValueError('Duplicate install destination')
        result[dest] = path
    if not {Path('code/app.py'), Path('code/market_calendar.py'), Path('panel_tasks.ps1'), Path('tasks.ps1')} <= result.keys():
        raise ValueError('Required payload files missing')
    return result


def check_ledger(config):
    path = Path(config['state_dir'])/'state.sqlite3'
    if not path.is_file():
        raise ValueError('Existing order ledger is missing; refuse creation')
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        tables = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'intents', 'outbox'} <= tables or conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Existing ledger failed integrity/schema check')
    finally:
        conn.close()
    return path


def _replace_file(source, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        raise ValueError('Refuse symlink destination')
    temporary = dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.update')
    try:
        shutil.copyfile(source, temporary)
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)


def new_install(source, root):
    root = validate_root(root)
    files = payload(source)
    if root.exists() and any(p.name != '.venv' for p in root.iterdir()):
        raise ValueError('New installation requires an empty directory (except its own .venv)')
    root.mkdir(parents=True, exist_ok=True)
    for dest, origin in files.items():
        _replace_file(origin, root/dest)
    config = {'account_id': 'YOUR_ACCOUNT',
              'qmt_userdata': str(root/'configure-miniQMT'/'userdata_mini'),
              'state_dir': str(root/'state'), 'control_dir': str(root/'runtime'),
              'webhook_file': str(root/'secure'/'webhook.txt'),
              'allowed_markets': ['SH', 'SZ'], 'enable_execution': False,
              'receipt_hot_days': 30, 'backup_keep': 30, 'archive_warn_mb': 1024}
    validate_config(config)
    (root/'secure').mkdir()
    # An empty private file is deliberately not a usable notification destination.
    (root/'secure'/'webhook.txt').write_text('', encoding='utf-8')
    (root/'runtime').mkdir(exist_ok=True)
    store = Store(root/'state'/'state.sqlite3')
    store.close()
    atomic_json(root/'config.json', config)
    atomic_json(root/'installed-source.json', {'schema_version': 1,
                'manifest_sha256': sha(Path(source)/MANIFEST), 'execution_enabled': False})
    return {'status': 'installed_disabled', 'account_configured': False, 'tasks_registered': False}


def upgrade(source, root, quiesced=False):
    if not quiesced:
        raise ValueError('First disable owned task triggers, wait for workers and close GUI; --quiesced required')
    root = validate_root(root)
    files = payload(source)
    config_path = root/'config.json'
    config_bytes = config_path.read_bytes()
    config = validate_config(read_json(config_path))
    ledger = check_ledger(config)
    for dest in files:
        if dest.is_absolute() or root not in (root/dest).resolve().parents:
            raise ValueError('Destination escapes installation')
    rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    backup = Path(config['control_dir'])/'code-backups'/rid
    with ExitStack() as stack:
        for lock in (root/'install.lock', Path(config['state_dir'])/'instance.lock', Path(config['control_dir'])/'monitor.lock'):
            stack.enter_context(single_instance(lock))
        before_ledger = sha(ledger)
        backup.mkdir(parents=True, exist_ok=False)
        records = {}
        for dest in files:
            path = root/dest
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Unexpected installed path')
                _replace_file(path, backup/'files'/dest)
                records[dest.as_posix()] = sha(path)
            else:
                records[dest.as_posix()] = None
        report = {'schema_version': 1, 'root': str(root), 'files': records,
                  'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                  'state_dir': config['state_dir'], 'ledger_sha256_before': before_ledger,
                  'status': 'prepared', 'new_manifest_sha256': sha(Path(source)/MANIFEST)}
        atomic_json(backup/'rollback.json', report)
        replaced = []
        try:
            for dest, origin in files.items():
                _replace_file(origin, root/dest)
                replaced.append(dest)
            if config_path.read_bytes() != config_bytes or sha(ledger) != before_ledger:
                raise RuntimeError('Private configuration or ledger changed during source-only upgrade')
        except Exception:
            for dest in reversed(replaced):
                if records[dest.as_posix()] is None:
                    (root/dest).unlink(missing_ok=True)
                else:
                    _replace_file(backup/'files'/dest, root/dest)
            raise
        report['status'] = 'source_replaced_private_state_unchanged'
        atomic_json(backup/'rollback.json', report)
    return {'status': report['status'], 'rollback_id': rid, 'config_preserved': True, 'ledger_preserved': True}


def rollback(root, rollback_id, quiesced=False):
    import re
    if not quiesced or not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{8}', rollback_id):
        raise ValueError('Valid rollback ID and quiesced host required')
    root = validate_root(root)
    config = validate_config(read_json(root/'config.json'))
    ledger = check_ledger(config)
    folder = Path(config['control_dir'])/'code-backups'/rollback_id
    report = read_json(folder/'rollback.json')
    if report.get('root') != str(root) or report.get('state_dir') != config['state_dir']:
        raise ValueError('Rollback identity mismatch')
    # Validate everything before restoring any file. NEVER roll back order state.
    for name, checksum in report['files'].items():
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
            raise ValueError('Unsafe rollback path')
        logical = name[5:] if name.startswith('code/') else name
        if destination(logical).as_posix() != name:
            raise ValueError('Rollback path outside source whitelist')
        if checksum is not None and sha(folder/'files'/name) != checksum:
            raise ValueError('Rollback source checksum mismatch')
    with ExitStack() as stack:
        for lock in (root/'install.lock', Path(config['state_dir'])/'instance.lock', Path(config['control_dir'])/'monitor.lock'):
            stack.enter_context(single_instance(lock))
        before = (sha(root/'config.json'), sha(ledger))
        for name, checksum in report['files'].items():
            if checksum is None:
                (root/name).unlink(missing_ok=True)
            else:
                _replace_file(folder/'files'/name, root/name)
        if before != (sha(root/'config.json'), sha(ledger)):
            raise RuntimeError('Private state changed while rolling back source')
    return {'status': 'source_rolled_back', 'private_state_restored': False}


def main(argv=None):
    p = argparse.ArgumentParser(description='源码安装/升级/回滚，不连接QMT、不发送通知、不操作任务')
    p.add_argument('operation', choices=['new', 'upgrade', 'rollback'])
    p.add_argument('--source', type=Path)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--quiesced', action='store_true')
    p.add_argument('--rollback-id')
    args = p.parse_args(argv)
    try:
        if args.operation == 'rollback':
            result = rollback(args.root, args.rollback_id or '', args.quiesced)
        else:
            if args.source is None:
                raise ValueError('Source delivery directory required')
            result = new_install(args.source, args.root) if args.operation == 'new' else upgrade(args.source, args.root, args.quiesced)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'installation_failed', 'error_type': type(exc).__name__,
                          'remedy': '核对源码清单、停止任务触发并关闭控制台、检查原配置和账本；不得删除账本重试。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
