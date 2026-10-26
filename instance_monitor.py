"""Independent instance/account/ledger/connection check, without order side effects."""
import argparse
from pathlib import Path
import json
import sys
from runtime import read_json, validate_config
from probe_readonly import probe
from maintenance import ledger_summary
from instance_identity import task_prefix


def monitor(root):
    config = validate_config(read_json(Path(root)/'config.json'))
    connection = probe(config, connection_only=False)
    ledger = ledger_summary(Path(config['state_dir'])/'state.sqlite3')
    from panel_backend import WindowsController, plan_state
    tasks = WindowsController(root).call('Snapshot')
    state = plan_state(tasks)[0]
    from app import inspect_health
    health = inspect_health(config)
    return {'ok': connection.get('status') == 'read_only_queries_complete' and ledger['status'] == 'readable'
                  and state in ('enabled','disabled') and not health.get('issues'),
            'task_prefix': task_prefix(config), 'transport': config.get('transport','xtquant'),
            'execution_enabled': config['enable_execution'], 'connection': connection,
            'ledger': ledger, 'scheduler_state':state, 'daily_health':health,
            'submission_calls': 0, 'message_sent': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    try:
        result = monitor(a.root)
    except Exception as exc:
        result = {'ok': False, 'error_type': type(exc).__name__, 'code': 'instance_monitor_failed',
                  'submission_calls': 0, 'message_sent': False}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result['ok'] else 2)
