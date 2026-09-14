"""Read-only deployment operations, shared by the human and AI setup.ps1 entry.

No live/probe/send/enable operation exists here. Configure is a separate local GUI.
"""
import argparse
from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path

from environment_check import environment_report
from release_info import VERSION
from runtime import read_json


def installation_plan(root, source):
    root, source = Path(root).resolve(), Path(source).resolve()
    if root == source or source in root.parents or root in source.parents:
        return {'ok': False, 'code': 'source_and_installation_must_be_separate', 'recommended_operation': None}
    if not root.exists() or not any(root.iterdir()):
        operation, code = 'New', 'new_empty_destination'
    elif (root/'new-install.json').is_file():
        from install_journal import load
        state = read_json(root/'new-install.json')
        if state.get('schema_version') != 1 or state.get('root') != str(root):
            raise ValueError('new_install_identity_mismatch')
        if state.get('stage') == 'complete':
            identity = read_json(root/'installed-source.json')
            checksum = hashlib.sha256((source/'DELIVERY_MANIFEST.json').read_bytes()).hexdigest()
            operation, code = ('Doctor','already_installed') if identity.get('manifest_sha256') == checksum else ('Upgrade','different_reviewed_package')
        else:
            load(source, root)  # Interrupted sessions must use the exact original package.
            operation, code = 'ResumeNew','new_install_interrupted'
    elif (root/'config.json').is_file():
        operation, code = 'Upgrade', 'existing_installation_requires_reviewed_upgrade'
    else:
        operation, code = None, 'unknown_nonempty_destination'
    return {'ok': operation is not None, 'code': code, 'recommended_operation': operation,
            'will_enable_execution': False, 'will_recreate_existing_ledger': False,
            'requires_python': '3.11 x64', 'changes_applied': False}


def doctor(root, environment_provider=None):
    environment_provider = environment_report if environment_provider is None else environment_provider
    environment = environment_provider()
    result = {'ok': environment['ok'], 'code': 'local_checks_only', 'environment': environment,
              'account_connected': False, 'message_sent': False, 'submission_calls': 0,
              'account_permissions': 'not_verified', 'broker_acceptance': 'not_verified'}
    if root is None:
        result['next_action'] = 'install_new_or_resume_explicitly'
        return result
    root = Path(root).resolve()
    if (root/'maintenance.json').exists():
        return result | {'ok': False, 'code': 'source_maintenance_pending',
                         'next_action': 'RecoveryPlan_and_review_saved_maintenance'}
    if not (root/'config.json').is_file():
        return result | {'ok': False, 'code': 'configuration_missing', 'next_action': 'plan'}
    from diagnostics import configuration_report
    config = read_json(root/'config.json')
    checks = configuration_report(config, check_environment=False)
    result.update(configuration=checks, ok=environment['ok'] and checks['ok'],
                  next_action='separately_authorized_readonly_check' if environment['ok'] and checks['ok'] else 'resolve_local_issues')
    receipt = root/'new-install.json'
    if receipt.exists():
        stage = read_json(receipt).get('stage')
        result['install_stage'] = stage
        if stage != 'complete':
            result.update(ok=False, code='installation_incomplete', next_action='ResumeNew_or_manual_review')
    if environment['system'] == 'win32':
        from panel_backend import WindowsController
        snapshot = WindowsController(root).call('Snapshot')
        # Deliberately do not return account_tail, full QMT path or any key material.
        rows = snapshot.get('tasks', []) if snapshot.get('ok') else []
        result['tasks'] = [{'name': r.get('name'), 'exists': r.get('exists'),
                            'enabled': r.get('enabled'), 'state': r.get('state')} for r in rows]
        result['tasks_readable'] = bool(snapshot.get('ok'))
        expected = {'QmtIPO3-Cycle','QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup'}
        if not snapshot.get('ok') or len(rows)!=4 or {r.get('name') for r in rows}!=expected or not all(r.get('exists') for r in rows):
            result.update(ok=False, code='task_definitions_unavailable', next_action='review_installation')
        else:
            from panel_backend import plan_state
            state, _ = plan_state(snapshot)
            result['execution_plan_state'] = state
            if state in ('unknown', 'inconsistent'):
                result.update(ok=False, code='execution_plan_inconsistent', next_action='review_configuration_and_owned_tasks')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline deployment checks. Never enables or connects accounts.')
    parser.add_argument('operation', choices=['plan', 'doctor', 'verify-package'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    try:
        with redirect_stdout(io.StringIO()):
            if args.operation == 'plan':
                if args.root is None:
                    raise ValueError('root_required')
                result = installation_plan(args.root, args.source)
            elif args.operation == 'doctor':
                result = doctor(args.root)
            else:
                from package_verify import verify_directory
                result = verify_directory(args.source)
    except Exception as exc:
        from install_journal import InstallStateError
        result = {'ok': False, 'code': str(exc) if isinstance(exc, InstallStateError) else 'deployment_check_failed',
                  'error_type': type(exc).__name__, 'next_action': 'manual_review_no_ledger_recreation'}
    result = {'schema_version': 1, 'version': VERSION, 'operation': args.operation,
              'account_connected': False, 'message_sent': False, 'submission_calls': 0, **result}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
