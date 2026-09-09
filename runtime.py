"""IO/locking only. Importing has no external side effects."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp,path)


@contextmanager
def single_instance(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def backup(source,target):
    source,target=Path(source),Path(target)
    if not source.is_file() or target.exists():
        raise ValueError('Backup source missing or immutable target already exists')
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.partial')
    src=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    dst=sqlite3.connect(tmp)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
    finally:
        dst.close(); src.close()
    os.replace(tmp,target)


def validate_config(config):
    for key in ('account_id','qmt_userdata','state_dir','control_dir','webhook_file'):
        if not isinstance(config.get(key),str) or not config[key].strip():
            raise ValueError('Missing required setting: '+key)
    for key in ('qmt_userdata','state_dir','control_dir','webhook_file'):
        if not Path(config[key]).is_absolute():
            raise ValueError('Absolute path required: '+key)
    if type(config.get('enable_execution')) is not bool:
        raise ValueError('Execution setting must be explicit boolean')
    if not config.get('allowed_markets') or set(config['allowed_markets'])-{'SH','SZ','KCB'}:
        raise ValueError('Invalid market settings')
    for key in ('state_dir','control_dir'):
        path=Path(config[key]).resolve(); qmt=Path(config['qmt_userdata']).resolve()
        if path == qmt or qmt in path.parents:
            raise ValueError('Runtime must be separate from QMT userdata')
    if Path(config['state_dir']).resolve() == Path(config['control_dir']).resolve():
        raise ValueError('Control receipts must be separate from shared order ledger')
    return config
