"""Offline archive construction: no installation or network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build_release import build
from runtime import atomic_json


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        files={}
        for name in ('app.py','market_calendar.py','tasks.ps1','panel_tasks.ps1'):
            data=b'# no execution; archive fixture\n';(self.source/name).write_bytes(data)
            files[name]=hashlib.sha256(data).hexdigest()
        atomic_json(self.source/'DELIVERY_MANIFEST.json',{'schema_version':1,'files':files})
        self.network=patch('socket.socket',side_effect=AssertionError('Network forbidden'));self.network.start()

    def tearDown(self):
        self.network.stop();self.temp.cleanup()

    def test_deterministic_archives_exclude_unlisted_private_files(self):
        (self.source/'config.json').write_text('PRIVATE_FAKE_CONFIG')
        (self.source/'private.log').write_text('PRIVATE_FAKE_LOG')
        a,b=self.root/'a.zip',self.root/'b.zip'
        one,two=build(self.source,a),build(self.source,b)
        self.assertEqual(a.read_bytes(),b.read_bytes());self.assertEqual(one['sha256'],two['sha256'])
        with zipfile.ZipFile(a) as z:
            self.assertFalse(any(n.endswith(('config.json','private.log')) for n in z.namelist()))
            identity=json.loads(z.read(next(n for n in z.namelist() if n.endswith('SOURCE_IDENTITY.json'))))
            self.assertFalse(identity['production_acceptance'])
            self.assertFalse(identity['dependency_binaries_included'])

    def test_corrupted_payload_refuses_before_creating_archive(self):
        (self.source/'app.py').write_text('changed')
        target=self.root/'bad.zip'
        with self.assertRaises(ValueError):build(self.source,target)
        self.assertFalse(target.exists())

    def test_refuses_overwrite_and_build_inside_source(self):
        target=self.root/'existing.zip';target.write_bytes(b'existing')
        with self.assertRaises(ValueError):build(self.source,target)
        self.assertEqual(target.read_bytes(),b'existing')
        with self.assertRaises(ValueError):build(self.source,self.source/'nested.zip')

    def test_manifest_cannot_include_private_json(self):
        data=b'PRIVATE_FAKE_SECRET';(self.source/'config.json').write_bytes(data)
        path=self.source/'DELIVERY_MANIFEST.json';doc=json.loads(path.read_text())
        doc['files']['config.json']=hashlib.sha256(data).hexdigest();atomic_json(path,doc)
        with self.assertRaises(ValueError):build(self.source,self.root/'bad.zip')


if __name__=='__main__':unittest.main(verbosity=2)
