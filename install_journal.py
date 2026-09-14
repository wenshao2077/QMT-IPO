"""Conservative checkpoints for NEW installations only; never recreates a ledger.

Resume may recreate/finish the private venv and retry dependencies/disabled task
registration. A partially initialized source/ledger that cannot be independently
verified is a hard stop for manual review, NOT permission to initialize it again.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from runtime import atomic_json, read_json
from release_info import VERSION

NAME = 'new-install.json'
STAGES = ('prepared', 'environment_ready', 'source_started', 'source_ready',
          'dependencies_ready', 'tasks_ready', 'complete')


class InstallStateError(ValueError):
    """Non-sensitive stable recovery code."""


def _safe_root(root):
    original = Path(root)
    if original.is_symlink() or (original.exists() and getattr(original.lstat(), 'st_file_attributes', 0) & 0x400):
        raise InstallStateError('installation_link_refused')
    return original.resolve()


def _manifest(source):
    from installer import payload
    payload(source)
    return hashlib.sha256((Path(source)/'DELIVERY_MANIFEST.json').read_bytes()).hexdigest()


def start(source, root):
    root, source = _safe_root(root), Path(source).resolve()
    if root == source or source in root.parents or root in source.parents:
        raise InstallStateError('source_and_installation_must_be_separate')
    checksum = _manifest(source)
    if root.exists() and any(root.iterdir()):
        raise InstallStateError('new_requires_empty_directory')
    root.mkdir(parents=True, exist_ok=True)
    doc = {'schema_version': 1, 'version': VERSION, 'root': str(root),
           'install_id': uuid.uuid4().hex, 'manifest_sha256': checksum,
           'stage': 'prepared', 'created_at': datetime.now(timezone.utc).isoformat()}
    atomic_json(root/NAME, doc)
    return summary(doc)


def load(source, root):
    root = _safe_root(root)
    path = root/NAME
    if not path.is_file() or path.is_symlink():
        raise InstallStateError('new_install_receipt_missing')
    try:
        doc = read_json(path)
    except (OSError, ValueError) as exc:
        raise InstallStateError('new_install_receipt_invalid') from exc
    if (not isinstance(doc, dict) or doc.get('schema_version') != 1
            or doc.get('root') != str(root) or doc.get('stage') not in STAGES
            or doc.get('manifest_sha256') != _manifest(source)):
        raise InstallStateError('new_install_identity_mismatch')
    return doc


def summary(doc):
    return {'schema_version': 1, 'ok': True, 'code': 'new_install_checkpoint',
            'stage': doc['stage'], 'version': doc['version'],
            'complete': doc['stage'] == 'complete', 'account_connected': False,
            'message_sent': False, 'submission_calls': 0}


def advance(source, root, stage):
    doc = load(source, root)
    if stage not in STAGES:
        raise InstallStateError('invalid_install_stage')
    old, new = STAGES.index(doc['stage']), STAGES.index(stage)
    if new not in (old, old+1):
        raise InstallStateError('install_stage_transition_refused')
    doc.update(stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
    atomic_json(Path(root)/NAME, doc)
    return summary(doc)


def source_may_start(source, root):
    doc = load(source, root)
    if doc['stage'] != 'source_started':
        raise InstallStateError('source_start_not_checkpointed')
    if any(p.name not in (NAME, '.venv') for p in Path(root).iterdir()):
        raise InstallStateError('source_initialization_incomplete_manual_review')
    return True


def verify_fresh(source, root):
    """Read-only evidence of a fully initialized but never-used new installation."""
    from installer import payload
    root = _safe_root(root)
    doc = load(source, root)
    if doc['stage'] == 'complete':
        raise InstallStateError('completed_installation_not_resumable')
    for name in ('code', 'secure', 'state', 'runtime'):
        path = root/name
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != root:
            raise InstallStateError('new_install_private_path_changed')
    for destination, source_file in payload(source).items():
        path = root/destination
        if (not path.is_file() or path.is_symlink()
                or path.resolve() != root/destination
                or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(source_file.read_bytes()).digest()):
            raise InstallStateError('source_initialization_incomplete_manual_review')
    try:
        config = read_json(root/'config.json')
        identity = read_json(root/'installed-source.json')
    except (OSError, ValueError) as exc:
        raise InstallStateError('source_initialization_incomplete_manual_review') from exc
    expected = {'account_id': 'YOUR_ACCOUNT', 'enable_execution': False,
                'allowed_markets': ['SH', 'SZ'], 'state_dir': str(root/'state'),
                'control_dir': str(root/'runtime'), 'webhook_file': str(root/'secure/webhook.txt'),
                'qmt_userdata': str(root/'configure-miniQMT/userdata_mini')}
    if any(config.get(k) != v for k, v in expected.items()):
        raise InstallStateError('new_install_private_configuration_changed')
    if identity.get('manifest_sha256') != doc['manifest_sha256']:
        raise InstallStateError('installed_source_identity_mismatch')
    for path in (root/'config.json', root/'secure/webhook.txt', root/'state/state.sqlite3'):
        if not path.is_file() or path.is_symlink():
            raise InstallStateError('new_install_private_file_missing_or_linked')
    if (root/'secure/webhook.txt').read_bytes():
        raise InstallStateError('new_install_private_configuration_changed')
    if ((root/'maintenance.json').exists() or (root/'runtime/configuration-pending.json').exists()
            or (root/'runtime/panel-authorization.json').exists()
            or any((root/'runtime/daily').glob('*-live.json'))):
        raise InstallStateError('installation_has_activity_do_not_resume_new')
    try:
        db = sqlite3.connect((root/'state/state.sqlite3').as_uri()+'?mode=ro', uri=True, timeout=2)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise InstallStateError('ledger_integrity_failed_do_not_reinitialize')
            if any(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('intents','outbox')):
                raise InstallStateError('installation_has_activity_do_not_resume_new')
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise InstallStateError('ledger_unreadable_do_not_reinitialize') from exc
    return summary(doc) | {'source_and_private_state_verified': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description='New installation recovery checkpoints only')
    parser.add_argument('operation', choices=['start', 'status', 'advance', 'verify-fresh', 'source-may-start'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', choices=STAGES)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'start':
            result = start(args.source, args.root)
        elif args.operation == 'advance':
            result = advance(args.source, args.root, args.stage)
        elif args.operation == 'verify-fresh':
            result = verify_fresh(args.source, args.root)
        elif args.operation == 'source-may-start':
            source_may_start(args.source, args.root)
            result = {'schema_version': 1, 'ok': True, 'code': 'empty_source_stage'}
        else:
            result = summary(load(args.source, args.root))
    except InstallStateError as exc:
        result = {'schema_version': 1, 'ok': False, 'code': str(exc),
                  'next_action': 'inspect_receipt_no_ledger_recreation'}
    except Exception as exc:
        result = {'schema_version': 1, 'ok': False, 'code': 'installation_state_unavailable',
                  'error_type': type(exc).__name__, 'next_action': 'manual_review'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
