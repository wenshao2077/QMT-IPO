"""Maintainer command: hash the install whitelist, excluding local secrets/state."""
import hashlib
from pathlib import Path
from runtime import atomic_json
from installer import destination


def build(root):
    root=Path(root).resolve()
    files={}
    paths=list(root.iterdir())+list((root/'calendars').glob('*.json'))+list((root/'docs').glob('*.md'))
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink():continue
        name=path.relative_to(root).as_posix()
        try:destination(name)
        except ValueError:continue
        # Never install private JSON, reports/logs, a ledger or a .venv.
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json(root/'DELIVERY_MANIFEST.json',{'schema_version':1,'base_commit':'922f7b1c193b0b3e180ea09b99dc6f1d89411629','files':files})
    return files


if __name__=='__main__':build(Path(__file__).parent)
