"""Regression coverage for acceptance findings. Never operates real tasks or SDK."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from build_release import build
from deploy import doctor
from maintenance import recovery_plan, export_support, MaintenanceError
from offline_dependencies import verify_wheels, install
from package_verify import verify_directory, PackageError
from runtime import atomic_json, validate_config
from support import Store
from test_maintenance_round2 import Fixture, environment


class RecoveryTests(Fixture):
    def test_pending_configuration_still_protects_external_ledger_from_export(self):
        external=self.base/'external-state'
        (self.root/'state').rename(external)
        self.cfg['state_dir']=str(external)
        atomic_json(self.root/'config.json',self.cfg)
        (self.root/'runtime/configuration-pending.json').write_text('{}')
        with self.assertRaises(MaintenanceError):
            export_support(self.root,external/'export.zip',confirmed=True,**self.kw)
        self.assertFalse((external/'export.zip').exists())

    def test_doctor_blocks_source_maintenance_before_any_task_query(self):
        atomic_json(self.root/'maintenance.json', {'status':'prepared'})
        with patch('panel_backend.WindowsController.call', side_effect=AssertionError('no query')):
            result=doctor(self.root, environment_provider=environment)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'source_maintenance_pending')
        self.assertTrue((self.root/'maintenance.json').exists())

    def test_interrupted_configuration_is_diagnosable_but_never_runnable(self):
        marker=self.root/'runtime/configuration-pending.json'
        marker.write_text('{}')
        result=recovery_plan(self.root, **self.kw)
        codes={a['issue'] for a in result['actions']}
        self.assertIn('configuration_recovery_required', codes)
        self.assertNotIn('configuration_unreadable', codes)
        self.assertEqual(result['current']['ledger']['status'], 'readable')
        with self.assertRaises(ValueError):validate_config(self.cfg)
        self.assertEqual(marker.read_text(), '{}')

    def test_failed_store_initialization_explicitly_closes_connection(self):
        path=self.root/'runtime/broken.sqlite3'
        path.write_bytes(b'not a database')
        conn=sqlite3.connect(path)
        proxy=Mock(wraps=conn)
        with patch('support.sqlite3.connect', return_value=proxy):
            with self.assertRaises(sqlite3.DatabaseError):Store(path)
        proxy.close.assert_called_once()
        path.rename(path.with_suffix('.closed'))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        src=self.root/'input';src.mkdir()
        files={}
        for name in ('app.py','market_calendar.py','tasks.ps1','panel_tasks.ps1'):
            (src/name).write_bytes(b'# fixture\n')
            files[name]=hashlib.sha256(b'# fixture\n').hexdigest()
        atomic_json(src/'DELIVERY_MANIFEST.json', {'schema_version':1,'files':files})
        archive=self.root/'source.zip';build(src,archive)
        with zipfile.ZipFile(archive) as z:z.extractall(self.root/'extracted')
        self.source=next((self.root/'extracted').iterdir())

    def tearDown(self):self.temp.cleanup()

    def test_original_directory_passes(self):
        self.assertTrue(verify_directory(self.source)['ok'])

    def test_invalid_identity_rejected(self):
        (self.source/'SOURCE_IDENTITY.json').write_text('{broken')
        with self.assertRaises(PackageError):verify_directory(self.source)

    def test_missing_components_rejected(self):
        (self.source/'COMPONENTS.json').unlink()
        with self.assertRaises(PackageError):verify_directory(self.source)

    def test_unlisted_file_rejected(self):
        (self.source/'unlisted.py').write_text('# no execution')
        with self.assertRaises(PackageError):verify_directory(self.source)


class WheelsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.wheels=self.root/'wheels';self.wheels.mkdir()
        filename='demo-1-py3-none-any.whl';data=b'fixture-only'
        (self.wheels/filename).write_bytes(data)
        digest=hashlib.sha256(data).hexdigest()
        atomic_json(self.root/'dependencies.lock.json', {'schema_version':1,
            'wheels':[{'name':'demo','version':'1','filename':filename,'sha256':digest}]})
        (self.root/'requirements-offline.txt').write_text('demo==1 --hash=sha256:'+digest+'\n')

    def tearDown(self):self.temp.cleanup()

    def test_pip_cannot_use_index_or_unhashed_requirements(self):
        with patch('offline_dependencies.subprocess.call', return_value=0) as call:
            self.assertEqual(install(self.wheels,self.root),0)
        args=call.call_args.args[0]
        for flag in ('--isolated','--no-index','--require-hashes','--only-binary=:all:'):
            self.assertIn(flag,args)

    def test_changed_wheel_stops_before_pip(self):
        next(self.wheels.iterdir()).write_bytes(b'tampered')
        with patch('offline_dependencies.subprocess.call', side_effect=AssertionError('pip forbidden')):
            with self.assertRaises(ValueError):install(self.wheels,self.root)

    def test_extra_wheel_rejected(self):
        (self.wheels/'extra.whl').write_bytes(b'extra')
        with self.assertRaises(ValueError):verify_wheels(self.wheels,self.root)


if __name__=='__main__':unittest.main()
