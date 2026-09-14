"""Build a deterministic REVIEW source ZIP from the checked delivery manifest.

No installation, network, SDK import or task operation. No dependency binaries are
bundled. The candidate is not a signed/publicly accepted production release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from installer import payload, MANIFEST
from release_info import VERSION, BASE_COMMIT, BASELINE_PACKAGE_SHA256, PINNED_DEPENDENCIES

ANCILLARY = ('.gitattributes', '.gitignore', '.github/workflows/offline.yml')


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError('Build outside the source/installation directory')
    if output.exists():
        raise ValueError('Refuse overwriting an existing release artifact')
    contents = source_contents(source)
    return write_archive(contents, output)


def source_contents(source):
    source = Path(source).resolve()
    payload(source)  # Hash, path and required-file validation before producing bytes.
    manifest = json.loads((source/MANIFEST).read_text(encoding='utf-8-sig'))
    names = sorted(set(manifest['files']) | {MANIFEST})
    for name in ANCILLARY:
        path = source/name
        if path.is_file():
            if path.is_symlink() or source not in path.resolve().parents:
                raise ValueError('Linked ancillary source refused')
            names.append(name)
    contents = {name:(source/name).read_bytes() for name in sorted(names)}
    components = {'schema_version':1, 'version':VERSION,
                  'inventory_kind':'declared_direct_runtime_dependencies_not_full_transitive_SBOM',
                  'dependency_binaries_included':False,
                  'components':[{'name':name,'version':version,'bundled':False,
                                 'redistribution_permission':'not_asserted'}
                                for name,version in sorted(PINNED_DEPENDENCIES.items())],
                  'runtime':{'name':'CPython','version_requirement':'3.11.x x64 with Tk', 'bundled':False}}
    contents['COMPONENTS.json']=(json.dumps(components,ensure_ascii=False,indent=2)+'\n').encode()
    identity = {'schema_version':1, 'version':VERSION, 'base_commit':BASE_COMMIT,
                'artifact_kind':'source_delivery', 'production_acceptance':False,
                'dependency_binaries_included':False, 'publisher_signature_included':False,
                'manifest_sha256':hashlib.sha256(contents[MANIFEST]).hexdigest(),
                'baseline_package_sha256':BASELINE_PACKAGE_SHA256,
                'components_sha256':hashlib.sha256(contents['COMPONENTS.json']).hexdigest(),
                'ancillary_sha256':{n:hashlib.sha256(contents[n]).hexdigest() for n in ANCILLARY if n in contents}}
    contents['SOURCE_IDENTITY.json'] = (json.dumps(identity,ensure_ascii=False,indent=2)+'\n').encode()
    return contents


def write_archive(contents, output):
    output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation avoids overwrites; remove only this build's partial file.
    created=False
    try:
        with output.open('xb') as stream:
            created=True
            with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
                for name,data in sorted(contents.items()):
                    info = zipfile.ZipInfo('QMT-IPO-'+VERSION+'-source/'+name,(2026,9,14,0,0,0))
                    info.compress_type=zipfile.ZIP_DEFLATED
                    info.create_system=3;info.external_attr=0o100644<<16
                    archive.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        from package_verify import verify_zip
        verify_zip(output)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {'ok':True,'artifact_kind':'source_delivery','version':VERSION,
            'file_count':len(contents),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Build a checked source candidate, not a production installer')
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=build(args.source,args.output)
    except Exception as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'code':'source_package_build_failed'}))
        return 2
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
