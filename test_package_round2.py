"""Untrusted source ZIP checks; no extracted code is executed."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from build_release import build
from package_verify import PackageError, safe_name, verify_zip
from runtime import atomic_json


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir();files={}
        for name in ('app.py','market_calendar.py','tasks.ps1','panel_tasks.ps1'):
            data=b'# offline fixture\n';(self.source/name).write_bytes(data);files[name]=hashlib.sha256(data).hexdigest()
        atomic_json(self.source/'DELIVERY_MANIFEST.json',{'schema_version':1,'files':files})
        (self.source/'.github/workflows').mkdir(parents=True)
        (self.source/'.github/workflows/offline.yml').write_text('name: offline fixture\n')
        self.archive=self.root/'source.zip';build(self.source,self.archive)
        with zipfile.ZipFile(self.archive) as z:self.contents={n:z.read(n) for n in z.namelist()}
        self.prefix=next(iter(self.contents)).split('/')[0]+'/'
        self.net=patch('socket.socket',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):self.net.stop();self.temp.cleanup()
    def modified(self,contents):
        target=self.root/'modified.zip'
        with zipfile.ZipFile(target,'w') as z:
            for name,value in contents.items():z.writestr(name,value)
        return target
    def test_valid_bound_hash_still_not_publisher_authentication(self):
        digest=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        result=verify_zip(self.archive,digest)
        self.assertTrue(result['expected_hash_matched']);self.assertFalse(result['publisher_authenticity_verified'])
        self.assertFalse(result['code_executed'])
    def test_no_external_hash_claim_without_external_hash(self):
        self.assertFalse(verify_zip(self.archive)['expected_hash_matched'])
    def test_wrong_external_hash_rejected(self):
        with self.assertRaises(PackageError):verify_zip(self.archive,'0'*64)
    def test_malformed_expected_hash_rejected(self):
        with self.assertRaises(PackageError):verify_zip(self.archive,'not-hash')
    def test_changed_source_content_rejected(self):
        self.contents[self.prefix+'app.py']=b'changed'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_unlisted_private_file_rejected(self):
        self.contents[self.prefix+'config.json']=b'PRIVATE'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_missing_required_content_rejected(self):
        del self.contents[self.prefix+'app.py']
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_missing_components_rejected(self):
        del self.contents[self.prefix+'COMPONENTS.json']
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_workflow_ancillary_tamper_rejected(self):
        self.contents[self.prefix+'.github/workflows/offline.yml']=b'changed'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_components_tamper_rejected(self):
        self.contents[self.prefix+'COMPONENTS.json']=b'{}'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_duplicate_member_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.archive,'a') as z:z.writestr(self.prefix+'app.py',b'duplicate')
        with self.assertRaises(PackageError):verify_zip(self.archive)
    def test_windows_case_collision_rejected(self):
        self.contents[self.prefix+'APP.PY']=b'duplicate'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_traversal_and_windows_reserved_names_rejected(self):
        for name in ('../x','/absolute','a/../b','a\\b','a:b','a//b','a/./b','a/CON.txt','a/x.','a/x ','a/\x00x','a/x?.py','a/x*.py','a/x|y.py'):
            with self.subTest(name=name),self.assertRaises(PackageError):safe_name(name)
    def test_multiple_roots_rejected(self):
        self.contents['other/not-allowed.py']=b'x'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_symlink_member_rejected(self):
        with zipfile.ZipFile(self.archive,'a') as z:
            info=zipfile.ZipInfo(self.prefix+'linked.py');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(info,b'../private')
        with self.assertRaises(PackageError):verify_zip(self.archive)
    def test_size_and_file_count_limits(self):
        for name,value in [('MAX_FILES',1),('MAX_MEMBER',1),('MAX_TOTAL',1),('MAX_ARCHIVE',1)]:
            with self.subTest(name=name),patch('package_verify.'+name,value),self.assertRaises(PackageError):verify_zip(self.archive)
    def test_duplicate_json_keys_rejected(self):
        self.contents[self.prefix+'SOURCE_IDENTITY.json']=b'{"version":"one","version":"two"}'
        with self.assertRaises(PackageError):verify_zip(self.modified(self.contents))
    def test_zip_integrity_does_not_import_payload(self):
        (self.source/'app.py').write_text("raise AssertionError('MUST NOT EXECUTE')\n")
        doc=json.loads((self.source/'DELIVERY_MANIFEST.json').read_text());doc['files']['app.py']=hashlib.sha256((self.source/'app.py').read_bytes()).hexdigest()
        atomic_json(self.source/'DELIVERY_MANIFEST.json',doc)
        target=self.root/'not-executed.zip';build(self.source,target)
        self.assertTrue(verify_zip(target)['ok'])
    def test_failed_build_removes_its_partial_artifact(self):
        output=self.root/'partial.zip'
        with patch('build_release.zipfile.ZipFile.writestr',side_effect=OSError('fake disk full')):
            with self.assertRaises(OSError):build(self.source,output)
        self.assertFalse(output.exists())

    def test_failed_final_validation_removes_artifact(self):
        output=self.root/'invalid.zip'
        with patch('package_verify.verify_zip',side_effect=PackageError('fake invalid')):
            with self.assertRaises(PackageError):build(self.source,output)
        self.assertFalse(output.exists())

    def test_reviewed_manifest_ignores_extra_python_and_markdown(self):
        import shutil
        from build_manifest import SOURCE_FILES, build as manifest_build
        source=self.root/'reviewed';source.mkdir()
        origin=Path(__file__).resolve().parent
        for name in SOURCE_FILES:
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(origin/name,target)
        (source/'private_config.py').write_text('SHOULD_NOT_ENTER_RELEASE')
        (source/'docs/private_notes.md').write_text('SHOULD_NOT_ENTER_RELEASE')
        files=manifest_build(source)
        self.assertNotIn('private_config.py',files);self.assertNotIn('docs/private_notes.md',files)

    def test_corrupted_zip_rejected(self):
        self.archive.write_bytes(b'not a zip')
        with self.assertRaises(PackageError):verify_zip(self.archive)


if __name__=='__main__':unittest.main(verbosity=2)
