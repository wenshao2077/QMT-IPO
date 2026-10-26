# coding: utf-8
"""Dedicated QMT model, Python 3.6 compatible. No sockets or existing bridge imports.

Loaded by an installation-bound loader. init only registers an independent timer.
passorder has NO return value: a response can prove an attempt, never acceptance.
"""
import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

PROTOCOL = 1
_service = None


def _read(path):
    if os.path.islink(path) or os.path.getsize(path) > 2 * 1024 * 1024:
        raise ValueError('invalid_local_document')
    with open(path, 'r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def _write(path, value):
    temporary = path + '.' + uuid.uuid4().hex + '.tmp'
    with open(temporary, 'x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _now():
    return datetime.now(timezone(timedelta(hours=8)))


def _submit_window(stamp):
    clock = (stamp.hour, stamp.minute, stamp.second, stamp.microsecond)
    return (9,35,0,0) <= clock < (11,30,0,0) or (13,0,0,0) <= clock <= (14,50,0,0)


def _calendar_open(root, permit, now):
    """Independent reviewed annual gate, without importing the 3.11 calendar module."""
    day = now.date().isoformat()
    doc = _read(os.path.join(root, 'code', 'calendars', str(now.year)+'.json'))
    payload = {k: v for k, v in doc.items() if k != 'sha256'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if (doc.get('sha256') != digest or doc.get('schema_version') != 2
            or doc.get('complete_year') is not True or doc.get('year') != now.year
            or doc.get('source', {}).get('kind') != 'exchange_annual'
            or doc.get('markets') != ['SH','SZ'] or doc.get('covered_from') != str(now.year)+'-01-01'
            or doc.get('covered_through') != str(now.year)+'-12-31'
            or set(doc.get('holidays', {})) != {'new_year','spring_festival','qingming','labour','dragon_boat','mid_autumn','national'}
            or permit.get('status') != 'trading_day' or permit.get('day') != day
            or now.weekday() >= 5):
        raise ValueError('native_calendar_not_open')
    for span in list(doc['holidays'].values())+doc.get('extra_closures', []):
        if not isinstance(span, list) or len(span) != 2 or not doc['covered_from'] <= span[0] <= span[1] <= doc['covered_through']:
            raise ValueError('native_calendar_invalid')
        if span[0] <= day <= span[1]:
            raise ValueError('native_calendar_closed')


def _field(row, name):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def _integer(value, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError('invalid_integer')
    number = int(value)
    if number < (0 if zero else 1):
        raise ValueError('invalid_integer')
    return number


def _orders(raw, account):
    if not isinstance(raw, (list, tuple)):
        raise ValueError('native_orders_incomplete')
    result = []
    for row in raw:
        if _field(row, 'm_strAccountID') != account:
            raise ValueError('native_order_account_mismatch')
        code = str(_field(row, 'm_strInstrumentID'))
        market = str(_field(row, 'm_strExchangeID')).upper()
        market = {'SSE': 'SH', 'SZSE': 'SZ', 'SHSE': 'SH', 'BSE': 'BJ'}.get(market, market)
        if '.' not in code:
            code += '.' + market
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code):
            raise ValueError('native_order_code_invalid')
        day = datetime.strptime(str(_field(row, 'm_strInsertDate')), '%Y%m%d').date().isoformat()
        stamp = str(_field(row, 'm_strInsertTime'))
        datetime.strptime(stamp, '%H:%M:%S' if ':' in stamp else '%H%M%S')
        status = _integer(_field(row, 'm_nOrderStatus'), zero=True)
        # m_nRef is local QMT identity; cabinet IDs can be non-numeric. Do not
        # fabricate an SDK integer from a cabinet string or filter out the row.
        reference = _field(row, 'm_nRef')
        order_id = 0  # Never treat a native local reference as an XtQuant ID.
        try:
            remark = _field(row, 'm_strRemark')
            remark_state = 'readable' if isinstance(remark, str) else 'missing'
        except UnicodeError:
            remark, remark_state = None, 'decode_failed'
        except (AttributeError, KeyError):
            remark, remark_state = None, 'missing'
        result.append({'code': code, 'day': day, 'order_id': order_id, 'raw_status': status,
                       'status': {50: 'reported', 55: 'partial', 56: 'succeeded', 57: 'rejected',
                                  53: 'canceled', 54: 'canceled', 86: 'confirmed'}.get(status, 'pending'),
                       'remark': remark if remark_state == 'readable' else None,
                       'remark_state': remark_state, 'identity_scope': 'native_remark',
                       'native_reference': str(reference),
                       'native_order_ref': str(_field(row, 'm_strOrderRef')) if hasattr(row, 'm_strOrderRef') or isinstance(row, dict) and 'm_strOrderRef' in row else None,
                       'native_system_id': str(_field(row, 'm_strOrderSysID')) if hasattr(row, 'm_strOrderSysID') or isinstance(row, dict) and 'm_strOrderSysID' in row else None})
    return result


class NativeService:
    def __init__(self, root, ctx, api, now=_now):
        self.root = os.path.abspath(root)
        self.ctx, self.api, self.now = ctx, api, now
        self.config = self._config()
        self.account = self.config['account_id']
        self.account_digest = hashlib.sha256(self.account.encode()).hexdigest()
        self.prefix = self.config.get('task_prefix', 'QmtIPO3')
        if not isinstance(self.prefix, str) or not re.fullmatch(r'QmtIPO[A-Za-z0-9-]{0,40}', self.prefix):
            raise ValueError('invalid_native_task_prefix')
        self.spool = os.path.abspath(self.config['native_bridge_dir'])
        if self.spool != os.path.join(self.root, 'native'):
            raise ValueError('native_spool_must_be_installation_bound')
        for name in ('requests', 'responses', 'claims'):
            path = os.path.join(self.spool, name)
            if os.path.islink(path):
                raise ValueError('native_directory_link_refused')
            os.makedirs(path, exist_ok=True)
        self.session = uuid.uuid4().hex
        code_path = os.path.join(self.root, 'code', 'native_qmt_bridge.py')
        with open(code_path, 'rb') as stream:
            self.code_id = hashlib.sha256(stream.read()).hexdigest()

    def _config(self):
        return _read(os.path.join(self.root, 'config.json'))

    def _ready(self):
        rows = self.api['get_trade_detail_data'](self.account, 'STOCK', 'ACCOUNT')
        if not isinstance(rows, (list, tuple)):
            raise ValueError('native_account_query_failed')
        matches = [r for r in rows if _field(r, 'm_strAccountID') == self.account]
        if len(matches) != 1:
            raise ValueError('native_account_not_unique')
        row = matches[0]
        if str(_field(row, 'm_strStatus')).strip() not in ('正常', '已登录', '登录成功'):
            raise ValueError('native_account_not_ready')
        if str(_field(row, 'm_strTradingDate')) != self.now().strftime('%Y%m%d'):
            raise ValueError('native_broker_date_mismatch')
        for name in ('m_Enable','m_nStatus'):
            try:
                value = _field(row, name)
            except (AttributeError, KeyError):
                continue
            if name == 'm_Enable' and value not in (True, 1):
                raise ValueError('native_account_state_conflict')
            if name == 'm_nStatus' and _integer(value, zero=True) != 0:
                raise ValueError('native_account_state_conflict')
        return True

    def _all_orders(self):
        rows = _orders(self.api['get_trade_detail_data'](self.account, 'STOCK', 'ORDER'), self.account)
        if any(row['day'] != self.now().date().isoformat() for row in rows):
            raise ValueError('native_order_date_mismatch')
        return rows

    def _identity(self):
        return {'protocol': PROTOCOL, 'task_prefix': self.prefix, 'account_digest': self.account_digest,
                'code_id': self.code_id, 'session': self.session, 'qmt_pid': os.getpid()}

    def _request_valid(self, request):
        current = self._config()
        if any(current.get(k) != self.config.get(k) for k in
               ('account_id', 'task_prefix', 'state_dir', 'control_dir', 'native_bridge_dir', 'allowed_markets')):
            raise ValueError('native_binding_changed_reload_required')
        if (request.get('protocol') != PROTOCOL or request.get('task_prefix') != self.prefix
                or request.get('account_digest') != self.account_digest
                or request.get('qmt_pid') != os.getpid()
                or request.get('session') != self.session or request.get('code_id') != self.code_id):
            raise ValueError('native_request_identity_mismatch')
        now = self.now().timestamp()
        created, expires = request['created'], request['expires']
        if (type(created) not in (int, float) or type(expires) not in (int, float)
                or not math.isfinite(created) or not math.isfinite(expires)
                or not created <= now <= expires or not 0 < expires-created <= 15):
            raise ValueError('native_request_expired')
        return current

    def _submission(self, request):
        from_native = self._request_valid(request)
        authorization = _read(os.path.join(from_native['control_dir'], 'panel-authorization.json'))
        if (from_native.get('enable_execution') is not True or from_native.get('transport') != 'native'
                or authorization.get('authorized') is not True
                or authorization.get('scope') != 'daily_stock_and_bond_ipo'):
            raise ValueError('native_submission_disabled')
        # The same calendar contract is compiled into a per-day permit by the
        # Python 3.11 client. It is bound to this request, code and broker day.
        permit = request.get('calendar', {})
        day = self.now().date().isoformat()
        _calendar_open(self.root, permit, self.now())
        if not _submit_window(self.now()):
            raise ValueError('native_outside_submit_window')
        self._ready()
        payload = request['payload']
        code = payload['code']
        if not re.fullmatch(r'\d{6}\.(SH|SZ)', code):
            raise ValueError('native_code_invalid')
        ipos = self.api['get_ipo_data']()
        info = ipos[code]
        if str(info['purchaseDate']).replace('-', '') != day.replace('-', ''):
            raise ValueError('native_not_today_ipo')
        kind = info['type']
        market = 'KCB' if kind == 'STOCK' and code.endswith('.SH') and code.startswith(('688','689','787','789')) else code[-2:]
        if market not in from_native['allowed_markets'] or kind not in ('STOCK', 'BOND'):
            raise ValueError('native_scope_not_enabled')
        cap = _integer(info['maxPurchaseNum'])
        lot = _integer(info['minPurchaseNum'])
        if kind == 'STOCK':
            cap = min(cap, _integer(self.api['get_new_purchase_limit'](self.account)[market], zero=True))
        else:
            cap = min(cap, 10000)
        quantity = cap // lot * lot
        price = float(info['issuePrice'])
        remark = 'IPOv2_' + day.replace('-', '') + '_' + code
        if (not math.isfinite(price) or price <= 0 or quantity <= 0
                or type(payload.get('quantity')) is not int
                or payload.get('quantity') != quantity or payload.get('price') != price
                or payload.get('remark') != remark or payload.get('day') != day):
            raise ValueError('native_ipo_metadata_or_plan_changed')
        if any(row['code'] == code for row in self._all_orders()):
            raise ValueError('native_existing_order_no_resubmit')
        # Re-read after all native queries, immediately before crossing authority.
        self._request_valid(request)
        latest = self._config()
        authorization = _read(os.path.join(latest['control_dir'], 'panel-authorization.json'))
        if latest.get('enable_execution') is not True or latest.get('transport') != 'native' or authorization.get('authorized') is not True:
            raise ValueError('native_submission_disabled')
        _calendar_open(self.root, permit, self.now())
        if not _submit_window(self.now()):
            raise ValueError('native_outside_submit_window')
        claim = os.path.join(self.spool, 'claims', self.account_digest+'-'+day+'-'+code+'.json')
        with open(claim, 'x', encoding='utf-8') as stream:
            json.dump({'request_id': request['id'], 'day': day, 'code': code, 'session': self.session}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # A slow disk or a pause/scope change during fsync must not cross the
        # cutoff. Keep the claim even if this final guard prevents the call.
        self._request_valid(request)
        latest = self._config()
        authorization = _read(os.path.join(latest['control_dir'], 'panel-authorization.json'))
        if (latest.get('enable_execution') is not True or latest.get('transport') != 'native'
                or authorization.get('authorized') is not True
                or authorization.get('scope') != 'daily_stock_and_bond_ipo'
                or market not in latest['allowed_markets']):
            raise ValueError('native_submission_disabled')
        _calendar_open(self.root, permit, self.now())
        # Final in-memory time checks come after ALL file/native I/O. No file
        # read, fsync, metadata query or calendar check may follow this point.
        final = self.now()
        if not request['created'] <= final.timestamp() <= request['expires']:
            raise ValueError('native_request_expired')
        if final.date().isoformat() != day or not _submit_window(final):
            raise ValueError('native_outside_submit_window')
        # EXACTLY one signature, once. Even TypeError/None/timeout is uncertain.
        self.api['passorder'](23, 1101, self.account, code, 11, price, quantity,
                              'AutoIPOv2', 2, remark, self.ctx)
        return {'attempted': True, 'order_id': 0, 'acceptance': 'requires_order_query'}

    def handle(self, request):
        self._request_valid(request)
        operation = request['operation']
        if operation == 'ready':
            return self._ready()
        self._ready()
        if operation == 'orders':
            return self._all_orders()
        if operation == 'ipos':
            value = self.api['get_ipo_data']()
        elif operation == 'limits':
            value = self.api['get_new_purchase_limit'](self.account)
        elif operation == 'submit':
            return self._submission(request)
        else:
            raise ValueError('native_operation_not_allowed')
        if not isinstance(value, dict):
            raise ValueError('native_query_incomplete')
        return value

    def tick(self):
        heartbeat = dict(self._identity(), at=self.now().timestamp(), status='alive')
        _write(os.path.join(self.spool, 'heartbeat.json'), heartbeat)
        requests = os.path.join(self.spool, 'requests')
        # One native call per timer; protect the existing auction bridges.
        for name in sorted(os.listdir(requests)):
            if not re.fullmatch(r'[a-f0-9]{32}\.json', name):
                continue
            path = os.path.join(requests, name)
            response = os.path.join(self.spool, 'responses', name)
            if os.path.isfile(response):
                continue
            result = dict(self._identity(), id=name[:-5], observed=self.now().timestamp())
            try:
                request = _read(path)
                if request.get('id') != name[:-5]:
                    raise ValueError('native_request_id_mismatch')
                result.update(ok=True, data=self.handle(request))
            except Exception as exc:
                # Never expose exception values: they may contain account data.
                result.update(ok=False, error_type=type(exc).__name__, error_code=str(exc) if type(exc) is ValueError and re.fullmatch('[a-z_]+', str(exc)) else 'native_operation_failed')
            result['observed'] = self.now().timestamp()
            _write(response, result)
            break


def init(ContextInfo):
    global _service
    root = globals().get('QMT_IPO_ROOT')
    if not root or not os.path.isabs(root):
        raise ValueError('installation_bound_loader_required')
    api = {}
    for name in ('get_trade_detail_data', 'get_ipo_data', 'get_new_purchase_limit', 'passorder'):
        fn = globals().get(name)
        if not callable(fn):
            raise ValueError('native_api_missing_' + name)
        api[name] = fn
    _service = NativeService(root, ContextInfo, api)
    ContextInfo.set_account(_service.account)
    ContextInfo.run_time('ipo_native_timer', '1nSecond', _now().strftime('%Y-%m-%d %H:%M:%S'))


def ipo_native_timer(ContextInfo):
    try:
        if _service is not None:
            _service.tick()
    finally:
        time.sleep(0.02)


def handlebar(ContextInfo):
    pass
