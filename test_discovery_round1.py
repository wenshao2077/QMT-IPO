"""Bounded rediscovery integrated with the REAL coordinator and a fake broker."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import dispatch
from coordinator import Coordinator, Ledger
from runtime import atomic_json, read_json
from test_system import FakeBroker, info, OPEN


class RediscoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.clock=OPEN;self.broker=FakeBroker()
        self.cfg={'account_id':'FAKE_ROUND1','qmt_userdata':str(self.root/'userdata_mini'),
                  'state_dir':str(self.root/'state'),'control_dir':str(self.root/'runtime'),
                  'webhook_file':str(self.root/'hook'),'allowed_markets':['SH','SZ'],'enable_execution':True}
        self.ledger=Ledger(self.root/'state/state.sqlite3')
        self.net=patch('socket.socket',side_effect=AssertionError('Network forbidden'));self.net.start()

    def tearDown(self):
        self.net.stop();self.ledger.close();self.temp.cleanup()

    def run_cycle(self,mode='live'):
        return Coordinator(self.cfg,self.broker,self.ledger,lambda:self.clock,mode).run()

    def test_early_empty_completion_rediscovers_late_security(self):
        self.broker.items={};self.run_cycle();self.clock+=timedelta(minutes=5)
        result=self.run_cycle();self.assertTrue(result['completed'])
        self.assertTrue(result['next_due'].endswith('10:00:00+08:00'))
        self.clock=OPEN.replace(hour=9,minute=59);self.run_cycle();self.assertEqual(self.broker.connected,2)
        self.broker.items={'301111.SZ':info()};self.clock=OPEN.replace(hour=10,minute=0)
        self.run_cycle();self.assertEqual(len(self.broker.calls),1)

    def test_zero_quota_is_rechecked_without_reserving_zero_quantity(self):
        self.broker.limit={'SZ':0};self.run_cycle()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)
        self.broker.limit={'SZ':17500};self.clock=OPEN.replace(hour=10,minute=0)
        self.run_cycle();self.assertEqual(self.broker.calls[0].quantity,17500)
        self.clock+=timedelta(minutes=5);self.run_cycle();self.assertEqual(len(self.broker.calls),1)

    def test_completed_order_not_repeated_when_new_security_arrives(self):
        self.run_cycle();self.broker.items['301222.SZ']=info();self.broker.order_id=102
        self.clock=OPEN.replace(hour=10,minute=20);self.run_cycle()
        self.assertEqual([x.code for x in self.broker.calls],['301111.SZ','301222.SZ'])
        self.clock=OPEN.replace(hour=11,minute=0);self.run_cycle();self.assertEqual(len(self.broker.calls),2)

    def test_uncertain_intent_never_reissued_at_checkpoints(self):
        self.broker.publish=False;self.broker.submit_error=True;self.run_cycle()
        for hour,minute in [(10,0),(11,0),(13,0),(14,40)]:
            self.clock=OPEN.replace(hour=hour,minute=minute);self.run_cycle()
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.ledger.db.execute('SELECT state FROM intents').fetchone()[0],'UNCERTAIN')

    def test_preview_rediscovery_never_creates_an_intent(self):
        self.run_cycle('preview');self.clock=OPEN.replace(hour=10,minute=0);self.run_cycle('preview')
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0],0)

    def test_late_discovery_after_window_never_submits(self):
        self.broker.items={};self.run_cycle();self.clock+=timedelta(minutes=5);self.run_cycle()
        self.broker.items={'301111.SZ':info()};self.clock=OPEN.replace(hour=15,minute=5)
        self.run_cycle();self.assertEqual(self.broker.calls,[])

    def test_legacy_completed_receipt_requeries_without_duplicate(self):
        self.run_cycle();path=self.root/'runtime/daily/2026-09-09-live.json'
        doc=read_json(path);doc.pop('discovery_checked_at');atomic_json(path,doc)
        self.clock+=timedelta(minutes=5);self.run_cycle()
        self.assertEqual(self.broker.connected,2);self.assertEqual(len(self.broker.calls),1)

    def test_lunch_discovery_does_not_bypass_window(self):
        self.run_cycle();self.broker.items['301222.SZ']=info()
        self.clock=OPEN.replace(hour=12,minute=0);result=self.run_cycle()
        self.assertEqual(result['phase'],'waiting_afternoon');self.assertEqual(len(self.broker.calls),1)
        self.broker.order_id=102;self.clock=OPEN.replace(hour=13,minute=0);self.run_cycle()
        self.assertEqual(len(self.broker.calls),2)

    def test_configuration_recovery_marker_blocks_production_dispatch(self):
        atomic_json(self.root/'runtime/configuration-pending.json',{'status':'writing'})
        with self.assertRaises(ValueError):
            dispatch('cycle',self.cfg,live=True,send=True,broker_factory=lambda cfg:self.broker,now=lambda:self.clock)
        self.assertEqual(self.broker.connected,0);self.assertEqual(self.broker.calls,[])


if __name__=='__main__':unittest.main(verbosity=2)
