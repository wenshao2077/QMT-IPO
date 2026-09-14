"""Hash-locked local wheel installation. No index access, SDK import or account use."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def verify_wheels(wheelhouse, source=None):
    source = Path(source or Path(__file__).parent)
    root = Path(wheelhouse)
    lock = json.loads((source/'dependencies.lock.json').read_text(encoding='utf-8-sig'))
    if root.is_symlink() or not root.is_dir() or lock.get('schema_version') != 1:
        raise ValueError('wheelhouse_or_lock_invalid')
    rows = lock['wheels']
    expected = {r['filename'] for r in rows}
    if len(expected) != len(rows) or {p.name for p in root.iterdir()} != expected:
        raise ValueError('wheelhouse_file_set_mismatch')
    requirements = []
    for row in rows:
        path = root/row['filename']
        if path.name != row['filename'] or path.is_symlink() or not path.is_file():
            raise ValueError('wheelhouse_path_invalid')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('wheel_hash_mismatch')
        requirements.append(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}")
    if (source/'requirements-offline.txt').read_text(encoding='utf-8').splitlines() != requirements:
        raise ValueError('offline_requirements_mismatch')
    return rows


def install(wheelhouse, source=None):
    source = Path(source or Path(__file__).parent)
    verify_wheels(wheelhouse, source)
    env = os.environ.copy()
    env['PIP_CONFIG_FILE'] = os.devnull
    command = [sys.executable, '-I', '-B', '-m', 'pip', '--isolated', 'install',
               '--no-index', '--find-links', str(Path(wheelhouse).resolve()),
               '--require-hashes', '--only-binary=:all:', '--disable-pip-version-check',
               '-r', str(source/'requirements-offline.txt')]
    return subprocess.call(command, env=env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheelhouse', required=True, type=Path)
    args = parser.parse_args()
    try:
        raise SystemExit(install(args.wheelhouse))
    except (OSError, ValueError, KeyError):
        print(json.dumps({'ok': False, 'code': 'offline_dependency_validation_failed'}))
        raise SystemExit(2)
