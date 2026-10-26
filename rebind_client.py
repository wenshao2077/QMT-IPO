"""Reviewed, paused-instance IPC path change. Account/ledger/switches stay fixed."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3

from client_binding import validate_userdata, binding_report
from configuration import control_mutex, require_disabled_tasks, _snapshot
from runtime import atomic_json, read_json, single_instance, validate_config


def rebind(root, userdata, *, confirmed=False, task_provider=_snapshot, mutex_factory=None, transport=None):
    if not confirmed:
        raise ValueError('explicit_rebind_confirmation_required')
    root = Path(root).resolve()
    def require_installation_complete():
        journal = root/'new-install.json'
        if journal.exists() and read_json(journal).get('stage') != 'complete':
            raise ValueError('finish_installation_before_rebind')
    require_installation_complete()
    if (root/'maintenance.json').exists():
        raise ValueError('source_maintenance_pending')
    candidate_path = validate_userdata(validate_userdata(userdata).resolve())
    candidate_binding = binding_report(candidate_path)
    if (root/'config.json').is_symlink():
        raise ValueError('private_path_invalid')
    config = validate_config(read_json(root/'config.json'))
    if config['enable_execution'] is not False:
        raise ValueError('pause_execution_before_rebind')
    ledger = Path(config['state_dir'])/'state.sqlite3'
    if not ledger.is_file():
        raise ValueError('ledger_missing_do_not_reinitialize')
    candidate = dict(config, qmt_userdata=str(candidate_path))
    if transport is not None:
        candidate['transport'] = transport
    validate_config(candidate)
    with ExitStack() as stack:
        stack.enter_context(mutex_factory() if mutex_factory else control_mutex(root))
        for path in (root/'install.lock', Path(config['state_dir'])/'instance.lock', Path(config['control_dir'])/'monitor.lock'):
            stack.enter_context(single_instance(path))
        require_disabled_tasks(task_provider(root), config)
        require_installation_complete()
        if read_json(root/'config.json') != config or (root/'maintenance.json').exists():
            raise ValueError('configuration_changed_retry')
        db = sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('ledger_integrity_failed')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'intents', 'outbox'} <= tables:
                raise ValueError('ledger_schema_invalid')
        finally:
            db.close()
        control = Path(config['control_dir'])
        old = (root/'config.json').read_bytes()
        fingerprint = hashlib.sha256(old).hexdigest()
        backup = control/'client-binding-backups'/f'{fingerprint}.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if backup.read_bytes() != old:
                raise ValueError('binding_backup_identity_mismatch')
        else:
            with backup.open('xb') as stream:
                stream.write(old)
        atomic_json(root/'config.json', candidate)
    return {'ok': True, 'code': 'client_rebound_paused', 'client_binding': candidate_binding,
            'execution_enabled': False, 'ledger_replaced': False, 'tasks_changed': False,
            'account_connected': False, 'message_sent': False, 'submission_calls': 0}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--qmt-userdata', type=Path, required=True)
    p.add_argument('--confirm-rebind', action='store_true')
    p.add_argument('--transport', choices=['xtquant', 'native'])
    a = p.parse_args(argv)
    try:
        result = rebind(a.root, a.qmt_userdata, confirmed=a.confirm_rebind, transport=a.transport)
    except Exception as exc:
        code = str(exc)
        result = {'ok': False, 'code': code if code.isascii() and code.replace('_', '').isalnum() else 'client_rebind_failed',
                  'account_connected': False, 'message_sent': False, 'submission_calls': 0}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
