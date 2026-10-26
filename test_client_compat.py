"""Regression tests for observed IPC/UTF-8 failures. Fake SDK; no sockets."""
from contextlib import nullcontext
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import venv
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

from client_binding import validate_userdata, BrokerDiagnosticError
from mini_base import MiniBroker
from probe_readonly import probe
from rebind_client import rebind
from prepare_monitor_adapter import stage
from run_history import make_row
from runtime import atomic_json, read_json
from support import build_plan
from test_system import OPEN, DATE
import test_system
from test_delivery_round1 import disabled_snapshot


def native_row(code='603218.SH', order_id=10, **changes):
    return SimpleNamespace(**({'account_id': 'fake', 'stock_code': code, 'order_id': order_id,
        'order_time': int(OPEN.timestamp()), 'order_status': 50, 'order_remark': ''} | changes))


class BrokenRemark:
    def __init__(self, **changes):
        values = vars(native_row(**changes)).copy()
        values.pop('order_remark')
        self.__dict__.update(values)

    @property
    def order_remark(self):
        raise UnicodeDecodeError('utf-8', b'x'*22+b'\xe4', 22, 23, 'unexpected end of data')


def adapter(rows):
    broker = MiniBroker({'account_id': 'fake'})
    broker.account = object()
    broker.constants = SimpleNamespace(ORDER_REPORTED=50, ORDER_SUCCEEDED=56)
    broker.trader = Mock()
    broker.trader.query_stock_orders.return_value = rows
    return broker


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('Network forbidden')).start()

    def test_same_sdk_accepts_each_explicit_endpoint_without_fallback(self):
        for name in ('userdata', 'userdata_mini'):
            with self.subTest(name=name):
                path = self.root/name
                path.mkdir()
                trader = Mock()
                trader.connect.return_value = trader.subscribe.return_value = 0
                constructor = Mock(return_value=trader)
                parent = ModuleType('xtquant')
                parent.xtconstant = SimpleNamespace()
                parent.xtdata = SimpleNamespace()
                modules = {'xtquant': parent, 'xtquant.xttrader': SimpleNamespace(XtQuantTrader=constructor),
                           'xtquant.xttype': SimpleNamespace(StockAccount=Mock())}
                with patch.dict(sys.modules, modules):
                    broker = MiniBroker({'account_id': 'fake', 'qmt_userdata': str(path)})
                    broker.connect()
                    broker.close()
                self.assertEqual(constructor.call_args.args[0], str(path))
                self.assertEqual(broker.connection_info['userdata_kind'], name)
                self.assertFalse(broker.connection_info['automatic_fallback'])
                trader.stop.assert_called_once()

    def test_invalid_directory_is_rejected_without_sdk_import(self):
        path = self.root/'other'
        path.mkdir()
        with self.assertRaises(ValueError):
            validate_userdata(path)

    def test_connect_and_subscribe_failures_have_separate_codes(self):
        path = self.root/'userdata'
        path.mkdir()
        for connect_rc, subscribe_rc, code in ((-1, 0, 'qmt_connect_failed'), (0, -2, 'qmt_subscribe_failed')):
            trader = Mock()
            trader.connect.return_value, trader.subscribe.return_value = connect_rc, subscribe_rc
            parent = ModuleType('xtquant')
            parent.xtconstant, parent.xtdata = SimpleNamespace(), SimpleNamespace()
            with patch.dict(sys.modules, {'xtquant': parent,
                'xtquant.xttrader': SimpleNamespace(XtQuantTrader=Mock(return_value=trader)),
                'xtquant.xttype': SimpleNamespace(StockAccount=Mock())}):
                broker = MiniBroker({'account_id': 'fake', 'qmt_userdata': str(path)})
                with self.assertRaises(BrokerDiagnosticError) as raised:
                    broker.connect()
                self.assertEqual(raised.exception.code, code)
                broker.close()
                trader.stop.assert_called_once()


class OrderParsingTests(unittest.TestCase):
    def test_51_rows_with_16_broken_remarks_are_retained(self):
        rows = [BrokenRemark(order_id=i+1) if i < 16 else native_row(order_id=i+1) for i in range(51)]
        broker = adapter(rows)
        normalized = broker.orders()
        self.assertEqual(len(normalized), 51)
        self.assertEqual([r['order_id'] for r in normalized], list(range(1, 52)))
        self.assertEqual(broker.order_diagnostics, {'row_count': 51, 'remark_unavailable_count': 16})
        self.assertTrue(all(r['remark'] is None and r['remark_state'] == 'decode_failed' for r in normalized[:16]))

    def test_core_getter_failure_never_returns_partial_list(self):
        class BrokenCode:
            @property
            def stock_code(self):
                raise UnicodeDecodeError('utf-8', b'\xe4', 0, 1, 'truncated')
            account_id='fake'
            order_time=int(OPEN.timestamp())
        with self.assertRaises(BrokerDiagnosticError) as raised:
            adapter([native_row(), BrokenCode()]).orders()
        self.assertEqual(raised.exception.diagnostic['row_index'], 1)
        self.assertEqual(raised.exception.diagnostic['field'], 'stock_code')

    def test_missing_remark_and_zero_id_are_explicit_not_dropped(self):
        row = native_row(order_id=0)
        del row.order_remark
        result = adapter([row]).orders()[0]
        self.assertEqual(result['order_id'], 0)
        self.assertIsNone(result['remark'])
        self.assertEqual(result['remark_state'], 'missing')


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.f = test_system.DailySystemTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_corrupt_remark_existing_security_still_blocks_new_intent(self):
        self.f.broker.orders = adapter([BrokenRemark(code='301111.SZ')]).orders
        result = self.f.run_cycle()
        self.assertEqual(result['items']['301111.SZ']['status'], 'EXTERNAL_ACCEPTED')
        self.assertEqual(self.f.broker.calls, [])
        self.assertEqual(self.f.ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)

    def test_existing_id_reconciles_even_when_remark_is_broken(self):
        self.f.run_cycle()
        self.f.clock = OPEN.replace(hour=15, minute=5)
        self.f.broker.orders = adapter([BrokenRemark(code='301111.SZ', order_id=101)]).orders
        self.assertTrue(self.f.run_cycle()['completed'])
        self.assertEqual(len(self.f.broker.calls), 1)

    def test_missing_id_with_broken_remark_never_claims_success_or_resends(self):
        plan = build_plan('301111.SZ', self.f.broker.items['301111.SZ'], self.f.broker.limit, DATE)
        from coordinator import Coordinator
        coordinator = Coordinator(self.f.cfg, self.f.broker, self.f.ledger, lambda: self.f.clock, 'live')
        self.f.ledger.reserve(coordinator.account, plan)
        self.f.broker.orders = adapter([BrokenRemark(code=plan.code)]).orders
        for minutes in (0, 5):
            self.f.clock = OPEN+timedelta(minutes=minutes)
            result = self.f.run_cycle()
            self.assertFalse(result['completed'])
            self.assertEqual(result['items'][plan.code]['status'], 'INTENT')
        self.assertEqual(self.f.broker.calls, [])
        self.assertEqual(self.f.ledger.db.execute('SELECT state FROM intents').fetchone()[0], 'INTENT')

    def test_previously_reported_but_missing_current_order_is_uncertain(self):
        self.f.run_cycle()
        self.f.clock = OPEN.replace(hour=15, minute=5)
        self.f.broker.order_rows = []
        result = self.f.run_cycle()
        self.assertFalse(result['completed'])
        self.assertEqual(result['items']['301111.SZ']['status'], 'UNCERTAIN')
        self.assertEqual(len(self.f.broker.calls), 1)

    def test_connection_monitor_does_not_claim_confirmed_unfinished_projects(self):
        from app import inspect_health
        self.f.broker.connect_error = True
        self.f.clock = OPEN.replace(hour=10)
        self.f.run_cycle()
        health = inspect_health(self.f.cfg, self.f.clock)
        self.assertIn('qmt_connection_failed', health['issues'])
        self.assertNotIn('daily_work_incomplete', health['issues'])

    def test_readonly_probe_does_not_touch_ledger_or_submit_or_query_in_connection_mode(self):
        before = list(self.f.ledger.db.iterdump())
        result = probe(self.f.cfg, connection_only=True, broker_factory=lambda _: self.f.broker, now=lambda: OPEN)
        self.assertEqual(result['status'], 'read_only_connection_complete')
        self.assertEqual(before, list(self.f.ledger.db.iterdump()))
        self.assertEqual(self.f.broker.calls, [])
        self.assertEqual(self.f.broker.closed, 1)

    def test_failure_stage_return_code_and_detail_are_visible(self):
        def fail():
            raise BrokerDiagnosticError('qmt_subscribe_failed', '订阅账户', return_code=-2)
        self.f.broker.connect = fail
        result = self.f.run_cycle()
        row = make_row(Path('receipt.json'), {'action':'cycle','activity':result['last_activity'],'result':result})
        self.assertIn('订阅账户', row['detail'])
        self.assertIn('return_code', row['detail'])
        self.assertIn('-2', row['detail'])


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.f = test_system.DailySystemTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.ledger.close()
        self.f.cfg['enable_execution'] = False
        self.install = self.f.root/'install'
        self.install.mkdir()
        atomic_json(self.install/'config.json', self.f.cfg)
        self.target = self.f.root/'userdata'
        self.target.mkdir()

    def call(self):
        return rebind(self.install, self.target, confirmed=True,
                      task_provider=disabled_snapshot, mutex_factory=nullcontext)

    def test_paused_used_instance_preserves_every_other_setting_and_ledger_bytes(self):
        before = read_json(self.install/'config.json')
        ledger = Path(before['state_dir'])/'state.sqlite3'
        ledger_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
        result = self.call()
        after = read_json(self.install/'config.json')
        self.assertEqual(after.pop('qmt_userdata'), str(self.target.resolve()))
        before.pop('qmt_userdata')
        self.assertEqual(after, before)
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger_hash)
        self.assertFalse(result['execution_enabled'])
        self.assertFalse(result['tasks_changed'])

    def test_enabled_instance_is_rejected_without_private_write(self):
        self.f.cfg['enable_execution'] = True
        atomic_json(self.install/'config.json', self.f.cfg)
        before = (self.install/'config.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'pause_execution'):
            self.call()
        self.assertEqual((self.install/'config.json').read_bytes(), before)

    def test_interrupted_new_install_is_not_rebound(self):
        atomic_json(self.install/'new-install.json', {'stage': 'tasks_ready'})
        before = (self.install/'config.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'finish_installation'):
            self.call()
        self.assertEqual((self.install/'config.json').read_bytes(), before)

    def test_resolved_directory_is_validated_before_any_write(self):
        before = (self.install/'config.json').read_bytes()
        invalid = self.f.root/'other'
        invalid.mkdir()
        with patch('rebind_client.validate_userdata', side_effect=[invalid, ValueError('invalid')]):
            with self.assertRaises(ValueError):
                self.call()
        self.assertEqual((self.install/'config.json').read_bytes(), before)


class MonitorAdapterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pwsh.exe') or shutil.which('pwsh'), 'PowerShell 7 unavailable')
    def test_native_ps_wrapper_requires_one_typed_success_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv.EnvBuilder(with_pip=False).create(root/'.venv')
            # The wrapper is a Windows integration; use its actual Windows layout.
            if not (root/'.venv/Scripts/python.exe').is_file():
                self.skipTest('Windows virtual environment layout required')
            (root/'config.json').write_text('{}', encoding='utf-8')
            shutil.copyfile(Path(__file__).parent/'probe_qmt_trade.ps1', root/'probe_qmt_trade.ps1')
            host = shutil.which('pwsh.exe') or shutil.which('pwsh')
            valid = {'status':'read_only_connection_complete', 'account_ready':True}
            for report, expected in ((valid, True), ({**valid,'account_ready':'true'}, False),
                    ([valid, {'status':'failed','account_ready':False}], False), ([valid], False), ({}, False)):
                with self.subTest(report=report):
                    body = 'import json\nprint('+repr(json.dumps(report))+')\n'
                    (root/'probe_readonly.py').write_text(body, encoding='utf-8')
                    result = subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(root/'probe_qmt_trade.ps1'),
                                             '-InstallRoot',str(root)], capture_output=True, encoding='utf-8', timeout=25)
                    parsed = json.loads(result.stdout)
                    self.assertIs(parsed['healthy'], expected)
                    self.assertEqual(result.returncode, 0 if expected else 2)

    @unittest.skipUnless(shutil.which('pwsh.exe') or shutil.which('pwsh'), 'PowerShell 7 unavailable')
    def test_generated_preopen_checks_exit_structure_and_never_forwards_raw_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitors = root/'monitors'
            monitors.mkdir()
            (monitors/'watch_live.ps1').write_text("foreach ($processName in @('miniquote', 'rocket')) { }", encoding='utf-8')
            (monitors/'probe_qmt_trade.ps1').write_text('# old probe', encoding='utf-8')
            (monitors/'monitor_qmt_preopen.ps1').write_text(
                "function Invoke-TradeProbe { }\nif (-not (Test-Path -LiteralPath $PROBE)) { throw 'missing' }\n"
                "(Invoke-TradeProbe).Result | ConvertTo-Json -Compress\n", encoding='utf-8')
            stage(monitors, root/'ipo', root/'staged')
            host = shutil.which('pwsh.exe') or shutil.which('pwsh')
            for body, rc, expected in (('{"healthy":true}', 0, True), ('{}', 0, False),
                    ('{"healthy":false,"issues":[]}', 0, False), ('{"healthy":true}', 1, False),
                    ('[{"healthy":true}]', 0, False), ('PRIVATE_RAW_SENTINEL', 0, False)):
                fake = root/'fake.ps1'
                fake.write_text("Write-Output '"+body+"'\nexit "+str(rc), encoding='utf-8')
                script = "$PROBE='"+str(fake).replace("'","''")+"'; & '"+str(root/'staged/monitor_qmt_preopen.ps1').replace("'","''")+"'"
                result = subprocess.run([host,'-NoProfile','-NonInteractive','-Command',script],
                                        capture_output=True, encoding='utf-8', timeout=15)
                parsed = json.loads(result.stdout)
                self.assertIs(parsed['healthy'], expected)
                self.assertNotIn('PRIVATE_RAW_SENTINEL', result.stdout)

    @unittest.skipUnless(shutil.which('pwsh.exe') or shutil.which('pwsh'), 'PowerShell 7 unavailable')
    def test_generated_monitor_never_accepts_false_empty_malformed_or_nonzero_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitors, ipo = root/'monitors', root/'ipo'
            monitors.mkdir()
            ipo.mkdir()
            original = ("$ErrorActionPreference='Stop'\n$issues=[Collections.Generic.List[string]]::new()\n"
                        "$details=@{}\nfunction Add-Issue($list,$issue){$list.Add($issue)}\n"
                        "foreach ($processName in @('miniquote', 'rocket')) { }\n"
                        "Write-Output ('ISSUE_COUNT='+$issues.Count)\n")
            (monitors/'watch_live.ps1').write_text(original, encoding='utf-8')
            (monitors/'probe_qmt_trade.ps1').write_text('# old probe', encoding='utf-8')
            stage(monitors, ipo, root/'staged')
            host = shutil.which('pwsh.exe') or shutil.which('pwsh')
            for body, rc, expected in (('{"healthy":true}', 0, 0), ('{}', 0, 1),
                    ('{"healthy":false,"issues":[]}', 0, 1), ('{"healthy":true}', 1, 1),
                    ('{"healthy":"true"}', 0, 1), ('[{"healthy":true}]', 0, 1), ('broken', 0, 1)):
                with self.subTest(body=body, rc=rc):
                    (ipo/'probe_qmt_trade.ps1').write_text("Write-Output '"+body+"'\nexit "+str(rc), encoding='utf-8')
                    result = subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(root/'staged/watch_live.ps1')],
                                            capture_output=True, encoding='utf-8', timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('ISSUE_COUNT='+str(expected), result.stdout)

    def test_stage_preserves_business_checks_and_refuses_unknown_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitors = root/'monitors'
            monitors.mkdir()
            original = "foreach ($processName in @('miniquote', 'rocket')) {\n}\n# Rocket account.json Fuel untouched\n"
            (monitors/'watch_live.ps1').write_text(original, encoding='utf-8')
            (monitors/'probe_qmt_trade.ps1').write_text('# old probe', encoding='utf-8')
            result = stage(monitors, root/'ipo with space', root/'staged')
            self.assertEqual(result['status'], 'staged_not_installed')
            self.assertEqual((monitors/'watch_live.ps1').read_text(), original)
            changed = (root/'staged/watch_live.ps1').read_text(encoding='utf-8')
            self.assertIn("@('rocket')", changed)
            self.assertIn('Rocket account.json Fuel untouched', changed)
            self.assertIn('tradeProbe.healthy', changed)
            with self.assertRaises(ValueError):
                stage(monitors, root/'ipo', root/'staged')


if __name__ == '__main__':
    unittest.main()
