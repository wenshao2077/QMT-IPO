"""Thin XtQuant adapter; SDK is imported only on explicit connection.

No QMT/QuantClass process start, stop, restart or ledger modification.
close() stops only this process's XtQuantTrader connection.
"""
from datetime import datetime
import os
from pathlib import Path
import time

from support import CHINA, positive_int
from client_binding import validate_userdata, binding_report, BrokerDiagnosticError


class MiniBroker:
    def __init__(self, config):
        self.config = config
        self.trader = None
        self.connection_info = {}
        self.order_diagnostics = {'row_count': 0, 'remark_unavailable_count': 0}

    def connect(self):
        root = validate_userdata(self.config['qmt_userdata'])
        self.connection_info = binding_report(root)
        account_id = self.config['account_id']
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError('资金账号未配置')
        from xtquant import xtconstant, xtdata
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
        self.constants, self.data = xtconstant, xtdata
        self.account = StockAccount(account_id, 'STOCK')
        session = (time.time_ns() ^ (os.getpid() << 16)) % 2147483646 + 1
        self.trader = XtQuantTrader(str(root), session)
        self.trader.start()
        rc = self.trader.connect()
        self.connection_info['connect_rc'] = rc
        if rc != 0:
            raise BrokerDiagnosticError('qmt_connect_failed', '连接QMT', return_code=rc)
        rc = self.trader.subscribe(self.account)
        self.connection_info['subscribe_rc'] = rc
        if rc != 0:
            raise BrokerDiagnosticError('qmt_subscribe_failed', '订阅账户', return_code=rc)

    def close(self):
        if self.trader is not None:
            self.trader.stop()

    def is_trading_day(self, day):
        # Compatibility entry point. Calendar decision no longer needs self.data
        # or a successful account connection and never extrapolates holidays.
        from market_calendar import CalendarService, OPEN, UNKNOWN
        decision = CalendarService(self.config).decide(day)
        if decision.status == UNKNOWN:
            raise RuntimeError('交易日历未知；请独立更新日历，不允许提交')
        return decision.status == OPEN

    def ipos(self):
        return self.trader.query_ipo_data()

    def limits(self):
        return self.trader.query_new_purchase_limit(self.account)

    def orders(self):
        raw = self.trader.query_stock_orders(self.account, False)
        if not isinstance(raw, (list, tuple)):
            raise RuntimeError('无法获取完整券商委托列表')
        status_names = {
            'ORDER_REPORTED': 'reported', 'ORDER_SUCCEEDED': 'succeeded',
            'ORDER_PART_SUCC': 'partial', 'ORDER_JUNK': 'rejected',
            'ORDER_CANCELED': 'canceled', 'ORDER_PART_CANCEL': 'canceled',
        }
        statuses = {getattr(self.constants, k): v for k, v in status_names.items()
                    if hasattr(self.constants, k)}
        # QMT EEntrustStatus: 86 = ENTRUST_STATUS_DETERMINED (已确认).
        # xtquant 250807.1.2 does not expose an ORDER_* constant for it.
        # Confirmation is acceptance, not a fill or an IPO allotment.
        statuses[86] = 'confirmed'
        orders = []
        self.order_diagnostics = {'row_count': len(raw), 'remark_unavailable_count': 0}
        for index, order in enumerate(raw):
            values = {}
            for field in ('account_id', 'order_time', 'stock_code', 'order_id', 'order_status'):
                try:
                    values[field] = getattr(order, field)
                except Exception as exc:
                    raise BrokerDiagnosticError('qmt_order_field_unreadable', '查询并核对券商委托',
                                                row_index=index, field=field) from exc
            # Scope to requested account even if broker unexpectedly returns mixed data.
            if values['account_id'] != self.config['account_id']:
                raise RuntimeError('委托查询账户不一致')
            stamp = positive_int(values['order_time'], '委托时间')
            if not 1_000_000_000 <= stamp < 10_000_000_000:
                raise RuntimeError('委托时间不是预期的秒级时间戳')
            if not isinstance(values['stock_code'], str) or not isinstance(values['order_status'], int) or isinstance(values['order_status'], bool):
                raise BrokerDiagnosticError('qmt_order_field_invalid', '查询并核对券商委托', row_index=index)
            try:
                remark = getattr(order, 'order_remark', None)
                remark_state = 'readable' if isinstance(remark, str) else 'missing'
            except UnicodeError:
                remark, remark_state = None, 'decode_failed'
            if remark_state != 'readable':
                remark = None
                self.order_diagnostics['remark_unavailable_count'] += 1
            orders.append({
                'code': values['stock_code'],
                'day': datetime.fromtimestamp(stamp, CHINA).date().isoformat(),
                # Broker-originated orders can lack a local SDK id. Keep them
                # in reconciliation/deduplication; submit() still requires > 0.
                'order_id': positive_int(values['order_id'], '委托号', allow_zero=True),
                'status': statuses.get(values['order_status'], 'pending'),
                'raw_status': values['order_status'],
                'remark': remark,
                'remark_state': remark_state,
            })
        return orders

    def submit(self, plan):
        return self.trader.order_stock(
            account=self.account, stock_code=plan.code,
            order_type=self.constants.STOCK_BUY, order_volume=plan.quantity,
            price_type=self.constants.FIX_PRICE, price=plan.price,
            strategy_name='AutoIPOv2', order_remark=plan.remark,
        )
