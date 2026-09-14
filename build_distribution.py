"""Build the private Windows offline delivery from verified public materials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import zipfile

from build_release import source_contents
from offline_dependencies import verify_wheels
from package_verify import safe_name
from release_info import VERSION


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def tree_manifest(root):
    return {p.relative_to(root).as_posix(): {'sha256': digest(p), 'size': p.stat().st_size}
            for p in sorted(root.rglob('*')) if p.is_file()}


def build(source, materials, output):
    source, materials, output = map(lambda p: Path(p).resolve(), (source, materials, output))
    if output.exists() or source in output.parents or materials in output.parents:
        raise ValueError('output_must_be_new_and_outside_inputs')
    provenance = json.loads((materials/'provenance.json').read_text(encoding='utf-8'))
    records = provenance['records']
    for row in records:
        safe_name(row['filename'])
        path = materials/row['filename']
        if path.is_symlink() or not path.is_file() or digest(path) != row['sha256']:
            raise ValueError('material_hash_mismatch')
    runtimes = [r for r in records if r['kind'] == 'runtime']
    lock = json.loads((source/'dependencies.lock.json').read_text(encoding='utf-8'))
    if len(runtimes) != 1 or runtimes[0]['sha256'] != lock['runtime']['sha256']:
        raise ValueError('runtime_not_pinned')
    with tempfile.TemporaryDirectory(prefix='qmt-dist-build-') as td:
        root = Path(td)/('QMT-IPO-'+VERSION+'-windows-offline')
        root.mkdir()
        contents = source_contents(source)
        for name, data in contents.items():
            path = root/'payload'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name in ('setup.ps1', 'verify.ps1'):
            shutil.copyfile(source/'delivery'/name, root/name)
        # PS5.1 receives the UTF-8 body explicitly (no BOM required).
        (root/'开始安装.cmd').write_bytes(b'@echo off\r\ncd /d "%~dp0"\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"\r\n')
        shutil.copyfile(source/'delivery/start.ps1', root/'start.ps1')
        shutil.copyfile(source/'docs/FINAL_DELIVERY.md', root/'使用说明.md')
        wheels = root/'packages/wheels'
        wheels.mkdir(parents=True)
        for row in lock['wheels']:
            shutil.copyfile(materials/row['filename'], wheels/row['filename'])
        verify_wheels(wheels, source)
        runtime_root = root/'runtime'
        runtime_root.mkdir()
        with tarfile.open(materials/runtimes[0]['filename'], 'r:gz') as tar:
            total = 0
            for member in tar.getmembers():
                name = member.name.rstrip('/')
                safe_name(name)
                if not name.startswith('python/') and name != 'python':
                    raise ValueError('unexpected_runtime_root')
                if not (member.isdir() or member.isfile()):
                    raise ValueError('non_regular_runtime_member')
                total += member.size
                if total > 600*1024*1024:
                    raise ValueError('runtime_size_limit')
                target = runtime_root/name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as stream, target.open('xb') as dest:
                        shutil.copyfileobj(stream, dest)
        write_json(root/'release/runtime-manifest.json',
                   dict(python_version=lock['runtime']['version'], target='x86_64-pc-windows-msvc',
                        files=tree_manifest(runtime_root/'python')))
        write_json(root/'release/version.json', dict(version=VERSION, python_bundled=True,
                   offline_dependencies_bundled=True, publisher_signature_included=False,
                   production_acceptance=False, distribution_scope='private_owner_delivery'))
        write_json(root/'release/provenance.json', provenance)
        write_json(root/'release/manifest.json', dict(schema_version=3, version=VERSION,
                   artifact_kind='windows_offline_distribution', files=tree_manifest(root)))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    info = zipfile.ZipInfo(root.name+'/'+path.relative_to(root).as_posix(), (2026,9,14,0,0,0))
                    info.create_system=3
                    info.external_attr=0o100644<<16
                    z.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    return dict(ok=True, version=VERSION, sha256=digest(output), size=output.stat().st_size,
                production_acceptance=False)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).parent)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.source,args.materials,args.output)))
