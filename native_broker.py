"""Bounded local file RPC to the independent native QMT IPO model. No retry."""
import hashlib
from pathlib import Path
import time
import uuid

from runtime import atomic_json, read_json
from market_calendar import CalendarService, OPEN
from support import china_now
from instance_identity import task_prefix
from client_binding import BrokerDiagnosticError, SubmissionReceipt


class NativeBroker:
    def __init__(self, config, read_only=False, *, clock=time.time, sleep=time.sleep, timeout=8):
        self.config, self.read_only = config, read_only
        self.clock, self.sleep, self.timeout = clock, sleep, timeout
        self.spool = Path(config['native_bridge_dir'])
        self.connection_info = {'transport': 'native', 'automatic_fallback': False}
        self.order_diagnostics = {}
        self.identity = None

    def connect(self):
        try:
            beat = read_json(self.spool/'heartbeat.json')
        except (OSError, ValueError):
            raise BrokerDiagnosticError('native_model_unavailable', '连接QMT原生打新桥') from None
        code = Path(__file__).resolve().parent/'native_qmt_bridge.py'
        expected = hashlib.sha256(code.read_bytes()).hexdigest()
        age = self.clock()-beat.get('at', 0)
        if (beat.get('protocol') != 1 or beat.get('task_prefix') != task_prefix(self.config)
                or beat.get('account_digest') != hashlib.sha256(self.config['account_id'].encode()).hexdigest()
                or beat.get('code_id') != expected or not 0 <= age <= 5
                or not isinstance(beat.get('qmt_pid'), int) or not isinstance(beat.get('session'), str)):
            raise BrokerDiagnosticError('native_model_identity_or_freshness', '连接QMT原生打新桥')
        self.identity = {k: beat[k] for k in ('protocol','task_prefix','account_digest','code_id','session','qmt_pid')}
        self.connection_info.update(native_session=beat['session'], qmt_pid=beat['qmt_pid'], code_id=expected, heartbeat_age=round(age, 3))

    def _call(self, operation, payload=None):
        if self.identity is None:
            raise RuntimeError('native_not_connected')
        rid = uuid.uuid4().hex
        created = self.clock()
        request = dict(self.identity, id=rid, created=created, expires=created+self.timeout,
                       operation=operation, payload=payload or {})
        if operation == 'submit':
            if self.read_only or self.config.get('enable_execution') is not True:
                raise RuntimeError('native_submission_disabled')
            decision = CalendarService(self.config).decide(china_now().date().isoformat())
            if decision.status != OPEN:
                raise RuntimeError('native_calendar_not_open')
            request['calendar'] = decision.to_dict()
        path = self.spool/'requests'/(rid+'.json')
        response = self.spool/'responses'/(rid+'.json')
        atomic_json(path, request)
        deadline = time.monotonic()+self.timeout
        while time.monotonic() < deadline:
            if response.is_file():
                value = read_json(response)
                if (value.get('id') != rid or any(value.get(k) != v for k, v in self.identity.items())
                        or not created <= value.get('observed', 0) <= request['expires']):
                    raise BrokerDiagnosticError('native_response_identity_or_freshness', '核对QMT原生桥响应')
                # Keep the response for this call only; server claims remain forever.
                path.unlink(missing_ok=True)
                response.unlink(missing_ok=True)
                if value.get('ok') is not True:
                    raise BrokerDiagnosticError(value.get('error_code', 'native_query_failed'), 'QMT原生接口查询')
                return value.get('data')
            self.sleep(0.05)
        # Do not delete a timed-out request: the native side rejects its expiry.
        # A submit timeout is always uncertain. Never retry it or change routes.
        raise BrokerDiagnosticError('native_request_timeout', '等待QMT原生桥响应')

    def ready(self):
        if self._call('ready') is not True:
            raise RuntimeError('native_account_not_ready')

    def orders(self):
        rows = self._call('orders')
        if not isinstance(rows, list):
            raise RuntimeError('native_orders_incomplete')
        self.order_diagnostics = {'row_count': len(rows), 'remark_unavailable_count': sum(r.get('remark_state') != 'readable' for r in rows)}
        return rows

    def ipos(self):
        return self._call('ipos')

    def limits(self):
        return self._call('limits')

    def submit(self, plan):
        import sqlite3
        if self.read_only or self.config.get('enable_execution') is not True:
            raise RuntimeError('native_submission_disabled')
        ledger = Path(self.config['state_dir'])/'state.sqlite3'
        db = sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True)
        try:
            account = hashlib.sha256(self.config['account_id'].encode()).hexdigest()[:24]
            row = db.execute('SELECT state,quantity FROM intents WHERE account=? AND day=? AND code=?',
                             (account, plan.day, plan.code)).fetchone()
            if row != ('INTENT', plan.quantity):
                raise RuntimeError('native_durable_first_intent_required')
        finally:
            db.close()
        receipt = self._call('submit', {'code': plan.code, 'day': plan.day, 'quantity': plan.quantity,
                              'price': plan.price, 'remark': plan.remark})
        if not isinstance(receipt, dict) or receipt.get('attempted') is not True or receipt.get('order_id') != 0:
            raise BrokerDiagnosticError('native_submission_receipt_invalid', '核对原生申购回报')
        return SubmissionReceipt()

    def drain_errors(self):
        return []

    def close(self):
        self.identity = None  # Never stops a QMT model or process.
