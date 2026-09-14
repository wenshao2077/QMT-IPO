"""Verify an unexecuted source ZIP with strict sizes, paths and complete hashes.

Checks consistency, NOT publisher identity. A separately obtained expected ZIP
hash can bind an artifact; no signature key is generated or trusted from the ZIP.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import os
import zipfile

MAX_FILES = 512
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_MEMBER = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
METADATA = {'DELIVERY_MANIFEST.json', 'SOURCE_IDENTITY.json', 'COMPONENTS.json'}
ANCILLARY = {'.gitattributes', '.gitignore', '.github/workflows/offline.yml', '.github/workflows/delivery-materials.yml',
             '.github/workflows/delivery-entry-smoke.yml', '.github/workflows/runtime-smoke.yml'}
REQUIRED = {'app.py', 'market_calendar.py', 'tasks.ps1', 'panel_tasks.ps1'}


class PackageError(ValueError):
    """Stable, non-sensitive rejection code."""


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PackageError('duplicate_json_key')
        value[key] = item
    return value


def _json(data):
    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_object)
    except (UnicodeError, ValueError) as exc:
        raise PackageError('package_metadata_invalid') from exc
    if not isinstance(value, dict):
        raise PackageError('package_metadata_invalid')
    return value


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or any(c in name for c in ':<>\"|?*') or any(ord(c) < 32 for c in name):
        raise PackageError('unsafe_package_path')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(x in ('', '.', '..') for x in name.split('/')):
        raise PackageError('unsafe_package_path')
    for part in p.parts:
        if part.endswith((' ', '.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part):
            raise PackageError('unsafe_windows_path')
    return p


def verify_zip(path, expected_sha256=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise PackageError('archive_missing_or_too_large')
    if expected_sha256 is not None and not re.fullmatch('[a-fA-F0-9]{64}', expected_sha256):
        raise PackageError('expected_hash_invalid')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_sha256 is not None and expected_sha256.lower() != digest:
        raise PackageError('archive_hash_mismatch')
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= MAX_FILES or sum(x.file_size for x in members) > MAX_TOTAL:
                raise PackageError('archive_size_limit')
            contents, roots, seen = {}, set(), set()
            for member in members:
                p = safe_name(member.filename)
                if len(p.parts) < 2:
                    raise PackageError('package_root_missing')
                roots.add(p.parts[0])
                mode = member.external_attr >> 16
                if member.is_dir() or member.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise PackageError('non_regular_archive_member')
                if member.file_size > MAX_MEMBER or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise PackageError('member_size_or_compression_limit')
                key = member.filename.casefold()
                if key in seen:
                    raise PackageError('duplicate_archive_path')
                seen.add(key)
                with archive.open(member) as stream:
                    data = stream.read(MAX_MEMBER + 1)
                if len(data) != member.file_size or len(data) > MAX_MEMBER:
                    raise PackageError('member_size_mismatch')
                contents['/'.join(p.parts[1:])] = data
            if len(roots) != 1:
                raise PackageError('multiple_package_roots')
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
        raise PackageError('archive_unreadable') from exc
    checked = verify_contents(contents, roots)
    return checked | {'code': 'source_zip_integrity_verified', 'sha256': digest,
                      'expected_hash_matched': expected_sha256 is not None}


def verify_directory(path):
    """Validate the entire extracted source tree; only Git administration is excluded."""
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise PackageError('source_directory_missing_or_linked')
    contents, seen = {}, set()
    for entry in root.rglob('*'):
        name = entry.relative_to(root).as_posix()
        if name == '.git' or name.startswith('.git/'):
            continue
        safe_name(name)
        if entry.is_symlink() or (os.name == 'nt' and entry.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise PackageError('linked_source_entry')
        if entry.is_dir():
            continue
        if not entry.is_file() or entry.stat().st_size > MAX_MEMBER:
            raise PackageError('member_size_or_compression_limit')
        key = name.casefold()
        if key in seen:
            raise PackageError('duplicate_archive_path')
        seen.add(key)
        contents[name] = entry.read_bytes()
        if len(contents) > MAX_FILES or sum(map(len, contents.values())) > MAX_TOTAL:
            raise PackageError('archive_size_limit')
    return verify_contents(contents) | {'code': 'source_directory_integrity_verified'}


def verify_contents(contents, roots=None):
    if not METADATA <= contents.keys():
        raise PackageError('metadata_missing')
    manifest = _json(contents['DELIVERY_MANIFEST.json'])
    identity = _json(contents['SOURCE_IDENTITY.json'])
    components = _json(contents['COMPONENTS.json'])
    files = manifest.get('files')
    if manifest.get('schema_version') != 1 or not isinstance(files, dict) or not REQUIRED <= files.keys():
        raise PackageError('manifest_invalid')
    if identity.get('manifest_sha256') != hashlib.sha256(contents['DELIVERY_MANIFEST.json']).hexdigest():
        raise PackageError('manifest_identity_mismatch')
    version = identity.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.]+)?', version):
        raise PackageError('version_invalid')
    if (roots is not None and roots != {'QMT-IPO-'+version+'-source'}) or manifest.get('version', version) != version:
        raise PackageError('version_identity_mismatch')
    if components.get('version') != version or components.get('artifact_sha256') is not None:
        raise PackageError('component_identity_mismatch')
    expected = set(files) | METADATA | (ANCILLARY & contents.keys())
    if contents.keys() != expected:
        raise PackageError('unlisted_or_missing_file')
    from installer import destination
    destinations = set()
    for name, checksum in files.items():
        safe_name(name)
        if name in METADATA:
            raise PackageError('recursive_manifest_identity')
        try:
            dest = destination(name).as_posix().casefold()
        except ValueError as exc:
            raise PackageError('private_or_unapproved_payload') from exc
        if dest in destinations:
            raise PackageError('duplicate_install_destination')
        destinations.add(dest)
        if not isinstance(checksum, str) or hashlib.sha256(contents[name]).hexdigest() != checksum:
            raise PackageError('payload_hash_mismatch')
    if identity.get('components_sha256') != hashlib.sha256(contents['COMPONENTS.json']).hexdigest():
        raise PackageError('components_hash_mismatch')
    ancillary_hashes = identity.get('ancillary_sha256')
    if not isinstance(ancillary_hashes, dict) or set(ancillary_hashes) != (ANCILLARY & contents.keys()):
        raise PackageError('ancillary_identity_mismatch')
    if any(hashlib.sha256(contents[n]).hexdigest() != h for n, h in ancillary_hashes.items()):
        raise PackageError('ancillary_hash_mismatch')
    return {'ok': True, 'version': version,
            'file_count': len(contents), 'payload_files': len(files),
            'publisher_authenticity_verified': False, 'code_executed': False,
            'account_connected': False, 'message_sent': False, 'submission_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify ZIP without executing its scripts')
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args(argv)
    try:
        result = verify_zip(args.zip, args.expected_sha256)
    except Exception as exc:
        result = {'ok': False, 'code': str(exc) if isinstance(exc, PackageError) else 'archive_verification_failed',
                  'code_executed': False, 'publisher_authenticity_verified': False}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
