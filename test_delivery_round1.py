"""Round-one delivery tests. Fake accounts only, sockets blocked, no task registration."""
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import sqlite3
import sys
import threading
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import configuration
from configuration import ConfigurationError, save_first_use, require_disabled_tasks, TASK_NAMES
from configure import ConfigurationWizard
from deploy import installation_plan, doctor, main as deploy_main
from discovery_policy import next_discovery_at
from environment_check import environment_report
from install_journal import start, load, advance, verify_fresh, source_may_start, InstallStateError
from installer import new_install, destination, payload, upgrade, rollback
from release_info import PINNED_DEPENDENCIES
from runtime import atomic_json, read_json, validate_config
from support import Store

SOURCE = Path(__file__).resolve().parent
HOOK = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=FAKE_LOCAL_TEST_ONLY'


def good_environment(**changes):
    values = dict(system='win32', python_version=(3,11,9), bits=64, machine='AMD64',
                  version_reader=lambda n: PINNED_DEPENDENCIES[n], module_finder=lambda n: object())
    values.update(changes)
    return environment_report(**values)


def disabled_snapshot(root=None):
    return {'ok': True, 'execution_enabled': False, 'tasks': [
        {'name': name, 'exists': True, 'enabled': False, 'state': 'Disabled'} for name in TASK_NAMES]}


class DeliveryRoundOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base/'source package'
        self.source.mkdir()
        # Minimal valid source payload; no SDK or executable trading implementation.
        for name in ('app.py', 'market_calendar.py', 'tasks.ps1', 'panel_tasks.ps1'):
            (self.source/name).write_text('# offline fixture\n', encoding='utf-8')
        self.refresh_manifest()
        self.root = self.base/'中文 installation'
        new_install(self.source, self.root)
        self.qmt = self.base/'external'/'userdata_mini'
        self.qmt.mkdir(parents=True)
        self.net = patch('socket.socket', side_effect=AssertionError('Network forbidden'))
        self.net.start()

    def tearDown(self):
        self.net.stop()
        self.temp.cleanup()

    def refresh_manifest(self):
        names = ('app.py','market_calendar.py','tasks.ps1','panel_tasks.ps1')
        atomic_json(self.source/'DELIVERY_MANIFEST.json', {'schema_version':1, 'files': {
            name:hashlib.sha256((self.source/name).read_bytes()).hexdigest() for name in names}})

    def save(self, **changes):
        args = dict(account_id='FAKE_ACCOUNT_123456', qmt_userdata=str(self.qmt),
                    allowed_markets=['SH','SZ'], webhook=HOOK,
                    task_snapshot=disabled_snapshot, mutex_factory=nullcontext)
        args.update(changes)
        return save_first_use(self.root, **args)

    def session(self):
        root = self.base/'interrupted'
        start(self.source,root)
        (root/'.venv').mkdir()
        advance(self.source,root,'environment_ready')
        advance(self.source,root,'source_started')
        new_install(self.source,root)
        advance(self.source,root,'source_ready')
        return root

    def test_doctor_detects_execution_task_inconsistency(self):
        snapshot=disabled_snapshot();snapshot['execution_enabled']=True
        with patch('diagnostics.configuration_report', return_value={'ok':True}), \
                patch('panel_backend.WindowsController') as controller:
            controller.return_value.call.return_value=snapshot
            result=doctor(self.root,environment_provider=good_environment)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'],'execution_plan_inconsistent')
        self.assertNotIn('FAKE_ACCOUNT',json.dumps(result))

    def test_doctor_distinguishes_installed_disabled_from_live_acceptance(self):
        with patch('diagnostics.configuration_report',return_value={'ok':True}), \
                patch('panel_backend.WindowsController') as controller:
            controller.return_value.call.return_value=disabled_snapshot()
            result=doctor(self.root,environment_provider=good_environment)
        self.assertTrue(result['ok']);self.assertEqual(result['execution_plan_state'],'disabled')
        self.assertEqual(result['broker_acceptance'],'not_verified')

    def test_environment_matches_without_importing_sdk(self):
        import builtins
        original = builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.startswith('xtquant'):
                raise AssertionError('SDK import forbidden')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            result = good_environment()
        self.assertTrue(result['ok'])
        self.assertEqual(result['level'],'local_environment_only')
        self.assertFalse(result['account_connected'])
        self.assertIn('broker_acceptance',result['unverified'])

    def test_environment_blocks_wrong_bits_version_machine_and_os(self):
        cases = [({'bits':32},'python_x64_required'),({'machine':'ARM64'},'python_x64_required'),
                 ({'system':'linux'},'windows_required'),({'python_version':(3,13,0)},'use_verified_python_3_11')]
        for change,code in cases:
            with self.subTest(change=change):
                report=good_environment(**change)
                self.assertFalse(report['ok']);self.assertIn(code,report['issues'])

    def test_environment_blocks_missing_or_mismatched_dependencies(self):
        def missing(name): raise importlib.metadata.PackageNotFoundError(name)
        self.assertIn('dependency_missing:xtquant',good_environment(version_reader=missing)['issues'])
        self.assertIn('dependency_version_mismatch:numpy',good_environment(version_reader=lambda n:'0')['issues'])
        self.assertIn('tkinter_missing',good_environment(module_finder=lambda n:None)['issues'])

    def test_environment_metadata_errors_are_not_reported_as_installed(self):
        def broken(name): raise OSError('FAKE_SECRET_MUST_NOT_APPEAR')
        report=good_environment(version_reader=broken)
        self.assertIn('dependency_metadata_unreadable:xtquant',report['issues'])
        self.assertNotIn('FAKE_SECRET',json.dumps(report))

    def test_native_control_mutex_or_platform_refusal(self):
        if sys.platform != 'win32':
            with self.assertRaises(ConfigurationError):
                with configuration.control_mutex():pass
            return
        observed=[]
        def contender():
            try:
                with configuration.control_mutex():observed.append('unexpected_acquisition')
            except ConfigurationError as exc:observed.append(str(exc))
        with configuration.control_mutex():
            thread=threading.Thread(target=contender);thread.start();thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(observed,['another_control_operation_running'])

    def test_completed_receipt_can_plan_newer_package_upgrade(self):
        root=self.session()
        advance(self.source,root,'dependencies_ready');advance(self.source,root,'tasks_ready');advance(self.source,root,'complete')
        self.assertEqual(installation_plan(root,self.source)['recommended_operation'],'Doctor')
        before=(root/'installed-source.json').read_bytes()
        (self.source/'app.py').write_text('# next reviewed version\n');self.refresh_manifest()
        self.assertEqual(installation_plan(root,self.source)['recommended_operation'],'Upgrade')
        result=upgrade(self.source,root,True)
        self.assertEqual(installation_plan(root,self.source)['recommended_operation'],'Doctor')
        rollback(root,result['rollback_id'],True)
        self.assertEqual((root/'installed-source.json').read_bytes(),before)

    def test_wizard_save_disabled_preserves_private_paths_and_ledger(self):
        before=read_json(self.root/'config.json')
        ledger=(self.root/'state/state.sqlite3').read_bytes()
        result=self.save()
        after=read_json(self.root/'config.json')
        for key in ('state_dir','control_dir','webhook_file'):
            self.assertEqual(before[key],after[key])
        self.assertFalse(after['enable_execution'])
        self.assertEqual((self.root/'state/state.sqlite3').read_bytes(),ledger)
        self.assertEqual((self.root/'secure/webhook.txt').read_text(),HOOK)
        self.assertNotIn('FAKE_ACCOUNT',json.dumps(result));self.assertNotIn('key=',json.dumps(result))
        self.assertEqual(result['submission_calls'],0)
        self.assertFalse((self.root/'runtime/configuration-pending.json').exists())

    def test_wizard_blank_hook_preserves_existing_secret(self):
        (self.root/'secure/webhook.txt').write_text(HOOK)
        self.save(webhook=None)
        self.assertEqual((self.root/'secure/webhook.txt').read_text(),HOOK)

    def test_wizard_rejects_invalid_input_without_writes(self):
        before=(self.root/'config.json').read_bytes()
        for change in ({'account_id':'YOUR_ACCOUNT'},{'account_id':'bad\naccount'},
                       {'allowed_markets':['BJ']},{'allowed_markets':[]},
                       {'qmt_userdata':str(self.base/'missing')},{'webhook':'http://bad.invalid'}):
            with self.subTest(change=change),self.assertRaises(ConfigurationError):self.save(**change)
        self.assertEqual((self.root/'config.json').read_bytes(),before)
        self.assertEqual((self.root/'secure/webhook.txt').read_bytes(),b'')

    def test_wizard_rejects_enabled_execution(self):
        cfg=read_json(self.root/'config.json');cfg['enable_execution']=True
        atomic_json(self.root/'config.json',cfg)
        with self.assertRaises(ConfigurationError):self.save()
        self.assertTrue(read_json(self.root/'config.json')['enable_execution'])

    def test_wizard_requires_known_disabled_owned_task_set(self):
        variants=[]
        for mutate in [lambda x:x.update(ok=False),lambda x:x['tasks'].pop(),
                       lambda x:x['tasks'][0].update(enabled=True),
                       lambda x:x['tasks'][0].update(state='Running'),
                       lambda x:x['tasks'][0].update(state='Unknown'),
                       lambda x:x['tasks'][0].update(name='other-task')]:
            state=disabled_snapshot();mutate(state);variants.append(state)
        before=(self.root/'config.json').read_bytes()
        for state in variants:
            with self.subTest(state=state),self.assertRaises(ConfigurationError):
                self.save(task_snapshot=lambda root,s=state:s)
        self.assertEqual((self.root/'config.json').read_bytes(),before)

    def test_wizard_serializes_with_control_mutex(self):
        mutex=Mock(side_effect=ConfigurationError('another_control_operation_running'))
        with self.assertRaises(ConfigurationError):self.save(mutex_factory=mutex)
        self.assertEqual(read_json(self.root/'config.json')['account_id'],'YOUR_ACCOUNT')

    def test_wizard_refuses_authorization_even_after_pause(self):
        atomic_json(self.root/'runtime/panel-authorization.json',{'authorized':False})
        with self.assertRaises(ConfigurationError):self.save()

    def test_wizard_refuses_live_history(self):
        atomic_json(self.root/'runtime/daily/2026-09-09-live.json',{'phase':'no_ipo'})
        with self.assertRaises(ConfigurationError):self.save()

    def test_wizard_refuses_existing_intents(self):
        store=Store(self.root/'state/state.sqlite3')
        store.db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?)',('fake','2026-09-09','301111.SZ','UNCERTAIN',None,500,0))
        store.db.commit();store.close()
        with self.assertRaises(ConfigurationError):self.save()

    def test_wizard_refuses_outbox_history(self):
        store=Store(self.root/'state/state.sqlite3');store.event('fake','fake');store.close()
        with self.assertRaises(ConfigurationError):self.save()

    def test_wizard_missing_ledger_is_not_recreated(self):
        ledger=self.root/'state/state.sqlite3';ledger.unlink()
        with self.assertRaises(ConfigurationError):self.save()
        self.assertFalse(ledger.exists())

    def test_wizard_refuses_incomplete_install(self):
        atomic_json(self.root/'new-install.json',{'stage':'source_ready'})
        with self.assertRaises(ConfigurationError):self.save()

    def test_wizard_write_failure_restores_old_private_bytes(self):
        cfg=(self.root/'config.json').read_bytes();hook=(self.root/'secure/webhook.txt').read_bytes()
        original=configuration._private_write
        failed=False
        def fail_once(path,content):
            nonlocal failed
            if Path(path)==self.root/'config.json' and not failed:
                failed=True;raise OSError('fake write failure')
            return original(path,content)
        with patch('configuration._private_write',side_effect=fail_once),self.assertRaises(ConfigurationError):self.save()
        self.assertEqual((self.root/'config.json').read_bytes(),cfg)
        self.assertEqual((self.root/'secure/webhook.txt').read_bytes(),hook)
        self.assertFalse((self.root/'runtime/configuration-pending.json').exists())

    def test_failed_configuration_recovery_blocks_execution_validation(self):
        with patch('configuration._private_write',side_effect=OSError('fake')),self.assertRaises(OSError):self.save()
        marker=self.root/'runtime/configuration-pending.json'
        self.assertTrue(marker.exists())
        cfg=read_json(self.root/'config.json');cfg['enable_execution']=True
        with self.assertRaises(ValueError):validate_config(cfg)
        self.assertEqual(read_json(self.root/'config.json')['account_id'],'YOUR_ACCOUNT')

    def test_receipt_rejects_production_directory_without_receipt(self):
        with self.assertRaises(InstallStateError):load(self.source,self.root)
        with self.assertRaises(InstallStateError):start(self.source,self.root)
        self.assertTrue((self.root/'state/state.sqlite3').exists())

    def test_receipt_stage_transitions_and_source_verification(self):
        root=self.session()
        before=(root/'state/state.sqlite3').read_bytes()
        self.assertTrue(verify_fresh(self.source,root)['source_and_private_state_verified'])
        self.assertEqual((root/'state/state.sqlite3').read_bytes(),before)
        with self.assertRaises(InstallStateError):advance(self.source,root,'complete')
        advance(self.source,root,'dependencies_ready');advance(self.source,root,'tasks_ready');advance(self.source,root,'complete')
        with self.assertRaises(InstallStateError):verify_fresh(self.source,root)

    def test_receipt_changed_source_refused(self):
        root=self.session()
        (self.source/'app.py').write_text('# different package\n');self.refresh_manifest()
        with self.assertRaises(InstallStateError):load(self.source,root)

    def test_receipt_missing_ledger_refused_without_recreation(self):
        root=self.session();ledger=root/'state/state.sqlite3';ledger.unlink()
        with self.assertRaises(InstallStateError):verify_fresh(self.source,root)
        self.assertFalse(ledger.exists())

    def test_receipt_private_config_or_key_change_refused(self):
        root=self.session();cfg=read_json(root/'config.json');cfg['account_id']='FAKE_CUSTOMER'
        atomic_json(root/'config.json',cfg)
        with self.assertRaises(InstallStateError):verify_fresh(self.source,root)
        cfg['account_id']='YOUR_ACCOUNT';atomic_json(root/'config.json',cfg)
        (root/'secure/webhook.txt').write_text(HOOK)
        with self.assertRaises(InstallStateError):verify_fresh(self.source,root)

    def test_receipt_partial_source_is_not_reinitialized(self):
        root=self.base/'partial';start(self.source,root)
        advance(self.source,root,'environment_ready');advance(self.source,root,'source_started')
        (root/'state').mkdir();(root/'state/state.sqlite3').write_bytes(b'partial database')
        with self.assertRaises(InstallStateError):source_may_start(self.source,root)
        with self.assertRaises(InstallStateError):new_install(self.source,root)
        self.assertEqual((root/'state/state.sqlite3').read_bytes(),b'partial database')

    def test_crash_after_source_before_checkpoint_can_be_verified(self):
        root=self.base/'post-source';start(self.source,root)
        advance(self.source,root,'environment_ready');advance(self.source,root,'source_started')
        new_install(self.source,root)
        self.assertTrue(verify_fresh(self.source,root)['ok'])
        self.assertEqual(advance(self.source,root,'source_ready')['stage'],'source_ready')

    def test_resume_refuses_ledger_activity(self):
        root=self.session();store=Store(root/'state/state.sqlite3');store.event('fake','fake');store.close()
        with self.assertRaises(InstallStateError):verify_fresh(self.source,root)

    def test_plan_distinguishes_new_resume_existing_unknown_without_writes(self):
        empty=self.base/'not-created'
        self.assertEqual(installation_plan(empty,self.source)['recommended_operation'],'New')
        self.assertFalse(empty.exists())
        self.assertEqual(installation_plan(self.root,self.source)['recommended_operation'],'Upgrade')
        root=self.session()
        self.assertEqual(installation_plan(root,self.source)['recommended_operation'],'ResumeNew')
        unknown=self.base/'unknown';unknown.mkdir();(unknown/'unrelated.txt').write_text('keep')
        self.assertFalse(installation_plan(unknown,self.source)['ok'])
        self.assertFalse(installation_plan(self.source,self.source)['ok'])

    def test_deployment_package_json_is_single_and_non_sensitive(self):
        output=io.StringIO()
        with redirect_stdout(output):rc=deploy_main(['verify-package','--source',str(self.source)])
        self.assertEqual(rc,0)
        report=json.loads(output.getvalue())
        self.assertFalse(report['publisher_authenticity_verified'])
        self.assertFalse(report['account_connected']);self.assertFalse(report['message_sent'])
        self.assertEqual(report['submission_calls'],0)

    def test_doctor_without_root_only_checks_environment(self):
        report=doctor(None,environment_provider=good_environment)
        self.assertTrue(report['ok']);self.assertEqual(report['account_permissions'],'not_verified')
        self.assertEqual(report['broker_acceptance'],'not_verified')
        self.assertFalse(report['account_connected'])

    def test_delivery_whitelist_rejects_private_json_and_ledger(self):
        for name in ('config.json','new-install.json','state.sqlite3','secure/webhook.txt','../evil.py'):
            with self.subTest(name=name),self.assertRaises(ValueError):destination(name)
        for name in ('开始安装.cmd','config.schema.json','DEPLOY_PROTOCOL.json','COMPATIBILITY.json'):
            self.assertEqual(destination(name),Path(name))

    def test_protocol_has_no_secret_or_execution_operations(self):
        protocol=read_json(SOURCE/'DEPLOY_PROTOCOL.json')
        self.assertEqual(set(protocol['operations']),{'Plan','Doctor','VerifyPackage','New','ResumeNew','Configure','Upgrade','Rollback',
                                                    'Health','RecoveryPlan','ExportSupport','CalendarCoverage','VerifyArchive'})
        self.assertIn('reinitialize_ledger',protocol['forbidden_automatic_actions'])
        entry=(SOURCE/'setup.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('install.ps1',entry);self.assertIn('deploy.py',entry)
        self.assertNotIn('Start-ScheduledTask',entry)
        self.assertNotIn('Enable-ScheduledTask',entry)

    def test_upgrade_and_rollback_preserve_private_data(self):
        self.save()
        cfg=(self.root/'config.json').read_bytes();hook=(self.root/'secure/webhook.txt').read_bytes()
        ledger=(self.root/'state/state.sqlite3').read_bytes()
        (self.source/'app.py').write_text('# changed code\n');self.refresh_manifest()
        result=upgrade(self.source,self.root,True)
        rollback(self.root,result['rollback_id'],True)
        self.assertEqual((self.root/'config.json').read_bytes(),cfg)
        self.assertEqual((self.root/'secure/webhook.txt').read_bytes(),hook)
        self.assertEqual((self.root/'state/state.sqlite3').read_bytes(),ledger)

    def test_configuration_gui_construction_and_close_do_not_save(self):
        window=tk.Tk();saver=Mock()
        try:
            wizard=ConfigurationWizard(window,self.root,saver)
            window.update_idletasks()
            self.assertEqual(wizard.hook.get(),'')
            self.assertGreater(wizard.save_button.winfo_reqwidth(),50)
            saver.assert_not_called()
        finally:window.destroy()

    def test_configuration_gui_save_remains_local_and_handles_destroy(self):
        window=tk.Tk();saver=Mock(return_value={'ok':True})
        wizard=ConfigurationWizard(window,self.root,saver)
        wizard.account.set('FAKE_LOCAL');wizard.qmt.set(str(self.qmt));wizard.hook.set(HOOK)
        with patch('configure.messagebox.showinfo') as message:
            wizard.save()
        saver.assert_called_once();message.assert_called_once()
        self.assertEqual(saver.call_args.kwargs['account_id'],'FAKE_LOCAL')


class DiscoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,9,9,40,tzinfo=timezone(timedelta(hours=8)))

    def test_first_next_checkpoint(self):
        self.assertEqual(next_discovery_at(self.now.isoformat(),self.now).strftime('%H:%M'),'10:00')

    def test_exact_checkpoint_advances_to_next(self):
        now=self.now.replace(hour=10,minute=0)
        self.assertEqual(next_discovery_at(now.isoformat(),now).strftime('%H:%M'),'11:00')

    def test_missed_checkpoint_remains_due(self):
        self.assertEqual(next_discovery_at(self.now.isoformat(),self.now.replace(hour=10,minute=20)).strftime('%H:%M'),'10:00')

    def test_legacy_invalid_or_future_receipt_gets_one_recheck(self):
        for value in (None,'bad','2026-09-09T09:35:00','2026-09-10T09:35:00+08:00','2026-09-09T11:00:00+08:00'):
            with self.subTest(value=value):self.assertEqual(next_discovery_at(value,self.now),self.now)

    def test_last_checkpoint_is_readonly_closeout(self):
        now=self.now.replace(hour=14,minute=40)
        self.assertEqual(next_discovery_at(now.isoformat(),now).strftime('%H:%M'),'15:05')


if __name__=='__main__':unittest.main(verbosity=2)
