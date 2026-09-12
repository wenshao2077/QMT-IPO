"""Offline regression tests: socket blocked, SDK and notifications are fakes only."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from app import dispatch, inspect_health, watch
from calendar_tool import import_annual
from coordinator import Coordinator, Ledger
from diagnostics import configuration_report, test_notification as send_test
from installer import new_install, payload, rollback, upgrade, validate_root
from launcher import command as launch_command, run as launch
from market_calendar import (CalendarService, OPEN, CLOSED, UNKNOWN, SDK_SOURCE,
                             ensure_calendar, refresh_qmt, sealed, validate_document)
from notification_policy import enqueue_calendar_issue, notification_stats, suppress_closed_alerts
from panel_backend import Backend, PHASES, WindowsController
from privacy import redact
from retention import maintain
from run_history import load_runs, describe_run
from runtime import atomic_json, read_json, backup
from support import CHINA, Store
from test_system import FakeBroker

SOURCE = Path(__file__).resolve().parent


class CalendarDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)/'中文 空格目录'
        self.root.mkdir()
        self.cfg = {'account_id':'fake-account-13579', 'qmt_userdata':str(self.root/'userdata_mini'),
                    'state_dir':str(self.root/'state'), 'control_dir':str(self.root/'runtime'),
                    'webhook_file':str(self.root/'private-hook'), 'allowed_markets':['SH','SZ'], 'enable_execution':True}
        (self.root/'userdata_mini').mkdir()
        (self.root/'private-hook').write_text('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake', encoding='utf-8')
        self.store = Ledger(self.root/'state/state.sqlite3')
        self.net = patch('socket.socket', side_effect=AssertionError('Network forbidden'))
        self.net.start()
        self.clock = datetime(2026,9,12,9,35,tzinfo=CHINA)
        self.broker = FakeBroker()
        self.broker.connect_error = True

    def tearDown(self):
        self.net.stop()
        self.store.close()
        self.tmp.cleanup()

    def cycle(self):
        return Coordinator(self.cfg,self.broker,self.store,lambda:self.clock,'live').run()

    def no_bundle(self):
        self.cfg['calendar_dir'] = str(self.root/'empty-bundle')
        return CalendarService(self.cfg)

    def test_65_weekend_polls_zero_connections_zero_orders_zero_connection_alerts(self):
        for _ in range(65):
            result = self.cycle()
            self.clock += timedelta(minutes=5)
        self.assertEqual(result['phase'], CLOSED)
        self.assertFalse(result['completed'])
        self.assertEqual(self.broker.connected,0)
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.store.pending_notifications(),0)
        self.assertEqual(result['attempts'],0)

    def test_exchange_closed_weekdays_do_not_connect(self):
        for month, day in [(1,1),(2,16),(4,6),(5,4),(6,19),(9,25),(10,7)]:
            with self.subTest(day=(month,day)):
                self.clock = datetime(2026,month,day,10,tzinfo=CHINA)
                self.assertEqual(self.cycle()['phase'],CLOSED)
        self.assertEqual(self.broker.connected,0)

    def test_makeup_work_weekends_still_closed(self):
        for day in ['2026-01-04','2026-02-14','2026-02-28','2026-05-09','2026-09-20','2026-10-10']:
            with self.subTest(day=day):self.assertEqual(CalendarService(self.cfg).decide(day).status,CLOSED)

    def test_trading_day_connection_failure_still_alerts(self):
        self.clock = datetime(2026,9,14,10,tzinfo=CHINA)
        result = self.cycle()
        self.assertEqual(result['phase'],'retryable_error')
        self.assertEqual(result['last_activity']['outcome'],'connection_error')
        self.assertEqual(self.broker.connected,1)
        self.assertEqual(self.store.pending_notifications(),1)
        self.assertIn('daily_work_incomplete',inspect_health(self.cfg,self.clock)['issues'])

    def test_missing_calendar_unknown_not_weekday_permission(self):
        service = self.no_bundle()
        self.assertEqual(service.decide('2026-09-14').status,UNKNOWN)
        self.clock = datetime(2026,9,14,10,tzinfo=CHINA)
        self.assertEqual(self.cycle()['phase'],'calendar_unknown')
        self.assertEqual(self.broker.connected,0)

    def test_damaged_calendar_unknown_without_valid_fallback(self):
        service=self.no_bundle()
        for content in ('{broken','[]','null','{"schema_version":2}','{"days": ["2026-09-14"]}'):
            with self.subTest(content=content):
                (self.root/'state/calendar.json').write_text(content)
                self.assertEqual(service.decide('2026-09-14').status,UNKNOWN)

    def test_invalid_cache_can_use_independently_valid_annual_pack(self):
        (self.root/'state/calendar.json').write_text('bad data')
        result=CalendarService(self.cfg).decide('2026-09-14')
        self.assertEqual(result.status,OPEN)
        self.assertTrue(result.warnings)
        self.assertEqual(result.source,'exchange_annual:2026')

    def test_no_2027_extrapolation(self):
        self.assertEqual(CalendarService(self.cfg).decide('2027-01-04').status,UNKNOWN)
        self.assertEqual(CalendarService(self.cfg).decide('2027-01-02').status,CLOSED)

    def test_legacy_full_coverage_cross_verified_and_preserved(self):
        doc=validate_document(read_json(SOURCE/'calendars/2026.json'))
        legacy={'year':2026,'covered_through':'2026-12-31','days':sorted(doc['days']['SH'])}
        path=self.root/'state/calendar.json';atomic_json(path,legacy)
        before=path.read_bytes()
        decision=CalendarService(self.cfg).decide('2026-09-25')
        self.assertEqual(decision.status,CLOSED)
        self.assertEqual(decision.source,'legacy_qmt+exchange_verified')
        self.assertEqual(path.read_bytes(),before)

    def test_legacy_one_holiday_year_hit_does_not_prove_full_year(self):
        service=self.no_bundle()
        atomic_json(self.root/'state/calendar.json',{'year':2026,'covered_through':'2026-12-31','days':['2026-01-05']})
        self.assertEqual(service.decide('2026-09-14').status,UNKNOWN)

    def history(self, days, refreshed='2026-09-14T10:00:00+08:00'):
        return sealed({'schema_version':2,'markets':['SH','SZ'],
            'covered_from':days[0],'covered_through':days[-1],
            'days_by_market':{'SH':days,'SZ':days},'refreshed_at':refreshed,
            'source':{'kind':'qmt_history','url':SDK_SOURCE,'requested_from':'2026-01-01',
                      'requested_through':'2026-09-14','coverage_basis':'first_and_last_returned'}})

    def test_history_outside_first_last_returned_is_unknown_not_closed(self):
        service=self.no_bundle()
        atomic_json(self.root/'state/calendar.json',self.history(['2026-09-10','2026-09-11']))
        self.assertEqual(service.decide('2026-09-14').status,UNKNOWN)
        self.assertEqual(service.decide('2026-01-05').status,UNKNOWN)
        self.assertEqual(service.decide('2026-09-11').status,OPEN)

    def test_calendar_conflict_fails_closed(self):
        doc=self.history(['2026-09-09','2026-09-11'])
        atomic_json(self.root/'state/calendar.json',doc)
        result=CalendarService(self.cfg).decide('2026-09-10')
        self.assertEqual(result.status,UNKNOWN)
        self.assertEqual(result.code,'calendar_conflict')

    def test_invalid_sources_ranges_dates_rejected(self):
        original=read_json(SOURCE/'calendars/2026.json')
        mutations=[lambda x:x.update(covered_from='2026-02-01'),
                   lambda x:x['holidays'].pop('national'),
                   lambda x:x['source']['announcements']['SH'].update(url='https://example.com/fake'),
                   lambda x:x['holidays'].update(national=['2026-10-01','2026-13-01']),
                   lambda x:x.update(complete_year=False)]
        for mutate in mutations:
            doc=json.loads(json.dumps(original));mutate(doc)
            with self.subTest(doc=doc),self.assertRaises((ValueError,TypeError)):
                validate_document(sealed(doc))

    def test_annual_pack_has_exact_explicit_2026_coverage(self):
        doc=validate_document(read_json(SOURCE/'calendars/2026.json'))
        self.assertEqual((str(doc['start']),str(doc['end'])),('2026-01-01','2026-12-31'))
        self.assertEqual(len(doc['days']['SH']),242)

    def test_unknown_dedup_shared_cycle_monitor_across_days(self):
        self.no_bundle();self.clock=datetime(2026,9,14,10,tzinfo=CHINA)
        for i in range(6):
            self.cycle();watch(self.cfg,False,self.clock)
            self.clock+=timedelta(hours=1)
        self.clock+=timedelta(days=1)
        self.cycle();watch(self.cfg,False,self.clock)
        stats=notification_stats(self.root/'runtime/monitor.sqlite3')
        self.assertEqual(stats['pending'],1)
        self.assertEqual(self.broker.connected,0)

    def test_refresh_timestamp_alone_does_not_create_new_unknown_event(self):
        self.no_bundle()
        for stamp in ['2026-09-14T10:00:00+08:00','2026-09-14T11:00:00+08:00']:
            atomic_json(self.root/'state/calendar.json',self.history(['2026-09-10','2026-09-11'],stamp))
            enqueue_calendar_issue(self.cfg,CalendarService(self.cfg).decide('2026-09-14'))
        self.assertEqual(notification_stats(self.root/'runtime/monitor.sqlite3')['pending'],1)

    def provider(self, days):
        fake=Mock()
        fake.get_trading_dates.return_value=[int(datetime.fromisoformat(d+'T00:00:00+08:00').timestamp()*1000) for d in days]
        return fake

    def test_data_only_refresh_can_bootstrap_without_account(self):
        self.no_bundle()
        fake=self.provider(['2026-09-10','2026-09-11','2026-09-14'])
        result=refresh_qmt(self.cfg,'2026-09-14',fake,now=datetime(2026,9,14,10,tzinfo=CHINA))
        self.assertEqual(result['covered_through'],'2026-09-14')
        self.assertEqual(CalendarService(self.cfg).decide('2026-09-14').status,OPEN)
        fake.connect.assert_not_called();fake.download_holiday_data.assert_not_called()
        self.assertEqual(fake.get_trading_dates.call_count,2)

    def test_failed_refresh_preserves_cache_bytes(self):
        path=self.root/'state/calendar.json';path.write_text('existing bytes')
        fake=self.provider([])
        with self.assertRaises(ValueError):refresh_qmt(self.cfg,'2026-09-14',fake,now=datetime(2026,9,14,10,tzinfo=CHINA))
        self.assertEqual(path.read_text(),'existing bytes')

    def test_explicit_future_download_requires_exact_annual_agreement(self):
        fake=self.provider(['2026-09-10','2026-09-11','2026-09-14'])
        doc=validate_document(read_json(SOURCE/'calendars/2026.json'))
        fake.get_trading_calendar.return_value=[x.replace('-','') for x in sorted(doc['days']['SH'])]
        result=refresh_qmt(self.cfg,'2026-09-14',fake,True,datetime(2026,9,14,10,tzinfo=CHINA))
        self.assertEqual(result['covered_through'],'2026-09-14') # NOT December 31
        self.assertIn('matched',result['future_check'])
        fake.download_holiday_data.assert_called_once()
        fake.get_trading_calendar.return_value=['20260105']
        with self.assertRaises(ValueError):refresh_qmt(self.cfg,'2026-09-14',fake,True,datetime(2026,9,14,10,tzinfo=CHINA))

    def test_refresh_rejects_seconds_or_future_timestamps(self):
        for values in ([1789344000],[True],[9999999999999]):
            fake=Mock();fake.get_trading_dates.return_value=values
            with self.subTest(values=values),self.assertRaises(ValueError):
                refresh_qmt(self.cfg,'2026-09-14',fake,now=datetime(2026,9,14,10,tzinfo=CHINA))

    def test_unknown_refresh_has_timeout_throttle_and_no_account_arguments(self):
        self.no_bundle()
        runner=Mock(side_effect=subprocess.TimeoutExpired(['fake'],20))
        now=datetime(2026,9,14,10,tzinfo=CHINA)
        for _ in range(3):self.assertEqual(ensure_calendar(self.cfg,'2026-09-14',now,runner).status,UNKNOWN)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.kwargs['timeout'],20)
        self.assertNotIn(self.cfg['account_id'],str(runner.call_args))
        self.assertNotIn('private-hook',str(runner.call_args))

    def test_known_weekend_does_not_try_calendar_refresh(self):
        runner=Mock()
        self.assertEqual(ensure_calendar(self.cfg,'2026-09-12',runner=runner).status,CLOSED)
        runner.assert_not_called()

    def test_import_requires_review_and_can_repair_corrupted_pack_preserving_it(self):
        source=SOURCE/'calendars/2026.json'
        with self.assertRaises(ValueError):import_annual(self.cfg,source)
        target=self.root/'state/calendars/2026.json';target.parent.mkdir(parents=True);target.write_bytes(b'{broken')
        import_annual(self.cfg,source,True)
        self.assertEqual(len(list((self.root/'state/calendar-backups').glob('*.json'))),1)
        self.assertEqual(next((self.root/'state/calendar-backups').glob('*.json')).read_bytes(),b'{broken')

    def old_error(self, day='2026-09-12'):
        self.store.event('old-error:'+day,f'{day} 自动打新V3【实盘】\n连接/查询未完成（RuntimeError）；仅未发起过的项目允许后续尝试。')

    def test_suppress_false_closed_error_not_delivery_and_never_delete(self):
        self.old_error()
        self.store.event('real-result','2026-09-11 301111.SZ 已提交待确认：101')
        before=self.store.db.execute('SELECT id,content,delivered FROM outbox ORDER BY id').fetchall()
        self.assertEqual(suppress_closed_alerts(self.store,self.cfg),1)
        after=self.store.db.execute('SELECT id,content,delivered FROM outbox ORDER BY id').fetchall()
        self.assertEqual([tuple(x) for x in before],[tuple(x) for x in after])
        sent=[];self.store.drain(sent.append,sleep=lambda _:None)
        self.assertEqual(len(sent),1);self.assertIn('已提交待确认',sent[0])
        self.assertEqual(suppress_closed_alerts(self.store,self.cfg),0)

    def test_any_intent_on_closed_day_preserves_availability_alert(self):
        self.old_error()
        with self.store.transaction():self.store.db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?)',('any','2026-09-12','301111.SZ','UNCERTAIN',None,500,0))
        self.assertEqual(suppress_closed_alerts(self.store,self.cfg),0)

    def test_trading_day_or_unknown_day_errors_never_suppressed(self):
        self.old_error('2026-09-11');self.old_error('2027-01-04')
        self.assertEqual(suppress_closed_alerts(self.store,self.cfg),0)

    def test_ambiguous_multi_page_or_order_combined_message_never_suppressed(self):
        self.store.event('combined','2026-09-12 自动打新V3【实盘】\n连接/查询未完成（RuntimeError）；仅未发起过的项目允许后续尝试。\n已有委托101')
        self.assertEqual(suppress_closed_alerts(self.store,self.cfg),0)

    def test_weekend_cycle_dispatch_has_readable_receipt_and_ui_state(self):
        result=dispatch('cycle',self.cfg,False,False,broker_factory=lambda _:self.broker,now=lambda:self.clock)
        self.assertEqual(result['result']['phase'],CLOSED)
        self.assertEqual(self.broker.connected,0)
        self.assertIn('非交易日，已跳过',describe_run(result))
        self.assertEqual(PHASES[CLOSED],'非交易日，已跳过')
        controller=Mock()
        controller.call.return_value={'ok':True,'control_dir':self.cfg['control_dir'],'state_dir':self.cfg['state_dir'],'markets':['SH','SZ']}
        with patch('panel_backend.china_now',return_value=self.clock):snap=Backend(self.root,controller).snapshot()
        self.assertEqual(snap['calendar']['status'],CLOSED)

    def test_history_prior_error_not_rewritten_on_closed_day(self):
        path=self.root/'runtime/daily/2026-09-12-live.json'
        atomic_json(path,{'day':'2026-09-12','mode':'live','account_digest':hashlib.sha256(self.cfg['account_id'].encode()).hexdigest()[:24],
                         'phase':'retryable_error','completed':False,'attempts':65,'items':{},'errors':['历史失败，不改写']})
        result=self.cycle()
        self.assertEqual(result['attempts'],65)
        self.assertEqual(result['errors'],['历史失败，不改写'])
        self.assertFalse(result['completed'])

    def test_config_validation_does_not_send_or_import_sdk(self):
        with patch('diagnostics.wecom_sender') as sender:
            report=configuration_report(self.cfg,False)
        sender.return_value.assert_not_called()
        self.assertFalse(report['message_sent'])
        self.assertNotIn('fake-account',json.dumps(report))
        self.assertNotIn('key=fake',json.dumps(report))

    def test_notification_test_requires_explicit_confirmation(self):
        fake=Mock()
        with self.assertRaises(ValueError):send_test(self.cfg,False,fake)
        fake.assert_not_called()
        self.assertEqual(send_test(self.cfg,True,fake)['submission_calls'],0)
        fake.return_value.assert_called_once()

    def test_redaction_removes_webhook_and_account(self):
        result=redact('account fake-account-13579 failed https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake password=foo',self.cfg)
        for value in ('fake-account-13579','key=fake','foo'):self.assertNotIn(value,result)

    def test_archive_receipts_losslessly_without_touching_ledger(self):
        old=self.root/'runtime/runs/2026-01-01';old.mkdir(parents=True)
        name='cycle-'+'a'*32+'.json'
        receipt={'action':'cycle','status':'finished','result':{'phase':'pending','items':{'301111.SZ':{'status':'UNCERTAIN'}}}}
        atomic_json(old/name,receipt)
        original=(old/name).read_bytes()
        before=self.store.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0]
        maintain(self.cfg,'2026-09-12')
        archives=list((self.root/'runtime/archive/runs').rglob('*.zip'))
        self.assertEqual(len(archives),1)
        with zipfile.ZipFile(archives[0]) as z:self.assertEqual(z.read(name),original)
        self.assertEqual(len(load_runs(self.root/'runtime','2026-01-01')),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],before)

    def test_backup_retention_keeps_verified_minimum_not_live_ledger(self):
        self.cfg['backup_keep']=2
        for i in range(4):backup(self.root/'state/state.sqlite3',self.root/'runtime/backups'/f'2026-09-0{i+1}-{i:08x}.sqlite3')
        maintain(self.cfg,'2026-09-12')
        self.assertEqual(len(list((self.root/'runtime/backups').glob('*.sqlite3'))),2)
        self.assertTrue((self.root/'state/state.sqlite3').is_file())

    def test_new_install_disabled_upgrade_preserves_config_ledger_and_history(self):
        root=self.root/'安装 根'
        result=new_install(SOURCE,root)
        self.assertEqual(result['status'],'installed_disabled')
        config=read_json(root/'config.json');self.assertFalse(config['enable_execution'])
        config['account_id']='private-account';config['enable_execution']=True
        # The upgraded instance deliberately references an old/external ledger.
        config['state_dir']=self.cfg['state_dir'];config['webhook_file']=self.cfg['webhook_file']
        atomic_json(root/'config.json',config)
        (root/'runtime/untouched-history.json').write_text('audit')
        cfg=(root/'config.json').read_bytes();ledger=(self.root/'state/state.sqlite3').read_bytes();hook=(self.root/'private-hook').read_bytes()
        with self.assertRaises(ValueError):upgrade(SOURCE,root)
        result=upgrade(SOURCE,root,True)
        self.assertTrue(result['ledger_preserved'])
        self.assertEqual((root/'config.json').read_bytes(),cfg)
        self.assertEqual((self.root/'state/state.sqlite3').read_bytes(),ledger)
        self.assertEqual((self.root/'private-hook').read_bytes(),hook)
        self.assertEqual((root/'runtime/untouched-history.json').read_text(),'audit')
        rollback(root,result['rollback_id'],True)
        self.assertEqual((root/'config.json').read_bytes(),cfg)
        self.assertEqual((self.root/'state/state.sqlite3').read_bytes(),ledger)

    def test_missing_upgrade_ledger_rejected_not_recreated(self):
        root=self.root/'existing';new_install(SOURCE,root)
        ledger=root/'state/state.sqlite3';ledger.unlink()
        with self.assertRaises(ValueError):upgrade(SOURCE,root,True)
        self.assertFalse(ledger.exists())

    def test_corrupted_delivery_manifest_hash_rejected(self):
        target=self.root/'bad-source';target.mkdir()
        manifest={'schema_version':1,'files':{'app.py':'0'*64}}
        atomic_json(target/'DELIVERY_MANIFEST.json',manifest);(target/'app.py').write_text('bad')
        with self.assertRaises(ValueError):payload(target)

    def test_unicode_spaces_command_uses_argument_list_no_shell(self):
        root=self.root/'含 空格';root.mkdir()
        atomic_json(root/'config.json',self.cfg)
        args=launch_command(root,'cycle')
        self.assertEqual(args[3],'cycle')
        self.assertIn(str(root/'config.json'),args)
        fake=Mock(return_value=Mock(returncode=0))
        launch(root,'notify',fake)
        self.assertNotIn('shell',fake.call_args.kwargs)
        self.assertEqual(fake.call_args.kwargs['timeout'],180)
        self.assertEqual(fake.call_args.kwargs['cwd'], root.resolve())
        self.assertEqual(fake.call_args.args[0], launch_command(root, 'notify'))
        # Exercise the documented PS5.1 -File entry, not only dot-sourcing.
        # A source==destination rejection happens before any installation/task mutation.
        import sys
        if sys.platform == 'win32':
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', str(SOURCE/'install.ps1'), '-Mode', 'New', '-Root', str(SOURCE)],
                capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Source and installation must be separate directories', result.stderr)
        cmd=WindowsController(root).command('Snapshot')
        self.assertIn('-EncodedCommand',cmd)
        with self.assertRaises(ValueError):validate_root(str(root)+'"bad')

    def test_notify_and_preview_cannot_recreate_lost_ledger(self):
        cfg=dict(self.cfg,state_dir=str(self.root/'lost-ledger'))
        for action in ('notify','cycle','backup'):
            with self.subTest(action=action),self.assertRaises(ValueError):
                dispatch(action,cfg,broker_factory=lambda _:self.broker)
        self.assertFalse((self.root/'lost-ledger/state.sqlite3').exists())

    def test_disabled_or_maintenance_launcher_does_not_run_worker(self):
        root=self.root/'disabled';root.mkdir()
        config=dict(self.cfg,enable_execution=False);atomic_json(root/'config.json',config)
        fake=Mock()
        self.assertEqual(launch(root,'cycle',fake)['status'],'not_activated')
        atomic_json(root/'maintenance.json',{})
        self.assertEqual(launch(root,'notify',fake)['status'],'maintenance_skip')
        fake.assert_not_called()


if __name__ == '__main__':unittest.main(verbosity=2)
