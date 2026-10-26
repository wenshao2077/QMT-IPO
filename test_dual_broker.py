"""Business/fault scenarios across two transports. No real QMT or message sends."""
import ast
from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from broker_factory import Broker, ReadOnlyBroker
from coordinator import Coordinator, Ledger
from instance_identity import task_names, task_prefix, mutex_name, initialize_prefix
from native_broker import NativeBroker
from native_qmt_bridge import NativeService, _orders, _calendar_open
from panel_backend import plan_state
from runtime import atomic_json, read_json, validate_config
from support import CHINA, build_plan

SOURCE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=CHINA)


class DualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'code/calendars').mkdir(parents=True)
        shutil.copyfile(SOURCE/'native_qmt_bridge.py', self.root/'code/native_qmt_bridge.py')
        shutil.copyfile(SOURCE/'calendars/2026.json', self.root/'code/calendars/2026.json')
        (self.root/'userdata').mkdir()
        self.cfg = {'account_id':'synthetic-account','task_prefix':'QmtIPO-Oriental','transport':'native',
                    'qmt_userdata':str(self.root/'userdata'),'native_bridge_dir':str(self.root/'native'),
                    'state_dir':str(self.root/'state'),'control_dir':str(self.root/'runtime'),
                    'webhook_file':str(self.root/'hook'),'allowed_markets':['SH','SZ'],'enable_execution':True}
        atomic_json(self.root/'config.json', self.cfg)
        atomic_json(self.root/'runtime/panel-authorization.json', {'authorized':True,'scope':'daily_stock_and_bond_ipo'})
        self.rows = []
        self.info = {'type':'STOCK','name':'合成新股','purchaseDate':'20261026',
                     'issuePrice':12.5,'minPurchaseNum':500,'maxPurchaseNum':20000}
        self.items = {'301999.SZ': self.info}
        self.api = {'get_trade_detail_data':Mock(side_effect=self.details),
                    'get_ipo_data':Mock(side_effect=lambda: self.items),
                    'get_new_purchase_limit':Mock(return_value={'SH':5000,'SZ':3500,'KCB':0}),
                    'passorder':Mock(side_effect=self.passorder)}
        self.clock = NOW
        self.service = NativeService(str(self.root), object(), self.api, now=lambda:self.clock)
        self.service.tick()
        self.broker = NativeBroker(self.cfg, clock=lambda:self.clock.timestamp(), sleep=lambda _:self.service.tick())
        self.ledger = Ledger(self.root/'state/state.sqlite3')
        self.plan = build_plan('301999.SZ', self.info, {'SZ':3500}, '2026-10-26', ['SH','SZ'])

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def details(self, account, kind, data):
        if data == 'ACCOUNT':
            return [SimpleNamespace(m_strAccountID=account,m_strStatus='正常',m_strTradingDate=self.clock.strftime('%Y%m%d'))]
        return self.rows

    def passorder(self, *args):
        self.rows.append(SimpleNamespace(m_strAccountID=self.cfg['account_id'],m_strInstrumentID=args[3][:6],
            m_strExchangeID=args[3][-2:],m_nOrderStatus=86,m_nRef=101,m_strInsertDate='20261026',
            m_strInsertTime='10:00:00',m_strRemark=args[9]))
        return None

    def request(self, operation='submit'):
        return dict(self.service._identity(),id='a'*32,created=self.clock.timestamp(),expires=self.clock.timestamp()+8,
                    operation=operation,calendar={'status':'trading_day','day':'2026-10-26'},
                    payload={'code':self.plan.code,'day':self.plan.day,'quantity':self.plan.quantity,
                             'price':self.plan.price,'remark':self.plan.remark})

    def cycle(self):
        with patch('native_broker.china_now', return_value=self.clock):
            return Coordinator(self.cfg,self.broker,self.ledger,lambda:self.clock,'live').run()

    def test_native_once_then_query_confirms_and_repeated_wakeups_never_resubmit(self):
        result=self.cycle()
        self.assertEqual(self.api['passorder'].call_count,1)
        self.assertEqual(result['items'][self.plan.code]['status'],'CONFIRMED')
        self.assertEqual(result['last_activity']['submitted'],1)
        self.cycle()
        self.assertEqual(self.api['passorder'].call_count,1)
        self.assertEqual(len(list((self.root/'native/claims').glob('*.json'))),1)

    def test_native_exception_after_call_cannot_resubmit_after_restart(self):
        def lost(*args):
            self.passorder(*args)
            raise TypeError('synthetic lost response')
        self.api['passorder'].side_effect=lost
        self.cycle()
        self.service=NativeService(str(self.root),object(),self.api,now=lambda:self.clock)
        self.service.tick()
        self.cycle()
        self.assertEqual(self.api['passorder'].call_count,1)

    def test_passorder_none_without_order_keeps_uncertain_and_no_retry(self):
        self.api['passorder'].side_effect=lambda *a:None
        result=self.cycle()
        self.assertEqual(result['items'][self.plan.code]['status'],'SUBMITTED')
        self.cycle()
        self.assertEqual(self.api['passorder'].call_count,1)

    def test_bond_ignores_stock_quota_and_uses_ten_thousand_bonds(self):
        self.items={'754999.SH':dict(self.info,type='BOND',issuePrice=100,minPurchaseNum=10,maxPurchaseNum=10000)}
        self.api['get_new_purchase_limit'].return_value={'SH':0,'SZ':0}
        self.cycle()
        args=self.api['passorder'].call_args.args
        self.assertEqual((args[3],args[5],args[6]),('754999.SH',100.0,10000))

    def test_readonly_broker_cannot_submit_even_if_config_live(self):
        with self.assertRaises(RuntimeError):
            NativeBroker(self.cfg,read_only=True).submit(self.plan)
        self.api['passorder'].assert_not_called()

    def test_durable_intent_required_before_transport(self):
        self.broker.connect()
        with self.assertRaisesRegex(RuntimeError,'durable_first_intent'):
            self.broker.submit(self.plan)
        self.assertFalse(list((self.root/'native/requests').glob('*.json')))

    def test_existing_order_blocks_even_if_other_remark(self):
        self.passorder(None,None,None,self.plan.code,None,None,None,None,None,'external')
        with self.assertRaisesRegex(ValueError,'existing_order'):
            self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_claim_rejects_new_request_uuid_after_service_restart(self):
        request=self.request()
        self.service.handle(request)
        self.rows=[]
        self.service=NativeService(str(self.root),object(),self.api,now=lambda:self.clock)
        new=self.request()
        new['id']='b'*32
        with self.assertRaises(FileExistsError):
            self.service.handle(new)
        self.assertEqual(self.api['passorder'].call_count,1)

    def test_expired_or_future_request_never_calls_native(self):
        for delta in (-20, 20):
            request=self.request()
            request['created']+=delta
            request['expires']+=delta
            with self.assertRaisesRegex(ValueError,'expired'):
                self.service.handle(request)
        self.api['passorder'].assert_not_called()

    def test_wrong_session_account_or_code_identity_rejected(self):
        for key in ('session','account_digest','code_id','task_prefix','qmt_pid'):
            request=self.request()
            request[key]='wrong'
            with self.assertRaisesRegex(ValueError,'identity'):
                self.service.handle(request)
        self.api['passorder'].assert_not_called()

    def test_paused_execution_or_missing_authority_blocks(self):
        atomic_json(self.root/'config.json',dict(self.cfg,enable_execution=False))
        with self.assertRaisesRegex(ValueError,'disabled'):
            self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_account_binding_change_requires_reload(self):
        atomic_json(self.root/'config.json',dict(self.cfg,account_id='other'))
        with self.assertRaisesRegex(ValueError,'binding_changed'):
            self.service.handle(self.request('ready'))

    def test_quantity_price_date_or_remark_drift_blocks(self):
        for key,value in (('quantity',4000),('price',12.6),('day','2026-10-27'),('remark','bad')):
            request=self.request()
            request['payload'][key]=value
            with self.assertRaises(ValueError):
                self.service.handle(request)
        self.api['passorder'].assert_not_called()

    def test_window_expires_during_native_queries(self):
        self.clock=NOW.replace(hour=14,minute=49,second=59)
        request=self.request()
        def late():
            self.clock+=timedelta(seconds=2)
            return self.items
        self.api['get_ipo_data'].side_effect=late
        with self.assertRaisesRegex(ValueError,'outside_submit_window'):
            self.service.handle(request)
        self.api['passorder'].assert_not_called()

    def test_market_disabled_during_order_query_prevents_call(self):
        def narrow(*args):
            value=self.details(*args)
            if args[-1]=='ORDER':
                atomic_json(self.root/'config.json',dict(self.cfg,allowed_markets=['SH']))
            return value
        self.api['get_trade_detail_data'].side_effect=narrow
        with self.assertRaises(ValueError):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_pause_or_scope_change_during_claim_fsync_keeps_claim_but_does_not_call(self):
        import native_qmt_bridge
        import json
        real=native_qmt_bridge.os.fsync
        def changed(fd):
            real(fd)
            (self.root/'runtime/panel-authorization.json').write_text(json.dumps({'authorized':False,'scope':'daily_stock_and_bond_ipo'}),'utf-8')
        with patch('native_qmt_bridge.os.fsync',side_effect=changed):
            with self.assertRaises(ValueError):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()
        self.assertEqual(len(list((self.root/'native/claims').glob('*.json'))),1)

    def test_scope_replaced_during_claim_fsync_prevents_call(self):
        import native_qmt_bridge
        import json
        real=native_qmt_bridge.os.fsync
        def changed(fd):
            real(fd)
            (self.root/'runtime/panel-authorization.json').write_text(json.dumps({'authorized':True,'scope':'unrelated'}),'utf-8')
        with patch('native_qmt_bridge.os.fsync',side_effect=changed):
            with self.assertRaises(ValueError):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_bad_account_state_or_broker_date_blocks(self):
        for status,day in (('登录失败','20261026'),('正常','20261023')):
            self.api['get_trade_detail_data'].side_effect=lambda *a:[SimpleNamespace(m_strAccountID=self.cfg['account_id'],m_strStatus=status,m_strTradingDate=day)]
            with self.assertRaises(ValueError):
                self.service.handle(self.request('ready'))

    def test_conflicting_optional_account_state_cannot_override_bad_status(self):
        self.api['get_trade_detail_data'].side_effect=lambda *a:[SimpleNamespace(m_strAccountID=self.cfg['account_id'],m_strStatus='正常',m_strTradingDate='20261026',m_Enable=False)]
        with self.assertRaisesRegex(ValueError,'state_conflict'):self.service.handle(self.request('ready'))

    def test_subsecond_after_last_submission_boundary_is_closed(self):
        self.clock=NOW.replace(hour=14,minute=50,microsecond=1)
        with self.assertRaisesRegex(ValueError,'outside_submit_window'):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_final_calendar_disk_read_cannot_cross_submission_cutoff(self):
        import native_qmt_bridge
        real=native_qmt_bridge._calendar_open
        self.clock=NOW.replace(hour=14,minute=50)
        calls=[]
        def late(*args):
            real(*args)
            calls.append(1)
            if len(calls)==3:self.clock+=timedelta(microseconds=1)
        with patch('native_qmt_bridge._calendar_open',side_effect=late):
            with self.assertRaisesRegex(ValueError,'outside_submit_window'):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_final_calendar_disk_read_cannot_cross_request_expiry(self):
        import native_qmt_bridge
        real=native_qmt_bridge._calendar_open
        calls=[]
        def late(*args):
            real(*args)
            calls.append(1)
            if len(calls)==3:self.clock+=timedelta(seconds=9)
        with patch('native_qmt_bridge._calendar_open',side_effect=late):
            with self.assertRaisesRegex(ValueError,'request_expired'):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()

    def test_order_bad_date_is_not_replaced_with_now(self):
        self.passorder(None,None,None,self.plan.code,None,None,None,None,None,'external')
        self.rows[0].m_strInsertDate=''
        with self.assertRaises(ValueError):
            _orders(self.rows,self.cfg['account_id'])

    def test_missing_remarks_and_nonpositive_reference_rows_are_retained(self):
        self.passorder(None,None,None,self.plan.code,None,None,None,None,None,None)
        self.rows[0].m_nRef=-1
        rows=_orders(self.rows,self.cfg['account_id'])
        self.assertEqual((len(rows),rows[0]['order_id'],rows[0]['remark_state']),(1,0,'missing'))

    def test_stale_or_cross_account_heartbeat_rejected(self):
        for key,value in (('at',NOW.timestamp()-10),('account_digest','other'),('code_id','other')):
            self.service.tick()
            beat=read_json(self.root/'native/heartbeat.json')
            beat[key]=value
            atomic_json(self.root/'native/heartbeat.json',beat)
            with self.assertRaises(RuntimeError):
                self.broker.connect()

    def test_cross_session_response_rejected(self):
        self.broker.connect()
        def wrong(_):
            self.service.tick()
            for path in (self.root/'native/responses').glob('*.json'):
                doc=read_json(path)
                doc['session']='wrong'
                atomic_json(path,doc)
        self.broker.sleep=wrong
        with self.assertRaises(RuntimeError):
            self.broker.ready()

    def test_calendar_weekend_holiday_unknown_and_bad_hash_block(self):
        for day in (datetime(2026,10,25,10,tzinfo=CHINA),datetime(2026,10,1,10,tzinfo=CHINA),datetime(2027,1,1,10,tzinfo=CHINA)):
            with self.assertRaises((ValueError,FileNotFoundError)):
                _calendar_open(str(self.root),{'status':'trading_day','day':day.date().isoformat()},day)
        path=self.root/'code/calendars/2026.json'
        doc=read_json(path);doc['sha256']='bad';atomic_json(path,doc)
        with self.assertRaises(ValueError):
            _calendar_open(str(self.root),self.request()['calendar'],NOW)

    def test_switching_to_sdk_retains_original_intent_and_never_resubmits(self):
        self.api['passorder'].side_effect=lambda *a:None
        self.cycle()
        from test_system import FakeBroker
        sdk=FakeBroker();sdk.items=self.items
        Coordinator(dict(self.cfg,transport='xtquant'),sdk,self.ledger,lambda:self.clock,'live').run()
        self.assertEqual(sdk.calls,[])

    def test_native_local_reference_collision_never_confirms_an_sdk_order(self):
        account=hashlib.sha256(self.cfg['account_id'].encode()).hexdigest()[:24]
        self.ledger.reserve(account,self.plan)
        self.ledger.record(account,self.plan.day,self.plan.code,'UNCERTAIN',101)
        self.passorder(None,None,None,self.plan.code,None,None,None,None,None,'manual')
        result=self.cycle()
        self.assertEqual(result['items'][self.plan.code]['status'],'UNCERTAIN')
        self.api['passorder'].assert_not_called()

    def test_native_exact_remark_can_reconcile_an_original_sdk_intent(self):
        account=hashlib.sha256(self.cfg['account_id'].encode()).hexdigest()[:24]
        self.ledger.reserve(account,self.plan)
        self.ledger.record(account,self.plan.day,self.plan.code,'UNCERTAIN',99)
        self.passorder(None,None,None,self.plan.code,None,None,None,None,None,self.plan.remark)
        result=self.cycle()
        self.assertEqual(result['items'][self.plan.code]['status'],'CONFIRMED')
        self.assertEqual(self.ledger.get(account,self.plan.day,self.plan.code)['order_id'],99)
        self.api['passorder'].assert_not_called()

    def test_instances_have_independent_names_mutexes_and_plan_state(self):
        self.assertNotEqual(task_names(self.cfg),task_names())
        self.assertNotEqual(mutex_name(self.root),mutex_name())
        snap={'ok':True,'task_prefix':'QmtIPO-Oriental','execution_enabled':False,
              'tasks':[{'name':n,'exists':True,'enabled':False} for n in task_names(self.cfg)]}
        self.assertEqual(plan_state(snap)[0],'disabled')
        snap['task_prefix']='QmtIPO-Northeast'
        self.assertEqual(plan_state(snap)[0],'inconsistent')

    def test_invalid_namespace_and_unknown_transport_rejected(self):
        for prefix in ('foreign','QmtIPO;evil','QmtIPO other'):
            with self.assertRaises(ValueError):task_prefix({'task_prefix':prefix})
        with self.assertRaises(ValueError):Broker(dict(self.cfg,transport='guess'))

    def test_legacy_instance_without_prefix_can_use_native_route(self):
        cfg=dict(self.cfg)
        cfg.pop('task_prefix')
        atomic_json(self.root/'config.json',cfg)
        service=NativeService(str(self.root),object(),self.api,now=lambda:self.clock)
        self.assertEqual(service.prefix,'QmtIPO3')

    def test_native_preparation_keeps_used_ledger_and_disabled_legacy_tasks(self):
        from prepare_native import prepare
        cfg=dict(self.cfg,enable_execution=False)
        cfg.pop('task_prefix')
        atomic_json(self.root/'config.json',cfg)
        self.ledger.reserve('old-account',self.plan)
        before=[tuple(r) for r in self.ledger.db.execute('SELECT * FROM intents')]
        snapshot={'ok':True,'execution_enabled':False,
            'tasks':[{'name':n,'exists':True,'enabled':False,'state':'Disabled'} for n in task_names(cfg)]}
        with patch('prepare_native.control_mutex',return_value=nullcontext()):
            result=prepare(self.root,confirmed=True,task_provider=lambda _:snapshot)
        self.assertTrue(result['ok'])
        self.assertEqual(before,[tuple(r) for r in self.ledger.db.execute('SELECT * FROM intents')])
        self.assertFalse(read_json(self.root/'config.json')['enable_execution'])

    def test_native_module_parses_as_python36_without_network_dependency(self):
        tree=ast.parse((SOURCE/'native_qmt_bridge.py').read_text('utf-8'),feature_version=(3,6))
        imports={node.names[0].name for node in ast.walk(tree) if isinstance(node,ast.Import)}
        self.assertFalse(imports & {'socket','socketserver','threading','xtquant','subprocess'})

    def test_fleet_monitor_keeps_broker_identity_and_reports_each_failure(self):
        from monitor_brokers import monitor
        manifest=self.root/'fleet.json'
        atomic_json(manifest,[{'id':'oriental','location':'local','root':str(self.root)},
                              {'id':'northeast','location':'ssh','host':'xiaohei','root':'C:/isolated IPO'}])
        runner=Mock(side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'id':'spoof'})),
                                 SimpleNamespace(returncode=2,stdout=json.dumps({'healthy':False}))])
        result=monitor(manifest,runner)
        self.assertFalse(result['ok'])
        self.assertEqual([r['id'] for r in result['brokers']],['oriental','northeast'])
        self.assertIn('"C:/isolated IPO/probe_qmt_trade.ps1"',runner.call_args_list[1].args[0][-1])
        self.assertEqual(result['submission_calls'],0)

    def test_fleet_monitor_rejects_healthy_json_from_a_failed_child(self):
        from monitor_brokers import monitor
        manifest=self.root/'fleet.json'
        atomic_json(manifest,[{'id':'northeast','location':'ssh','host':'xiaohei','root':'C:/test'}])
        result=monitor(manifest,Mock(return_value=SimpleNamespace(returncode=2,stdout='{"healthy":true}')))
        self.assertFalse(result['ok'])

    def test_oriental_result_card_names_its_broker_without_account_data(self):
        result=self.cycle()
        from notification_card import PREFIX
        contents=[r[0] for r in self.ledger.db.execute('SELECT content FROM outbox')]
        cards=[json.loads(c[len(PREFIX):]) for c in contents if c.startswith(PREFIX)]
        self.assertEqual(cards[0]['broker_label'],'东方证券')
        self.assertNotIn(self.cfg['account_id'],str(contents))

    def test_legacy_card_remains_valid_and_arbitrary_extra_fields_are_rejected(self):
        from notification_card import validate_card
        card={'day':'2026-10-26','items':[],'page':1,'pages':1}
        validate_card(card)
        validate_card(dict(card,broker_label='东方证券'))
        with self.assertRaises(ValueError):validate_card(dict(card,arbitrary=True))

    def test_namespace_cannot_be_changed_for_used_ledger(self):
        cfg=dict(self.cfg,enable_execution=False)
        atomic_json(self.root/'config.json',cfg)
        self.ledger.reserve('existing',self.plan)
        with self.assertRaises(ValueError):initialize_prefix(self.root,'QmtIPO-Northeast')

    def test_pending_address_cycle_retains_notifications_and_never_uses_old_hook(self):
        from app import dispatch
        cfg=dict(self.cfg,notification_mode='pending_address')
        (self.root/'hook').write_text('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=old-synthetic','utf-8')
        with patch('app.wecom_sender',side_effect=AssertionError('No destination authorized')), patch('native_broker.china_now',return_value=NOW):
            result=dispatch('cycle',cfg,live=True,send=True,broker_factory=lambda _:self.broker,now=lambda:NOW)
        self.assertEqual(result['status'],'finished')
        self.assertEqual(self.api['passorder'].call_count,1)
        self.assertEqual(result['result']['notification_delivery'],'pending_address')
        self.assertGreater(result['result']['pending_notifications'],0)
        self.assertEqual(self.ledger.db.execute('SELECT SUM(attempts) FROM outbox').fetchone()[0],0)

    def test_pending_address_notify_has_success_exit_without_losing_queue(self):
        from app import main
        import contextlib,io
        self.ledger.event('pending-destination','synthetic-message')
        atomic_json(self.root/'config.json',dict(self.cfg,notification_mode='pending_address'))
        with patch('app.wecom_sender',side_effect=AssertionError('No delivery')),contextlib.redirect_stdout(io.StringIO()):
            rc=main(['notify','--config',str(self.root/'config.json'),'--send'])
        self.assertEqual(rc,0)
        self.assertEqual(self.ledger.pending_notifications(),1)
        self.assertEqual(self.ledger.db.execute('SELECT attempts FROM outbox').fetchone()[0],0)

    def test_pending_address_monitor_never_sends_or_marks_delivery(self):
        from app import watch
        cfg=dict(self.cfg,notification_mode='pending_address')
        with patch('app.wecom_sender',side_effect=AssertionError('No delivery')):
            watch(cfg,send=True,now=NOW)
            report=watch(cfg,send=True,now=NOW+timedelta(minutes=5))
        self.assertEqual(report['notification_delivery'],'pending_address')
        import sqlite3
        db=sqlite3.connect(self.root/'runtime/monitor.sqlite3')
        try:self.assertEqual(db.execute('SELECT SUM(attempts) FROM outbox').fetchone()[0],0)
        finally:db.close()

    def test_pending_address_diagnostics_allow_empty_hook_and_show_deferral(self):
        from diagnostics import configuration_report,test_notification
        cfg=dict(self.cfg,notification_mode='pending_address')
        report=configuration_report(cfg,check_environment=False)
        self.assertTrue(report['ok'])
        self.assertEqual(report['notification_delivery'],'pending_address')
        with self.assertRaisesRegex(ValueError,'destination_pending'):test_notification(cfg,confirmed=True)
        self.assertIn('webhook_missing_or_invalid',configuration_report(self.cfg,check_environment=False)['issues'])

    def test_destination_resume_drains_existing_messages_without_resubmitting(self):
        from app import dispatch
        self.ledger.event('pending-destination','synthetic-message')
        (self.root/'hook').write_text('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=new-synthetic','utf-8')
        sender=Mock()
        with patch('app.wecom_sender',return_value=sender):
            result=dispatch('notify',dict(self.cfg,notification_mode='enabled'),send=True,now=lambda:NOW)
        self.assertEqual(result['status'],'finished')
        self.assertEqual(self.ledger.pending_notifications(),0)
        self.assertEqual(sender.call_count,1)
        self.api['passorder'].assert_not_called()

    def test_unknown_notification_mode_is_rejected(self):
        with self.assertRaises(ValueError):validate_config(dict(self.cfg,notification_mode='guess'))

    def test_future_activation_does_not_connect_or_reserve_today(self):
        cfg=dict(self.cfg,execution_start_date='2026-10-27')
        broker=Mock()
        result=Coordinator(cfg,broker,self.ledger,lambda:NOW,'live').run()
        self.assertEqual(result['phase'],'waiting_activation')
        broker.connect.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)

    def test_future_activation_does_not_raise_todays_missing_run_alarm(self):
        from app import watch,inspect_health
        cfg=dict(self.cfg,execution_start_date='2026-10-27',notification_mode='pending_address')
        watch(cfg,send=False,now=NOW)
        report=inspect_health(cfg,NOW)
        self.assertEqual(report['status'],'waiting_activation')
        self.assertNotIn('daily_run_missing',report['issues'])

    def test_native_independently_rejects_a_pre_activation_submission(self):
        atomic_json(self.root/'config.json',dict(self.cfg,execution_start_date='2026-10-27'))
        with self.assertRaisesRegex(ValueError,'disabled'):self.service.handle(self.request())
        self.api['passorder'].assert_not_called()
        self.assertFalse(list((self.root/'native/claims').glob('*.json')))

    def test_launcher_blocks_early_logon_even_if_scheduler_is_misconfigured(self):
        from launcher import run
        atomic_json(self.root/'config.json',dict(self.cfg,execution_start_date='2026-10-27'))
        runner=Mock()
        with patch('launcher.china_now',return_value=NOW):result=run(self.root,'cycle',runner)
        self.assertEqual(result['status'],'waiting_activation')
        runner.assert_not_called()

    def test_activation_date_is_strict_and_old_instances_remain_due(self):
        from runtime import execution_due
        for value in ('2026-2-7','invalid',True):
            with self.assertRaises((ValueError,TypeError)):validate_config(dict(self.cfg,execution_start_date=value))
        self.assertTrue(execution_due(self.cfg,'2026-10-26'))
        self.assertTrue(execution_due(dict(self.cfg,execution_start_date='2026-10-27'),'2026-10-27'))

    def test_rebind_changes_transport_only_while_paused_and_retains_used_intent(self):
        from rebind_client import rebind
        cfg=dict(self.cfg,enable_execution=False)
        atomic_json(self.root/'config.json',cfg)
        self.ledger.reserve('old-account',self.plan)
        before=[tuple(r) for r in self.ledger.db.execute('SELECT * FROM intents')]
        snapshot={'ok':True,'execution_enabled':False,'tasks':[
            {'name':n,'exists':True,'enabled':False,'state':'Disabled'} for n in task_names(cfg)]}
        result=rebind(self.root,self.root/'userdata',confirmed=True,transport='xtquant',
                      task_provider=lambda _:snapshot,mutex_factory=nullcontext)
        self.assertTrue(result['ok'])
        after=read_json(self.root/'config.json')
        self.assertEqual(after['transport'],'xtquant')
        self.assertEqual(after['account_id'],cfg['account_id'])
        self.assertEqual(after['state_dir'],cfg['state_dir'])
        self.assertEqual(before,[tuple(r) for r in self.ledger.db.execute('SELECT * FROM intents')])


if __name__=='__main__':unittest.main()
