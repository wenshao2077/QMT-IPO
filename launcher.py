"""Bounded, hidden worker launcher; no shell and no raw SDK stdout logging."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from runtime import atomic_json, read_json, single_instance

ACTIONS = ('cycle', 'notify', 'monitor', 'backup')


def command(root, action):
    root = Path(root).resolve()
    if action not in ACTIONS:
        raise ValueError('Unknown task action')
    args = [str(root/'.venv/Scripts/python.exe'), '-B', str(root/'code/app.py'),
            action, '--config', str(root/'config.json')]
    if action == 'cycle':
        args += ['--live', '--send']
    elif action in ('notify', 'monitor'):
        args += ['--send']
    return args


def run(root, action, runner=subprocess.run):
    root = Path(root).resolve()
    config = read_json(root/'config.json')
    control = Path(config['control_dir'])
    with single_instance(control/(action+'-launcher.lock')):
        if (root/'maintenance.json').exists():
            return {'status': 'maintenance_skip', 'exit_code': 0}
        if action == 'cycle' and config.get('enable_execution') is not True:
            return {'status': 'not_activated', 'exit_code': 0}
        start = datetime.now(timezone.utc)
        try:
            result = runner(command(root, action), cwd=root, timeout=180,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            receipt = {'status': 'finished', 'exit_code': result.returncode}
        except subprocess.TimeoutExpired:
            # subprocess.run terminates its own worker only, NEVER miniQMT.
            receipt = {'status': 'worker_timeout_reconcile_before_any_retry', 'exit_code': 124}
        except Exception as exc:
            receipt = {'status': 'launch_failed', 'error_type': type(exc).__name__, 'exit_code': 1}
        receipt.update(action=action, started_at=start.isoformat(), finished_at=datetime.now(timezone.utc).isoformat())
        # Fixed-size latest logs: detailed business history remains in app receipts.
        atomic_json(control/'launcher'/('latest-'+action+'.json'), receipt)
        return receipt


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=ACTIONS)
    p.add_argument('--root', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        return run(args.root, args.action)['exit_code']
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
