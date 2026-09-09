"""End-to-end business simulations. No real SDK calls, sockets or notifications."""
from datetime import datetime,timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,MagicMock,patch

from app import dispatch,inspect_health
from coordinator import Coordinator,Ledger,submit_window
from runtime import atomic_json,backup,single_instance
from support import CHINA,Store,build_plan,wecom_sender

DATE='2026-09-09'
OPEN=datetime(2026,9,9,9,35,tzinfo=CHINA)


def info(kind='STOCK',**kw):
    return dict({'type':kind,'name':'模拟申购','purchaseDate':'20260909','issuePrice':100 if kind=='BOND' else 15,
                 'minPurchaseNum':10 if kind=='BOND' else 500,'maxPurchaseNum':10000 if kind=='BOND' else 65000},**kw)


class FakeBroker:
    def __init__(self):
        self.items={'301111.SZ':info()}; self.limit={'SH':20000,'SZ':17500,'KCB':5000}
        self.order_rows=[]; self.calls=[]; self.connected=0; self.closed=0
        self.connect_error=False; self.ready_error=False; self.query_error=False
        self.limits_error=False; self.submit_error=False; self.publish=True; self.order_id=101
        self.is_trade=True; self.on_submit=None; self.on_ready=None
    def connect(self):
        self.connected+=1
        if self.connect_error: raise RuntimeError('fake connect failure')
    def close(self): self.closed+=1
    def is_trading_day(self,day): return self.is_trade
    def ready(self):
        if self.on_ready: self.on_ready()
        if self.ready_error: raise RuntimeError('fake account offline')
    def ipos(self): return self.items
    def limits(self):
        if self.limits_error: raise TimeoutError('fake quota timeout')
        return self.limit
    def orders(self):
        if self.query_error: raise TimeoutError('fake query failure')
        return list(self.order_rows)
    def drain_errors(self): return []
    def submit(self,plan):
        self.calls.append(plan)
        if self.on_submit: self.on_submit(plan)
        if self.publish:
            self.order_rows.append({'code':plan.code,'day':plan.day,'order_id':self.order_id,
                                    'status':'reported','remark':plan.remark})
        if self.submit_error: raise TimeoutError('fake lost response')
        return self.order_id


class DailySystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.clock=OPEN; self.broker=FakeBroker()
        self.cfg={'account_id':'fake','qmt_userdata':str(self.root/'userdata_mini'),
                  'state_dir':str(self.root/'shared'),'control_dir':str(self.root/'control'),
                  'webhook_file':str(self.root/'hook'),'allowed_markets':['SH','SZ'],'enable_execution':True}
        (self.root/'hook').write_text('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake',encoding='utf-8')
        self.ledger=Ledger(self.root/'shared'/'state.sqlite3')
        self.net=patch('socket.socket',side_effect=AssertionError('Network forbidden in tests'))
        self.net.start()
    def tearDown(self):
        self.net.stop(); self.ledger.close(); self.tmp.cleanup()
    def run_cycle(self,mode='live'):
        return Coordinator(self.cfg,self.broker,self.ledger,lambda:self.clock,mode).run()

    def test_preopen_start_then_scheduler_open_then_repeated_wakeup(self):
        self.clock=OPEN.replace(hour=8,minute=44)
        self.assertEqual(self.run_cycle()['phase'],'waiting_open')
        self.assertEqual(self.broker.connected,0)
        self.clock=OPEN
        self.assertTrue(self.run_cycle()['completed'])
        self.clock+=timedelta(minutes=5)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_next_day_runs_without_new_manual_confirmation(self):
        self.run_cycle()
        self.clock+=timedelta(days=1)
        self.broker.items={'301222.SZ':info(purchaseDate='20260910')}
        self.broker.order_rows=[]
        self.run_cycle()
        self.assertEqual(len(self.broker.calls),2)
        self.assertEqual(self.broker.calls[-1].day,'2026-09-10')

    def test_qmt_not_ready_then_recovers_and_catches_up(self):
        self.broker.ready_error=True
        self.assertEqual(self.run_cycle()['phase'],'retryable_error')
        self.assertEqual(self.broker.calls,[])
        self.broker.ready_error=False
        self.clock+=timedelta(minutes=10)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_transport_failure_before_intent_is_retryable(self):
        self.broker.connect_error=True
        self.run_cycle()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)
        self.broker.connect_error=False
        self.clock+=timedelta(minutes=5)
        self.run_cycle()
        self.assertEqual(len(self.broker.calls),1)

    def test_missed_morning_wakeup_can_catch_up_later(self):
        self.clock=OPEN.replace(hour=10,minute=20)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_stock_and_bond_and_fixed_bond_cap(self):
        self.broker.items['754810.SH']=info('BOND',maxPurchaseNum=20000)
        self.run_cycle()
        self.assertEqual([p.quantity for p in self.broker.calls],[17500,10000])

    def test_bond_does_not_depend_on_stock_quota(self):
        self.broker.limits_error=True
        self.broker.items['754810.SH']=info('BOND')
        result=self.run_cycle()
        self.assertFalse(result['completed'])
        self.assertEqual([p.kind for p in self.broker.calls],['BOND'])
        self.broker.limits_error=False; self.clock+=timedelta(minutes=5)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual([p.kind for p in self.broker.calls],['BOND','STOCK'])

    def test_zero_quota_skip_no_false_submission(self):
        self.broker.limit={'SZ':0}
        result=self.run_cycle()
        self.assertTrue(result['completed'])
        self.assertEqual(result['items']['301111.SZ']['status'],'SKIPPED_QUOTA')
        self.assertEqual(self.broker.calls,[])

    def test_issuance_cap_and_integer_lot(self):
        plan=build_plan('787001.SH',info(maxPurchaseNum=2700),{'KCB':5000},DATE,['KCB'])
        self.assertEqual(plan.quantity,2500)

    def test_preview_does_not_consume_live_daily_completion(self):
        self.assertTrue(self.run_cycle('preview')['completed'])
        self.assertEqual(self.broker.calls,[])
        self.assertTrue(self.run_cycle('live')['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_old_v2_intent_survives_upgrade(self):
        engine=Coordinator(self.cfg,self.broker,self.ledger,lambda:self.clock)
        plan=build_plan('301111.SZ',info(),self.broker.limit,DATE)
        self.ledger.reserve(engine.account,plan)
        self.assertEqual(self.run_cycle()['items']['301111.SZ']['status'],'INTENT')
        self.assertEqual(self.broker.calls,[])

    def test_intent_durable_before_submit(self):
        def inspect(plan):
            other=sqlite3.connect(self.root/'shared'/'state.sqlite3')
            try: self.assertEqual(other.execute('SELECT state FROM intents').fetchone()[0],'INTENT')
            finally: other.close()
        self.broker.on_submit=inspect
        self.run_cycle()

    def test_restart_after_crash_does_not_submit_twice(self):
        self.run_cycle(); self.ledger.close()
        self.ledger=Ledger(self.root/'shared'/'state.sqlite3')
        self.clock+=timedelta(minutes=5)
        self.run_cycle()
        self.assertEqual(len(self.broker.calls),1)

    def test_lost_reply_with_visible_order_reconciles(self):
        self.broker.submit_error=True
        self.assertTrue(self.run_cycle()['completed'])
        self.clock+=timedelta(minutes=5); self.run_cycle()
        self.assertEqual(len(self.broker.calls),1)

    def test_lost_reply_without_visibility_never_retries(self):
        self.broker.submit_error=True; self.broker.publish=False
        self.run_cycle(); self.clock+=timedelta(minutes=5)
        self.assertEqual(self.run_cycle()['items']['301111.SZ']['status'],'UNCERTAIN')
        self.assertEqual(len(self.broker.calls),1)

    def test_delayed_order_appears_on_later_cycle(self):
        self.broker.publish=False; self.run_cycle()
        plan=self.broker.calls[0]
        self.broker.order_rows=[{'code':plan.code,'day':DATE,'order_id':101,'status':'reported','remark':plan.remark}]
        self.clock+=timedelta(minutes=5)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_rejected_return_does_not_reissue(self):
        self.broker.order_id=-1; self.broker.publish=False
        self.run_cycle(); self.clock+=timedelta(minutes=5); self.run_cycle()
        self.assertEqual(len(self.broker.calls),1)

    def test_existing_manual_order_skipped(self):
        self.broker.order_rows=[{'code':'301111.SZ','day':DATE,'status':'reported','order_id':9,'remark':'manual'}]
        self.assertEqual(self.run_cycle()['items']['301111.SZ']['status'],'EXTERNAL_ACCEPTED')
        self.assertEqual(self.broker.calls,[])

    def test_current_bj_zero_date_record_is_not_a_sh_sz_order(self):
        self.broker.items={'920229.BJ':info(purchaseDate='0',minPurchaseNum=100,maxPurchaseNum=467500,issuePrice=15.67)}
        result=self.run_cycle()
        self.assertEqual(result['items']['920229.BJ']['status'],'SKIPPED_SCOPE')
        self.assertEqual(self.broker.calls,[])

    def test_current_bj_record_does_not_block_valid_sh_sz_bond(self):
        self.broker.items['920229.BJ']=info(purchaseDate='0')
        self.broker.items['754810.SH']=info('BOND')
        self.run_cycle()
        self.assertEqual([p.code for p in self.broker.calls],['301111.SZ','754810.SH'])

    def test_existing_rejection_blocks_automatic_resubmission(self):
        self.broker.order_rows=[{'code':'301111.SZ','day':DATE,'status':'rejected','order_id':9,'remark':'manual'}]
        self.assertFalse(self.run_cycle()['completed'])
        self.assertEqual(self.broker.calls,[])

    def test_final_reconciliation_detects_late_rejection(self):
        self.run_cycle(); self.broker.order_rows[0]['status']='rejected'
        self.clock=OPEN.replace(hour=15,minute=5)
        self.assertEqual(self.run_cycle()['phase'],'final_attention')
        self.assertEqual(len(self.broker.calls),1)

    def test_two_empty_observations_are_required(self):
        self.broker.items={}
        self.assertEqual(self.run_cycle()['phase'],'waiting_data')
        self.clock+=timedelta(seconds=30)
        self.assertEqual(self.run_cycle()['phase'],'waiting_data')
        self.clock+=timedelta(minutes=5)
        self.assertEqual(self.run_cycle()['phase'],'no_ipo')

    def test_empty_after_partial_run_does_not_erase_candidates(self):
        self.broker.publish=False; self.run_cycle(); self.broker.items={}
        self.clock+=timedelta(minutes=5)
        result=self.run_cycle()
        self.assertIn('301111.SZ',result['items']); self.assertFalse(result['completed'])

    def test_missing_metadata_is_retryable_and_not_submitted(self):
        self.broker.items['301111.SZ']['purchaseDate']=None
        self.assertEqual(self.run_cycle()['items']['301111.SZ']['status'],'RETRYABLE')
        self.assertEqual(self.broker.calls,[])
        self.broker.items['301111.SZ']['purchaseDate']='20260909'
        self.clock+=timedelta(minutes=5)
        self.assertTrue(self.run_cycle()['completed'])

    def test_nontrading_day_and_lunch(self):
        self.broker.is_trade=False
        self.assertEqual(self.run_cycle()['phase'],'market_closed')
        self.assertEqual(self.broker.calls,[])
        self.clock=OPEN.replace(hour=12,minute=0)
        self.assertEqual(self.run_cycle()['phase'],'market_closed')

    def test_lunch_queries_without_intent_then_afternoon_can_submit(self):
        self.clock=OPEN.replace(hour=12,minute=0)
        result=self.run_cycle()
        self.assertEqual(result['phase'],'waiting_afternoon')
        self.assertTrue(result['last_activity']['queried'])
        self.assertEqual(result['last_activity']['candidate_count'],1)
        self.assertEqual(result['items']['301111.SZ']['status'],'WAITING_WINDOW')
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)
        self.clock=OPEN.replace(hour=13,minute=0)
        self.assertTrue(self.run_cycle()['completed'])
        self.assertEqual(len(self.broker.calls),1)

    def test_lunch_bj_has_explicit_permission_reason(self):
        self.clock=OPEN.replace(hour=12,minute=0)
        self.broker.items={'920229.BJ':info(purchaseDate='0')}
        result=self.run_cycle()
        self.assertEqual(result['items']['920229.BJ']['reason'],'北交所无权限，已跳过')
        self.assertEqual(result['last_activity']['scope_skipped'],1)
        self.assertFalse(result['completed'])
        self.assertEqual(self.broker.calls,[])
        self.clock=OPEN.replace(hour=13,minute=0)
        self.assertEqual(self.run_cycle()['phase'],'no_eligible')
        self.assertEqual(self.broker.calls,[])

    def test_valid_lunch_wait_does_not_raise_incomplete_alarm(self):
        self.clock=OPEN.replace(hour=12,minute=0)
        self.run_cycle()
        atomic_json(self.root/'shared'/'calendar.json',{'days':[DATE],'covered_through':'2026-12-31'})
        atomic_json(self.root/'control'/'latest-cycle-live.json',{'day':DATE,'status':'finished','started_at':self.clock.isoformat()})
        self.assertNotIn('daily_work_incomplete',inspect_health(self.cfg,self.clock)['issues'])
        later=self.clock.replace(hour=13,minute=10)
        self.assertIn('daily_work_incomplete',inspect_health(self.cfg,later)['issues'])

    def test_submission_window_is_not_widened(self):
        self.assertFalse(submit_window(OPEN.replace(hour=12,minute=0)))
        self.assertTrue(submit_window(OPEN.replace(hour=13,minute=0)))
        self.assertFalse(submit_window(OPEN.replace(hour=15,minute=30)))

    def test_cutoff_and_readiness_call_crossing_cutoff(self):
        ready_calls=[0]
        def advance():
            ready_calls[0]+=1
            if ready_calls[0]==2: self.clock=OPEN.replace(hour=15,minute=1)
        self.broker.on_ready=advance
        self.run_cycle()
        self.assertEqual(self.broker.calls,[])

    def test_final_cold_start_reports_missed_not_success(self):
        self.clock=OPEN.replace(hour=15,minute=5)
        self.assertEqual(self.run_cycle()['items']['301111.SZ']['status'],'MISSED')
        self.assertEqual(self.broker.calls,[])

    def test_query_failure_never_becomes_no_ipo(self):
        self.broker.query_error=True
        self.assertEqual(self.run_cycle()['phase'],'retryable_error')
        self.assertEqual(self.broker.calls,[])

    def test_live_disabled_rejected_before_broker(self):
        self.cfg['enable_execution']=False
        with self.assertRaises(ValueError): self.run_cycle()
        self.assertEqual(self.broker.connected,0)

    def test_notifications_are_not_duplicated_after_completed_run(self):
        self.run_cycle(); count=self.ledger.pending_notifications()
        self.clock+=timedelta(minutes=5); self.run_cycle()
        self.assertEqual(self.ledger.pending_notifications(),count)

    def test_worker_dispatch_persists_full_receipt(self):
        with patch('app.wecom_sender',return_value=lambda _:None):
            result=dispatch('cycle',self.cfg,True,True,broker_factory=lambda _:self.broker,now=lambda:self.clock)
        self.assertEqual(result['status'],'finished')
        self.assertEqual(result['mode'],'live')
        self.assertEqual(result['result']['items']['301111.SZ']['status'],'REPORTED')

    def test_missing_live_ledger_is_not_silently_recreated(self):
        self.cfg['state_dir']=str(self.root/'missing-ledger')
        with self.assertRaises(ValueError):
            dispatch('cycle',self.cfg,True,True,broker_factory=lambda _:self.broker,now=lambda:self.clock)
        self.assertEqual(self.broker.connected,0)
        self.assertFalse((self.root/'missing-ledger'/'state.sqlite3').exists())

    def test_monitor_detects_no_daily_run_and_respects_activation(self):
        atomic_json(self.root/'shared'/'calendar.json',{'days':[DATE],'covered_through':'2026-12-31'})
        result=inspect_health(self.cfg,OPEN.replace(hour=10))
        self.assertIn('daily_run_missing',result['issues'])
        self.cfg['enable_execution']=False
        self.assertEqual(inspect_health(self.cfg,OPEN)['status'],'not_activated')

    def test_backup_matches_ledger(self):
        self.run_cycle(); target=self.root/'backup.sqlite3'
        backup(self.root/'shared'/'state.sqlite3',target)
        copy=sqlite3.connect(target)
        try:self.assertEqual(copy.execute('SELECT COUNT(*) FROM intents').fetchone()[0],1)
        finally:copy.close()

    def test_shared_lock_excludes_second_process_entry(self):
        with single_instance(self.root/'shared'/'instance.lock'):
            with self.assertRaises(OSError):
                with single_instance(self.root/'shared'/'instance.lock'): pass


class NotificationTests(unittest.TestCase):
    def test_wecom_http_200_business_error_is_failure(self):
        opener=MagicMock(); response=opener.open.return_value.__enter__.return_value
        response.status=200; response.read.return_value=b'{"errcode":93000}'
        sender=wecom_sender('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake',opener)
        with self.assertRaises(RuntimeError):sender('test')

    def test_failed_outbox_persists_and_retries_without_orders(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.sqlite3'; store=Store(path)
            store.event('x','消息'*2000)
            rows=store.db.execute('SELECT content FROM outbox').fetchall()
            self.assertTrue(all(len(r[0].encode())<=2000 for r in rows))
            fail=Mock(side_effect=TimeoutError())
            store.drain(fail,now=lambda:100,sleep=lambda _:None); store.close()
            store=Store(path)
            try:
                good=Mock(); store.drain(good,now=lambda:110,sleep=lambda _:None)
                good.assert_not_called()
                self.assertEqual(store.drain(good,now=lambda:131,sleep=lambda _:None),0)
                n=good.call_count; store.drain(good,now=lambda:200,sleep=lambda _:None)
                self.assertEqual(good.call_count,n)
            finally:store.close()


if __name__=='__main__':unittest.main(verbosity=2)
