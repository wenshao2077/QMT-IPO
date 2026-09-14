"""Round-2 offline fault injection. Fake account/provider; socket use is forbidden."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from app import inspect_health, watch
from calendar_health import coverage_report
from environment_check import environment_report
from maintenance import (MaintenanceError, export_support, ledger_summary, main,
                         recovery_plan, status_report)
from notification_policy import combined_notification_stats, notification_stats
from runtime import atomic_json, read_json
from support import CHINA, Store

SOURCE = Path(__file__).resolve().parent
NOW = datetime(2026,9,14,10,tzinfo=CHINA)
SECRET = 'NEVER_EXPORT_PRIVATE_SENTINEL_9384716'
TASKS = ('QmtIPO3-Cycle','QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup')


def environment():
    pins = {'xtquant':'250807.1.2','numpy':'1.26.4','pandas':'2.2.3'}
    return environment_report(system='win32',python_version=(3,11,9),bits=64,machine='AMD64',
                              version_reader=pins.__getitem__,module_finder=lambda _: object())


def tasks(root=None):
    return {'ok':True,'execution_enabled':False,'account_tail':SECRET,'qmt_path':SECRET,
            'tasks':[{'name':n,'exists':True,'enabled':False,'state':'Disabled','xml':SECRET} for n in TASKS]}


def bytes_snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'安装 路径';self.root.mkdir()
        (self.root/'runtime').mkdir();(self.root/'state').mkdir();(self.root/'secure').mkdir()
        (self.root/'userdata_mini').mkdir()
        self.cfg={'account_id':SECRET,'qmt_userdata':str(self.root/'userdata_mini'),
                  'state_dir':str(self.root/'state'),'control_dir':str(self.root/'runtime'),
                  'webhook_file':str(self.root/'secure/webhook.txt'),'allowed_markets':['SH','SZ'],
                  'enable_execution':False,'calendar_dir':str(SOURCE/'calendars')}
        atomic_json(self.root/'config.json',self.cfg)
        (self.root/'secure/webhook.txt').write_text(SECRET)
        atomic_json(self.root/'installed-source.json',{'version':'3.4.0-alpha2'})
        store=Store(self.root/'state/state.sqlite3');store.close()
        self.net=patch('socket.socket',side_effect=AssertionError('Network forbidden'));self.net.start()
        self.kw={'now':NOW,'environment_provider':environment,'task_provider':tasks}

    def tearDown(self):
        self.net.stop();self.temp.cleanup()


class CalendarLookaheadTests(Fixture):
    def test_current_september_has_no_early_expiry_alert(self):
        result=coverage_report(self.cfg,'2026-09-14')
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['first_unknown_day'],'2027-01-01')
        self.assertEqual(result['verified_through'],'2026-12-31')
        self.assertFalse(result['downloaded'])

    def test_weekend_does_not_hide_next_weekday_unknown(self):
        self.cfg['calendar_dir']=str(self.base/'no-calendars')
        result=coverage_report(self.cfg,'2026-09-12')
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['first_unknown_day'],'2026-09-14')

    def test_year_boundary_not_extrapolated(self):
        result=coverage_report(self.cfg,'2026-12-26')
        self.assertEqual(result['status'],'expiring')
        self.assertEqual(result['severity'],'urgent')
        self.assertEqual(result['first_unknown_day'],'2027-01-01')

    def test_2027_weekend_still_checks_monday(self):
        result=coverage_report(self.cfg,'2027-01-02')
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['first_unknown_day'],'2027-01-04')

    def test_same_coverage_issue_stable_identity(self):
        a=coverage_report(self.cfg,'2026-11-05');b=coverage_report(self.cfg,'2026-11-06')
        self.assertEqual(a['issue_key'],b['issue_key'])

    def test_urgency_escalation_new_event(self):
        a=coverage_report(self.cfg,'2026-11-15');b=coverage_report(self.cfg,'2026-12-15')
        self.assertNotEqual(a['issue_key'],b['issue_key'])
        self.assertEqual(b['severity'],'warning')

    def test_whole_horizon_checked_without_claiming_beyond_it(self):
        result=coverage_report(self.cfg,'2026-09-14',horizon_days=10,warning_days=7)
        self.assertTrue(result['horizon_fully_verified'])
        self.assertEqual(result['verified_through'],'2026-09-24')
        self.assertIsNone(result['first_unknown_day'])
        self.assertEqual(result['checked_days'],11)

    def test_bad_horizon_rejected(self):
        for horizon,warning in [(0,0),(10000,1),(True,0),(10,11),(10,-1)]:
            with self.subTest(horizon=horizon,warning=warning),self.assertRaises(ValueError):
                coverage_report(self.cfg,horizon_days=horizon,warning_days=warning)

    def test_local_coverage_has_no_mutation(self):
        before=bytes_snapshot(self.root)
        coverage_report(self.cfg,'2026-12-24')
        self.assertEqual(bytes_snapshot(self.root),before)

    def test_holiday_ahead_does_not_look_like_missing_calendar(self):
        result=coverage_report(self.cfg,'2026-09-30',horizon_days=8,warning_days=7)
        self.assertEqual(result['status'],'ready')
        self.assertTrue(result['horizon_fully_verified'])

    def test_weekend_maintenance_notice_deduplicated(self):
        now=datetime(2026,12,26,10,tzinfo=CHINA)
        for i in range(4):watch(self.cfg,False,now+timedelta(minutes=i*5))
        db=sqlite3.connect(self.root/'runtime/monitor.sqlite3')
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
        finally:db.close()
        self.assertEqual(ledger_summary(self.root/'state/state.sqlite3')['intent_count'],0)

    def test_unknown_day_existing_calendar_alert_not_duplicated(self):
        self.cfg['enable_execution']=True;self.cfg['calendar_dir']=str(self.base/'no-calendar')
        for _ in range(3):watch(self.cfg,False,NOW)
        self.assertEqual(notification_stats(self.root/'runtime/monitor.sqlite3')['pending'],1)


class DeliveryObservabilityTests(Fixture):
    def test_empty_queue_no_fabricated_delivery_time(self):
        stats=notification_stats(self.root/'state/state.sqlite3')
        self.assertIsNone(stats['last_confirmed_at']);self.assertFalse(stats['delivery_time_known'])

    def test_success_creates_receipt_at_confirmed_time(self):
        store=Store(self.root/'state/state.sqlite3')
        try:
            store.event('ok','FAKE');store.drain(lambda _:None,now=lambda:100,sleep=lambda _:None)
        finally:store.close()
        self.assertEqual(notification_stats(self.root/'state/state.sqlite3')['last_confirmed_at'],100)

    def test_failed_send_no_receipt(self):
        store=Store(self.root/'state/state.sqlite3')
        try:store.event('fail','FAKE');store.drain(Mock(side_effect=TimeoutError()),now=lambda:100)
        finally:store.close()
        stats=notification_stats(self.root/'state/state.sqlite3')
        self.assertEqual(stats['failed'],1);self.assertIsNone(stats['last_confirmed_at'])

    def test_retry_records_only_success_time(self):
        store=Store(self.root/'state/state.sqlite3')
        try:
            store.event('fail','FAKE');store.drain(Mock(side_effect=TimeoutError()),now=lambda:100)
            store.drain(lambda _:None,now=lambda:131)
        finally:store.close()
        stats=notification_stats(self.root/'state/state.sqlite3')
        self.assertEqual(stats['last_confirmed_at'],131);self.assertEqual(stats['failed'],0)

    def test_legacy_delivered_rows_not_backfilled(self):
        path=self.root/'state/state.sqlite3'
        db=sqlite3.connect(path)
        db.execute('DROP TABLE notification_delivery_receipts')
        db.execute("INSERT INTO outbox VALUES('old','FAKE',1,0,0,'')");db.commit();db.close()
        before=path.read_bytes()
        self.assertIsNone(notification_stats(path)['last_confirmed_at'])
        self.assertEqual(path.read_bytes(),before)
        store=Store(path);store.close()
        self.assertIsNone(notification_stats(path)['last_confirmed_at'])

    def test_suppressed_is_not_delivered(self):
        store=Store(self.root/'state/state.sqlite3')
        try:
            store.event('suppress','FAKE')
            row=store.db.execute('SELECT id FROM outbox').fetchone()[0]
            with store.transaction():store.db.execute('INSERT INTO notification_suppressions VALUES(?,?,?,?)',(row,'fake',99,'fake'))
            sender=Mock();store.drain(sender);sender.assert_not_called()
        finally:store.close()
        stats=notification_stats(self.root/'state/state.sqlite3')
        self.assertEqual(stats['suppressed'],1);self.assertIsNone(stats['last_confirmed_at'])

    def test_corrupt_monitor_makes_total_unknown(self):
        (self.root/'runtime/monitor.sqlite3').write_bytes(b'NOT A DATABASE')
        result=combined_notification_stats(self.cfg)
        self.assertIsNone(result['pending']);self.assertIsNone(result['failed'])
        self.assertEqual(result['queues']['business']['pending'],0)
        self.assertFalse(result['readable'])

    def test_absent_monitor_explicit_not_created(self):
        result=combined_notification_stats(self.cfg)
        self.assertEqual(result['queues']['monitor']['status'],'not_created')
        self.assertEqual(result['pending'],0)
        self.assertFalse((self.root/'runtime/monitor.sqlite3').exists())

    def test_missing_previously_observed_monitor_is_unknown(self):
        atomic_json(self.root/'runtime/health.json',{'status':'previous_monitor_run'})
        result=combined_notification_stats(self.cfg)
        self.assertFalse(result['readable'])
        self.assertEqual(result['queues']['monitor']['status'],'unavailable')
        self.assertIsNone(result['pending'])
        self.assertFalse((self.root/'runtime/monitor.sqlite3').exists())

    def test_missing_business_not_recreated(self):
        path=self.root/'state/state.sqlite3';path.unlink()
        result=combined_notification_stats(self.cfg)
        self.assertFalse(result['readable']);self.assertFalse(path.exists())

    def test_paused_still_reports_notification_failure_not_daily_missing(self):
        store=Store(self.root/'state/state.sqlite3')
        try:store.event('bad','FAKE');store.drain(Mock(side_effect=TimeoutError()),now=lambda:100)
        finally:store.close()
        health=inspect_health(self.cfg,NOW)
        self.assertEqual(health['status'],'attention')
        self.assertIn('notification_backlog',health['issues'])
        self.assertNotIn('daily_run_missing',health['issues'])

    def test_paused_corrupt_ledger_remains_critical(self):
        (self.root/'state/state.sqlite3').write_bytes(b'broken')
        health=inspect_health(self.cfg,NOW)
        self.assertIn('ledger_unavailable',health['issues'])
        self.assertFalse(health['active'])

    def test_health_file_written_even_when_monitor_cannot_open(self):
        (self.root/'runtime/monitor.sqlite3').write_bytes(b'broken')
        with self.assertRaises(sqlite3.Error):watch(self.cfg,False,NOW)
        health=read_json(self.root/'runtime/health.json')
        self.assertIn('monitor_ledger_unavailable',health['issues'])


class MaintenanceBoundaryTests(Fixture):
    def report(self,**kwargs):
        return status_report(self.root,**dict(self.kw,**kwargs))

    def test_snapshot_no_private_values_or_paths(self):
        report=self.report();text=json.dumps(report,ensure_ascii=False)
        self.assertNotIn(SECRET,text);self.assertNotIn(str(self.root),text)
        self.assertFalse(report['account_connected']);self.assertFalse(report['message_sent'])
        self.assertEqual(report['ledger']['intent_count'],0)

    def test_no_webhook_file_read(self):
        original=Path.open
        def guarded(path,*a,**kw):
            if path.name=='webhook.txt':raise AssertionError('Webhook bytes forbidden')
            return original(path,*a,**kw)
        with patch.object(Path,'open',guarded):self.report()

    def test_only_status_aggregates_not_untrusted_order_state(self):
        db=sqlite3.connect(self.root/'state/state.sqlite3')
        db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?)',(SECRET,'2026-09-14',SECRET,SECRET,101,123,100));db.commit();db.close()
        report=self.report()
        self.assertEqual(report['ledger']['unresolved_count'],1)
        self.assertNotIn(SECRET,json.dumps(report))
        self.assertIn('unresolved_intents',[x['code'] for x in report['issues']])

    def test_reads_committed_wal_without_restoring_database(self):
        db=sqlite3.connect(self.root/'state/state.sqlite3')
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("INSERT INTO intents VALUES('fake','2026-09-14','000000.SZ','UNCERTAIN',NULL,10,100)");db.commit()
            before=db.execute('SELECT * FROM intents').fetchall()
            self.assertEqual(self.report()['ledger']['unresolved_count'],1)
            self.assertEqual(db.execute('SELECT * FROM intents').fetchall(),before)
        finally:db.close()

    def test_export_requires_consent_and_creates_nothing(self):
        output=self.base/'support.zip'
        with self.assertRaises(MaintenanceError):export_support(self.root,output,**self.kw)
        self.assertFalse(output.exists())

    def test_export_exact_allowlist_and_no_raw_secrets(self):
        (self.root/'runtime/private.log').write_text(SECRET)
        output=self.base/'support.zip'
        result=export_support(self.root,output,confirmed=True,**self.kw)
        self.assertFalse(result['uploaded'])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()),{'status.json','MANIFEST.json','说明.txt'})
            manifest=json.loads(archive.read('MANIFEST.json'))
            for name,checksum in manifest.items():self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),checksum)
            for name in archive.namelist():self.assertNotIn(SECRET.encode(),archive.read(name))

    def test_export_does_not_touch_installation(self):
        before=bytes_snapshot(self.root)
        export_support(self.root,self.base/'support.zip',confirmed=True,**self.kw)
        after=bytes_snapshot(self.root)
        self.assertEqual({k:after[k] for k in before},before)
        # SQLite read-only WAL queries can create/manage auxiliary files. Do not
        # use immutable=1 (would risk ignoring active WAL), or delete sidecars.
        self.assertLessEqual(set(after)-set(before),{'state/state.sqlite3-wal','state/state.sqlite3-shm'})

    def test_export_refuses_inside_installation(self):
        with self.assertRaises(MaintenanceError):export_support(self.root,self.root/'support.zip',confirmed=True,**self.kw)
        self.assertFalse((self.root/'support.zip').exists())

    def test_export_refuses_external_state_directory(self):
        external=self.base/'old ledger';external.mkdir()
        self.cfg['state_dir']=str(external);atomic_json(self.root/'config.json',self.cfg)
        with self.assertRaises(MaintenanceError):export_support(self.root,external/'support.zip',confirmed=True,**self.kw)

    def test_export_refuses_overwrite(self):
        output=self.base/'support.zip';output.write_bytes(b'KEEP')
        with self.assertRaises(MaintenanceError):export_support(self.root,output,confirmed=True,**self.kw)
        self.assertEqual(output.read_bytes(),b'KEEP')

    def test_export_failure_removes_only_new_partial_output(self):
        output=self.base/'support.zip'
        with patch('maintenance.zipfile.ZipFile.writestr',side_effect=OSError()):
            with self.assertRaises(OSError):export_support(self.root,output,confirmed=True,**self.kw)
        self.assertFalse(output.exists());self.assertTrue((self.root/'config.json').exists())

    def test_unknown_config_produces_diagnostic_not_secret_error(self):
        (self.root/'config.json').write_text(SECRET)
        report=self.report();self.assertFalse(report['ok'])
        self.assertNotIn(SECRET,json.dumps(report))
        self.assertIn('configuration_unreadable',[x['code'] for x in report['issues']])

    def test_recovery_plan_does_not_remove_marker(self):
        atomic_json(self.root/'maintenance.json',{'secret':SECRET})
        atomic_json(self.root/'runtime/configuration-pending.json',{'secret':SECRET})
        before=bytes_snapshot(self.root)
        result=recovery_plan(self.root,**self.kw)
        self.assertFalse(result['changes_applied'])
        self.assertTrue(result['needs_attention']);self.assertFalse(result['ledger_restored'])
        after=bytes_snapshot(self.root)
        self.assertEqual({k:after[k] for k in before},before)
        self.assertLessEqual(set(after)-set(before),{'state/state.sqlite3-wal','state/state.sqlite3-shm'})
        self.assertNotIn(SECRET,json.dumps(result))

    def test_environment_metadata_filtered(self):
        raw=environment();raw['dependencies']['xtquant']['installed']=SECRET;raw['machine']=SECRET
        result=self.report(environment_provider=lambda:raw)
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertIsNone(result['environment']['dependencies']['xtquant']['installed'])

    def test_scheduler_error_not_exported(self):
        result=self.report(task_provider=Mock(side_effect=ValueError(SECRET)))
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertIn('task_state_unavailable',[x['code'] for x in result['issues']])

    def test_scheduler_inconsistency_reported(self):
        raw=tasks();raw['execution_enabled']=True
        result=self.report(task_provider=lambda _:raw)
        self.assertIn('task_plan_inconsistent',[x['code'] for x in result['issues']])
        self.assertFalse(result['ok'])

    def test_running_task_instructs_wait_not_kill(self):
        raw=tasks();raw['tasks'][0]['state']='Running'
        result=recovery_plan(self.root,**dict(self.kw,task_provider=lambda _:raw))
        self.assertIn('task_running',[x['issue'] for x in result['actions']])
        self.assertTrue(all(not x['automatic_repair_available'] for x in result['actions']))

    def test_missing_ledger_never_recreated_by_report_or_plan(self):
        path=self.root/'state/state.sqlite3';path.unlink()
        self.report();recovery_plan(self.root,**self.kw)
        self.assertFalse(path.exists())

    def test_no_migration_during_readonly_diagnostics(self):
        db=sqlite3.connect(self.root/'state/state.sqlite3');db.execute('DROP TABLE notification_delivery_receipts');db.commit();db.close()
        self.report()
        db=sqlite3.connect(self.root/'state/state.sqlite3')
        try:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='notification_delivery_receipts'").fetchone())
        finally:db.close()

    def test_cli_no_confirm_returns_structured_error(self):
        output=self.base/'support.zip';stdout=io.StringIO()
        with redirect_stdout(stdout):result=main(['export-support','--root',str(self.root),'--output',str(output)])
        self.assertEqual(result,2);self.assertFalse(output.exists())
        self.assertEqual(json.loads(stdout.getvalue())['code'],'explicit_export_confirmation_required')

    def test_cli_rejects_export_arguments_for_readonly_status(self):
        stdout=io.StringIO()
        with redirect_stdout(stdout):result=main(['status','--root',str(self.root),'--confirm-export'])
        self.assertEqual(result,2)
        self.assertEqual(json.loads(stdout.getvalue())['code'],'export_parameters_only_for_export')


class RuntimeAdvisoryTests(unittest.TestCase):
    def test_official_fixed_versions_recognized(self):
        from environment_check import sqlite_runtime_report
        for version in ('3.51.3','3.52.0','3.44.6','3.44.7','3.50.7'):
            with self.subTest(version=version):
                result=sqlite_runtime_report(version)
                self.assertFalse(result['review_required']);self.assertFalse(result['automatically_updated'])

    def test_unfixed_and_other_branches_need_review(self):
        from environment_check import sqlite_runtime_report
        for version in ('3.51.2','3.46.1','3.44.5','3.50.6','3.45.9'):
            with self.subTest(version=version):self.assertTrue(sqlite_runtime_report(version)['review_required'])

    def test_unrecognized_runtime_no_claim_of_fix(self):
        from environment_check import sqlite_runtime_report
        for version in ('unknown','3.51',False,'-3.51.3'):
            with self.subTest(version=version):self.assertTrue(sqlite_runtime_report(version)['review_required'])


if __name__=='__main__':unittest.main(verbosity=2)
