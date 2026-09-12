"""Bound hot files/backups; preserve every order ledger and audit receipt.

Old receipts are losslessly archived, not discarded. Archives require explicit
operator capacity planning: automatically deleting the only audit copy is unsafe.
"""
from datetime import date, timedelta
import hashlib
from pathlib import Path
import re
import sqlite3
import zipfile

from runtime import atomic_json

_RECEIPT = re.compile(r'(cycle|notify|monitor|backup)-[a-f0-9]{32}\.json\Z')
_BACKUP = re.compile(r'\d{4}-\d{2}-\d{2}-[a-f0-9]{8}\.sqlite3\Z')


def maintain(config, today):
    today = date.fromisoformat(str(today))
    root = Path(config['control_dir'])
    hot_days = config.get('receipt_hot_days', 30)
    keep = config.get('backup_keep', 30)
    if type(hot_days) is not int or not 7 <= hot_days <= 366 or type(keep) is not int or not 2 <= keep <= 365:
        raise ValueError('Invalid retention settings')
    archived = 0
    runs = root/'runs'
    if runs.exists():
        for folder in sorted(runs.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            try:
                if date.fromisoformat(folder.name) >= today-timedelta(days=hot_days):
                    continue
            except ValueError:
                continue
            files = [p for p in folder.iterdir() if _RECEIPT.fullmatch(p.name) and p.is_file() and not p.is_symlink()]
            if not files:
                continue
            # Content-derived archive names permit safe re-runs after interruption.
            signature = hashlib.sha256()
            for p in sorted(files):
                signature.update(p.name.encode()); signature.update(hashlib.sha256(p.read_bytes()).digest())
            target = root/'archive'/'runs'/folder.name/(signature.hexdigest()[:24]+'.zip')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temp = target.with_suffix('.partial')
                with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as z:
                    for p in files:
                        z.writestr(p.name, p.read_bytes())
                temp.replace(target)
            # Verify EACH source against persisted archive before removing hot copy.
            with zipfile.ZipFile(target) as z:
                for p in files:
                    if z.read(p.name) != p.read_bytes():
                        raise RuntimeError('Archive verification failed; originals retained')
            for p in files:
                p.unlink(); archived += 1
            if not any(folder.iterdir()):
                folder.rmdir()
    backups = sorted((p for p in (root/'backups').glob('*.sqlite3')
                      if _BACKUP.fullmatch(p.name) and not p.is_symlink()), reverse=True)
    healthy = []
    for p in backups:
        try:
            conn = sqlite3.connect(p.resolve().as_uri()+'?mode=ro', uri=True)
            try:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok':
                    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if {'intents', 'outbox'} <= tables:
                        healthy.append(p)
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    removed = 0
    if len(healthy) >= keep:
        boundary = healthy[keep-1].name
        for p in backups:
            if p.name < boundary:
                p.unlink(); removed += 1
    result = {'archived_receipts': archived, 'removed_redundant_backups': removed,
              'order_ledger_touched': False, 'audit_deleted': False}
    atomic_json(root/'retention-latest.json', result)
    return result


def archive_size_warning(config):
    root = Path(config['control_dir'])/'archive'
    limit_mb = config.get('archive_warn_mb', 1024)
    size = sum(p.stat().st_size for p in root.rglob('*.zip') if p.is_file() and not p.is_symlink()) if root.exists() else 0
    return size > limit_mb*1024*1024
