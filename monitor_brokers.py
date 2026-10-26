"""Read-only fleet check. Explicit private manifest; no send, submit, enable or restart.

Local and SSH readers return only constructed summaries, never remote raw logs.
Designed as the independent monitor consumer for both broker instances.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from runtime import read_json


def monitor(manifest, runner=subprocess.run):
    entries = read_json(manifest)
    if not isinstance(entries, list) or not entries:
        raise ValueError('explicit_broker_manifest_required')
    labels = [row['id'] for row in entries]
    if len(set(labels)) != len(labels):
        raise ValueError('duplicate_broker_identity')
    results = []
    for row in entries:
        if row.get('location') == 'local':
            root = Path(row['root']).resolve()
            command = [str(root/'.venv/Scripts/python.exe'), '-B', str(root/'code/instance_monitor.py'), '--root', str(root)]
        elif row.get('location') == 'ssh':
            # Only a named SSH alias, not arbitrary SSH flags/host shell strings.
            import re
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', row['host']):
                raise ValueError('invalid_ssh_alias')
            root = row['root']
            if not re.fullmatch(r'[A-Za-z]:[\\/][\w /\\.-]+', root):
                raise ValueError('invalid_remote_installation_root')
            command = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',row['host'],
                       'pwsh.exe -NoProfile -File "'+root+'/probe_qmt_trade.ps1" -InstallRoot "'+root+'"']
        else:
            raise ValueError('unknown_broker_location')
        try:
            value = runner(command, capture_output=True, text=True, encoding='utf-8', timeout=35, check=False)
            result = json.loads(value.stdout.strip())
            if not isinstance(result, dict) or value.returncode not in (0,2):
                raise ValueError('invalid_monitor_response')
            if row.get('location') == 'ssh':
                # Consume the already deployed Northeast probe, without an upgrade.
                result={'ok':result.get('healthy') is True and value.returncode == 0, 'connection':result,
                        'coverage':'existing_independent_connection_probe'}
            elif value.returncode != 0:
                result={**result,'ok':False}
            results.append({**result,'id': row['id']})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            results.append({'id': row['id'], 'ok': False, 'code': 'monitor_unavailable'})
    return {'ok': all(r.get('ok') is True for r in results), 'brokers': results,
            'read_only': True, 'submission_calls': 0, 'message_sent': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True)
    a = p.parse_args()
    result = monitor(a.manifest)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result['ok'] else 2)
