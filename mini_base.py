"""Thin miniQMT adapter; SDK is imported only on explicit connection.

No QMT/QuantClass process start, stop, restart or ledger modification.
close() stops only this process's XtQuantTrader connection.
"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import time

from support import CHINA, positive_int


class MiniBroker:
    def __init__(self, config):
        self.config = config
        self.trader = None

    def connect(self):
        root = Path(self.config['qmt_userdata'])
        if not root.is_absolute() or not root.is_dir() or root.name.lower() != 'userdata_mini':
            raise ValueError('请核对 miniQMT 的绝对 userdata_mini 路径')
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
        if self.trader.connect() != 0:
            raise RuntimeError('miniQMT连接失败')
        if self.trader.subscribe(self.account) != 0:
            raise RuntimeError('miniQMT账户订阅失败')

    def close(self):
        if self.trader is not None:
            self.trader.stop()

    def is_trading_day(self, day):
        # Use QMT calendar, not the upstream fixed D:\data\period_offset.csv.
        year = day[:4]
        dates = self.data.get_trading_dates('SH', year+'0101', year+'1231', -1)
        if dates is None or len(dates) == 0:
            raise RuntimeError('交易日历不可用，禁止将空数据当作休市')
        parsed = set()
        for value in dates:
            # xtdata trading dates are Unix milliseconds.
            stamp = float(value)
            if stamp < 1_000_000_000_000:
                raise RuntimeError('交易日历时间单位不符合毫秒接口合同')
            parsed.add(datetime.fromtimestamp(stamp/1000, CHINA).date().isoformat())
        covered_through = max(parsed)
        # get_trading_dates is historical. Extend ONLY using existing, year-covered
        # QMT holiday data. Do not invoke SDK get_trading_calendar (it downloads).
        try:
            holidays = self.data.get_holidays()
            if (not isinstance(holidays, (list, tuple)) or not holidays) and self.config.get('holiday_file'):
                holidays = [line.strip().replace('-', '') for line in
                            Path(self.config['holiday_file']).read_text(encoding='utf-8-sig').splitlines()
                            if line.strip() and not line.lstrip().startswith('#')]
            if not isinstance(holidays, (list, tuple)) or not holidays:
                raise ValueError('holidays unavailable')
            holidays = {datetime.strptime(str(value), '%Y%m%d').date() for value in holidays}
            if max(holidays).year < int(year):
                raise ValueError('holidays stale')
            stamp = datetime.fromisoformat(covered_through).date() + timedelta(days=1)
            end = datetime(int(year), 12, 31).date()
            while stamp <= end:
                if stamp.weekday() < 5 and stamp not in holidays:
                    parsed.add(stamp.isoformat())
                stamp += timedelta(days=1)
            covered_through = end.isoformat()
        except (ValueError, TypeError, AttributeError):
            pass
        if self.config.get('state_dir'):
            import json
            import uuid
            root = Path(self.config['state_dir'])
            root.mkdir(parents=True, exist_ok=True)
            temp = root/('calendar.'+uuid.uuid4().hex+'.tmp')
            temp.write_text(json.dumps({'year': int(year), 'days': sorted(parsed), 'covered_through': covered_through,
                                        'refreshed_at': datetime.now(CHINA).isoformat()}), encoding='utf-8')
            os.replace(temp, root/'calendar.json')
        if day > covered_through:
            raise RuntimeError('日历未覆盖请求日期，不能当作休市')
        return day in parsed

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
        orders = []
        for order in raw:
            # Scope to requested account even if broker unexpectedly returns mixed data.
            if getattr(order, 'account_id', None) != self.config['account_id']:
                raise RuntimeError('委托查询账户不一致')
            stamp = positive_int(order.order_time, '委托时间')
            if not 1_000_000_000 <= stamp < 10_000_000_000:
                raise RuntimeError('委托时间不是预期的秒级时间戳')
            orders.append({
                'code': order.stock_code,
                'day': datetime.fromtimestamp(stamp, CHINA).date().isoformat(),
                'order_id': positive_int(order.order_id, '委托号'),
                'status': statuses.get(order.order_status, 'pending'),
                'remark': getattr(order, 'order_remark', ''),
            })
        return orders

    def submit(self, plan):
        return self.trader.order_stock(
            account=self.account, stock_code=plan.code,
            order_type=self.constants.STOCK_BUY, order_volume=plan.quantity,
            price_type=self.constants.FIX_PRICE, price=plan.price,
            strategy_name='AutoIPOv2', order_remark=plan.remark,
        )
