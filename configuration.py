"""First-use configuration only. No account connection, notification or activation.

Used by the local wizard. Refuses previously authorized/used installations and
holds the same Windows mutex as panel_tasks.ps1 while checking and saving.
"""
from contextlib import contextmanager, ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from runtime import atomic_json, read_json, single_instance, validate_config
from support import wecom_sender

TASK_NAMES = ('QmtIPO3-Cycle', 'QmtIPO3-Notify', 'QmtIPO3-Monitor', 'QmtIPO3-Backup')


class ConfigurationError(ValueError):
    """Stable, non-sensitive error code; never embeds configuration values."""


@contextmanager
def control_mutex():
    if sys.platform != 'win32':
        raise ConfigurationError('windows_required')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, 'Local\\QmtIpoPanelControl')
    if not handle:
        raise ConfigurationError('control_lock_unavailable')
    locked = False
    try:
        locked = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        if not locked:
            raise ConfigurationError('another_control_operation_running')
        yield
    finally:
        if locked:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _snapshot(root):
    from panel_backend import WindowsController
    return WindowsController(root).call('Snapshot')


def require_disabled_tasks(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('ok') is not True:
        raise ConfigurationError('task_state_unavailable')
    if snapshot.get('execution_enabled') is not False:
        raise ConfigurationError('execution_must_be_disabled')
    rows = snapshot.get('tasks')
    if not isinstance(rows, list):
        raise ConfigurationError('task_state_unavailable')
    tasks = {row.get('name'): row for row in rows if isinstance(row, dict)}
    if len(rows) != len(TASK_NAMES) or set(tasks) != set(TASK_NAMES):
        raise ConfigurationError('task_definitions_incomplete')
    for row in tasks.values():
        if row.get('exists') is not True or row.get('enabled') is not False:
            raise ConfigurationError('all_tasks_must_be_disabled')
        # Unknown is not equivalent to stopped. Task ownership is checked by Snapshot.
        if row.get('state') not in ('Disabled', 'Ready'):
            raise ConfigurationError('task_not_quiescent')


def _first_use(root, config):
    if config['enable_execution'] is not False:
        raise ConfigurationError('execution_must_be_disabled')
    if (root/'maintenance.json').exists():
        raise ConfigurationError('maintenance_in_progress')
    session = root/'new-install.json'
    if session.exists() and read_json(session).get('stage') != 'complete':
        raise ConfigurationError('finish_installation_first')
    control = Path(config['control_dir'])
    if (control/'panel-authorization.json').exists() or any((control/'daily').glob('*-live.json')):
        raise ConfigurationError('used_installation_requires_reviewed_account_change')
    ledger = Path(config['state_dir'])/'state.sqlite3'
    if not ledger.is_file() or ledger.is_symlink():
        raise ConfigurationError('ledger_missing_do_not_reinitialize')
    try:
        db = sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ConfigurationError('ledger_integrity_failed')
            for table in ('intents', 'outbox'):
                if db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:
                    raise ConfigurationError('used_installation_requires_reviewed_account_change')
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise ConfigurationError('ledger_integrity_failed') from exc


def _private_write(path, content):
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ConfigurationError('private_path_invalid')
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_first_use(root, *, account_id, qmt_userdata, allowed_markets, webhook=None,
                   task_snapshot=None, mutex_factory=None):
    """Save an inactive first installation. Secrets are arguments only inside this process.

    No CLI secret flags are provided. State/control/key paths are immutable here.
    Testing injects task snapshots and mutexes; production always uses Windows.
    """
    root = Path(root).resolve()
    task_snapshot = _snapshot if task_snapshot is None else task_snapshot
    mutex_factory = control_mutex if mutex_factory is None else mutex_factory
    try:
        config = validate_config(read_json(root/'config.json'))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ConfigurationError('configuration_unreadable') from exc
    # Check before creating lock files. Never create a missing state directory.
    _first_use(root, config)
    candidate = dict(config)
    if not isinstance(account_id, str) or not account_id.strip() or account_id.strip() in ('YOUR_ACCOUNT', 'CHANGE_ME'):
        raise ConfigurationError('account_required')
    if any(c in account_id for c in ('\r', '\n', '\x00')):
        raise ConfigurationError('account_invalid')
    qmt = Path(qmt_userdata)
    if not qmt.is_absolute() or not qmt.is_dir() or qmt.name.lower() != 'userdata_mini':
        raise ConfigurationError('mini_qmt_userdata_missing')
    candidate.update(account_id=account_id.strip(), qmt_userdata=str(qmt.resolve()),
                     allowed_markets=list(allowed_markets), enable_execution=False)
    try:
        validate_config(candidate)
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigurationError('configuration_invalid') from exc
    hook_path = Path(config['webhook_file'])
    if not hook_path.is_file() or hook_path.is_symlink() or (root/'config.json').is_symlink():
        raise ConfigurationError('private_path_invalid')
    try:
        old_hook = hook_path.read_bytes()
        hook_text = old_hook.decode('utf-8-sig').strip() if webhook is None else webhook.strip()
        wecom_sender(hook_text)  # Syntax validation only; the returned callable is never used.
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        raise ConfigurationError('webhook_missing_or_invalid') from exc
    with ExitStack() as stack:
        stack.enter_context(mutex_factory())
        for path in (root/'install.lock', Path(config['state_dir'])/'instance.lock',
                     Path(config['control_dir'])/'monitor.lock'):
            stack.enter_context(single_instance(path))
        current = read_json(root/'config.json')
        if current != config or hook_path.read_bytes() != old_hook:
            raise ConfigurationError('configuration_changed_retry')
        _first_use(root, current)
        require_disabled_tasks(task_snapshot(root))
        before = (root/'config.json').read_bytes()
        new_hook = hook_text.encode('utf-8') if webhook is not None else old_hook
        new_config = json.dumps(candidate, ensure_ascii=False, indent=2).encode('utf-8')
        # Persist a failure marker before the first private write. On interruption,
        # diagnostics/activation refuse until reviewed recovery, not silent guessing.
        marker = Path(config['control_dir'])/'configuration-pending.json'
        if marker.exists():
            raise ConfigurationError('configuration_recovery_required')
        atomic_json(marker, {'schema_version': 1, 'status': 'writing',
                            'before_config_sha256': hashlib.sha256(before).hexdigest()})
        try:
            if new_hook != old_hook:
                _private_write(hook_path, new_hook)
            _private_write(root/'config.json', new_config)
        except Exception:
            # A failed restoration leaves the marker in place and execution blocked.
            _private_write(hook_path, old_hook)
            _private_write(root/'config.json', before)
            marker.unlink()
            raise ConfigurationError('configuration_write_failed')
        marker.unlink()
    return {'schema_version': 1, 'ok': True, 'code': 'configured_disabled',
            'execution_enabled': False, 'account_connected': False,
            'message_sent': False, 'submission_calls': 0,
            'next_action': 'doctor_then_separately_authorized_readonly_check'}
