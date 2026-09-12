"""Current miniQMT adapter: query and submission, no sell/cancel/fund-transfer code."""
import queue
import re

from mini_base import MiniBroker
from privacy import redact


class Broker(MiniBroker):
    def __init__(self,config):
        super().__init__(config)
        self.errors=queue.SimpleQueue()

    def connect(self):
        super().connect()
        from xtquant.xttrader import XtQuantTraderCallback
        owner=self
        class Callback(XtQuantTraderCallback):
            def on_order_error(self,error):
                if getattr(error,'account_id',None) == owner.config['account_id']:
                    owner.errors.put({'id':getattr(error,'order_id',None),
                                      'remark':getattr(error,'order_remark',''),
                                      'message':owner.clean(getattr(error,'error_msg',''))})
        self.callback=Callback()
        self.trader.register_callback(self.callback)

    def clean(self,value):
        return redact(value, self.config)[:400]

    def ready(self):
        statuses=self.trader.query_account_status()
        if not isinstance(statuses,(list,tuple)):
            raise RuntimeError('无法确认券商账户登录状态')
        found=[row for row in statuses if row.account_id == self.config['account_id']
               and row.account_type == self.account.account_type]
        if len(found) != 1 or found[0].status != self.constants.ACCOUNT_STATUS_OK:
            raise RuntimeError('券商账户未就绪，稍后仅对未提交项目再尝试')

    def drain_errors(self):
        rows=[]
        while not self.errors.empty():
            rows.append(self.errors.get_nowait())
        return rows


class ReadOnlyBroker(Broker):
    def submit(self,plan):
        raise RuntimeError('Preview adapter cannot submit')
