"""Prepare an inactive installation-bound QMT model. Never loads QMT or enables it."""
import argparse
from contextlib import ExitStack
import hashlib
from pathlib import Path
import json
from runtime import read_json, atomic_json, validate_config, single_instance
from configuration import require_disabled_tasks, control_mutex, _snapshot
from installer import check_ledger


def prepare(root, *, confirmed=False, task_provider=_snapshot):
    if not confirmed:
        raise ValueError('explicit_preparation_confirmation_required')
    root = Path(root).resolve()
    config = validate_config(read_json(root/'config.json'))
    if config['enable_execution'] is not False or (root/'maintenance.json').exists():
        raise ValueError('paused_complete_installation_required')
    if (root/'new-install.json').exists() and read_json(root/'new-install.json').get('stage') != 'complete':
        raise ValueError('finish_installation_first')
    check_ledger(config)
    require_disabled_tasks(task_provider(root), config)
    with ExitStack() as stack:
        stack.enter_context(control_mutex(root))
        for path in (root/'install.lock',Path(config['state_dir'])/'instance.lock',Path(config['control_dir'])/'monitor.lock'):
            stack.enter_context(single_instance(path))
        require_disabled_tasks(task_provider(root), config)
        if config != read_json(root/'config.json'):
            raise ValueError('configuration_changed')
        check_ledger(config)
        candidate = dict(config, transport='native', native_bridge_dir=str(root/'native'))
        validate_config(candidate)
        for name in ('requests','responses','claims'):
            path = root/'native'/name
            if path.is_symlink():
                raise ValueError('native_directory_link_refused')
            path.mkdir(parents=True, exist_ok=True)
        loader = ('# coding: utf-8\n# Dedicated IPO model; init never submits.\n'
                  'QMT_IPO_ROOT = '+repr(str(root))+'\n'
                  '_ipo_path = QMT_IPO_ROOT + "/code/native_qmt_bridge.py"\n'
                  'with open(_ipo_path, "rb") as _ipo_stream:\n'
                  '    exec(compile(_ipo_stream.read(), _ipo_path, "exec"), globals())\n')
        before=(root/'config.json').read_bytes()
        backup=Path(config['control_dir'])/'client-binding-backups'/(hashlib.sha256(before).hexdigest()+'.json')
        backup.parent.mkdir(parents=True,exist_ok=True)
        if backup.exists():
            if backup.read_bytes()!=before:raise ValueError('binding_backup_identity_mismatch')
        else:
            with backup.open('xb') as stream:stream.write(before)
        loader_path=root/'native_loader.py'
        if loader_path.is_symlink():raise ValueError('native_loader_link_refused')
        loader_path.write_text(loader, encoding='utf-8')
        atomic_json(root/'config.json', candidate)
    return {'ok': True, 'code': 'native_model_prepared_inactive', 'model_loaded': False,
            'execution_enabled': False, 'submission_calls': 0, 'message_sent': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--confirm-prepare', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, confirmed=args.confirm_prepare)))
