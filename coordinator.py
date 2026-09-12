"""A daily application, not a manually repeated script. Windows supplies wakeups.

Only never-attempted securities can enter submission. Unknown results are queried,
never resubmitted. Existing V2 intents remain authoritative across the upgrade.
"""
from datetime import time as T
import hashlib
import json
from pathlib import Path
import re
import time

from runtime import atomic_json, read_json
from support import Store, build_plan, positive_int
from market_calendar import CalendarService, CLOSED, UNKNOWN
from notification_policy import enqueue_calendar_issue
from privacy import redact

ACCEPTED={'REPORTED','SUCCEEDED'}
PENDING={'INTENT','UNCERTAIN','SUBMITTED','PENDING','PARTIAL'}
DONE=ACCEPTED|{'EXTERNAL_ACCEPTED','SKIPPED_QUOTA','SKIPPED_SCOPE','PREVIEW'}
STATUS_MAP={'reported':'REPORTED','succeeded':'SUCCEEDED','partial':'PARTIAL',
            'rejected':'REJECTED','canceled':'CANCELED'}


class Ledger(Store):
    def reserve(self,account,plan):
        with self.transaction():
            count=self.db.execute('INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,?,?)',
                (account,plan.day,plan.code,'INTENT',None,plan.quantity,time.time())).rowcount
        return bool(count)

    def record(self,account,day,code,state,order_id=None):
        with self.transaction():
            self.db.execute('''UPDATE intents SET state=?,order_id=COALESCE(?,order_id),updated=?
                WHERE account=? AND day=? AND code=?''',
                (state,order_id,time.time(),account,day,code))


def submit_window(now):
    t=now.time().replace(tzinfo=None)
    return T(9,35)<=t<T(11,30) or T(13)<=t<=T(14,50)


class Coordinator:
    def __init__(self,config,broker,ledger,now,mode='preview',calendar_decision=None):
        self.config,self.broker,self.ledger,self.now=config,broker,ledger,now
        self.calendar_decision=calendar_decision
        self.activity={'queried':False,'candidate_count':None,'submitted':0,'deferred':0,
                       'scope_skipped':0,'outcome':'starting','steps':[]}
        self.mode=mode
        if mode not in ('preview','live'):
            raise ValueError('Unknown mode')
        if mode=='live' and config.get('enable_execution') is not True:
            raise ValueError('Live execution not enabled')
        self.account=hashlib.sha256(config['account_id'].encode()).hexdigest()[:24]
        self.day=now().date().isoformat()
        self.path=Path(config['control_dir'])/'daily'/f'{self.day}-{mode}.json'
        self.doc=read_json(self.path) if self.path.exists() else {
            'day':self.day,'mode':mode,'account_digest':self.account,'phase':'new',
            'completed':False,'attempts':0,'empty_observations':0,'items':{},'errors':[]}
        if self.doc['account_digest'] != self.account or self.doc.get('mode')!=mode or self.doc.get('day')!=self.day:
            raise ValueError('Daily control record account/date/mode mismatch')

    def save(self):
        self.doc['updated_at']=self.now().isoformat()
        self.doc['last_activity']=dict(self.activity)
        atomic_json(self.path,self.doc)
        return self.doc

    def note(self,step):
        self.activity['steps'].append({'at':self.now().isoformat(),'step':step})

    def defer(self,item,stamp):
        if stamp.date().isoformat()==self.day and T(11,30)<=stamp.time().replace(tzinfo=None)<T(13):
            item.update(status='WAITING_WINDOW',reason='已查询，午间不提交；等待13:00申报')
            self.activity['deferred']+=1
        else:
            item.update(status='MISSED',reason='已超出本程序申报窗口，未提交')

    def message(self,key,text):
        self.ledger.event(f'v3:{self.account}:{self.day}:{self.mode}:{key}',
                          f'{self.day} 自动打新V3【'+('实盘' if self.mode=='live' else '预览、不下单')+'】\n'+text)

    def orders(self):
        rows=self.broker.orders()
        if not isinstance(rows,list):
            raise RuntimeError('委托查询没有返回完整列表')
        for row in rows:
            if row.get('day')!=self.day or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(row.get('code',''))):
                raise RuntimeError('委托数据日期或代码不符合合同')
        return rows

    def synchronize(self,orders):
        for row in self.ledger.records(self.account):
            if row['day']!=self.day:
                continue
            code=row['code']
            item=self.doc['items'].setdefault(code,{'code':code,'name':code,'kind':'UNKNOWN','quantity':row['quantity']})
            matches=[o for o in orders if o['code']==code]
            if row['order_id']:
                matches=[o for o in matches if o.get('order_id')==row['order_id']]
            else:
                expected=f"IPOv2_{self.day.replace('-','')}_{code}"
                matches=[o for o in matches if o.get('remark')==expected]
            if len(matches)==1:
                order=matches[0]
                state=STATUS_MAP.get(order.get('status'),'PENDING')
                self.ledger.record(self.account,self.day,code,state,order.get('order_id'))
                item.update(status=state,order_id=order.get('order_id'),reason='券商委托查询')
            else:
                item.update(status=row['state'],order_id=row['order_id'],reason='已有请求，当前未能唯一核对；不重发')
        for error in self.broker.drain_errors():
            for code,item in self.doc['items'].items():
                if ((item.get('order_id') and item['order_id']==error.get('id'))
                        or error.get('remark')==f"IPOv2_{self.day.replace('-','')}_{code}"):
                    item['broker_error']=error.get('message','券商错误回报')

    def summary(self):
        labels={'REPORTED':'已报','SUCCEEDED':'已成','EXTERNAL_ACCEPTED':'已有有效委托，跳过',
                'REJECTED':'废单待核对','CANCELED':'已撤待核对','PENDING':'等待回报',
                'SUBMITTED':'已提交待确认','INTENT':'意图已登记待核对','UNCERTAIN':'结果不确定，不重发',
                'PARTIAL':'部分成交待核对','SKIPPED_QUOTA':'无足额额度，跳过',
                'SKIPPED_SCOPE':'未启用的市场，跳过','PREVIEW':'计划预览，未下单',
                'RETRYABLE':'数据/额度待重试','MISSED':'窗口结束，未提交','EXTERNAL_BLOCKED':'已有异常委托，不重报',
                'WAITING_WINDOW':'已查询，等待申报时段'}
        phases={'complete':'本日处理完成','pending':'部分项目待核对或重试','no_ipo':'今日无申购项目',
                'final_attention':'收盘仍有项目需处理','retryable_error':'连接或查询待恢复',
                'waiting_afternoon':'午间已查询，等待13:00','no_eligible':'已查询，无范围内申购项目',
                'market_closed':'非交易日，已跳过','calendar_unknown':'日历未知，禁止提交'}
        lines=[f"状态：{phases.get(self.doc['phase'],self.doc['phase'])}；项目：{len(self.doc['items'])}只"]
        if not self.doc['items'] and self.doc['phase']=='no_ipo':
            lines.append('已分两轮查询确认，今日无新股、新债。')
        for code,item in sorted(self.doc['items'].items()):
            lines.append(f"{code} {item.get('name','')}｜{labels.get(item.get('status'),item.get('status','待处理'))}｜{item.get('quantity',0)}"+('张' if item.get('kind')=='BOND' else '股'))
            if item.get('broker_error'):
                lines.append(item['broker_error'])
            elif item.get('reason') and item.get('status') in ('SKIPPED_SCOPE','WAITING_WINDOW'):
                lines.append(item['reason'])
        lines.extend(self.doc.get('errors',[]))
        lines.append('委托已报/已成不代表中签；本程序不卖出、不缴款。')
        text=redact('\n'.join(lines),self.config)
        fingerprint=hashlib.sha256(text.encode()).hexdigest()[:16]
        self.message('summary:'+fingerprint,text)

    def run(self):
        stamp=self.now(); t=stamp.time().replace(tzinfo=None)
        self.doc['next_due']=None
        decision=self.calendar_decision or CalendarService(self.config).decide(self.day)
        if decision.day != self.day:
            raise ValueError('Calendar decision date mismatch')
        self.doc['calendar']=decision.to_dict()
        if decision.status in (CLOSED, UNKNOWN):
            self.activity['outcome']='market_closed' if decision.status==CLOSED else 'calendar_unknown'
            # Historical run receipts remain untouched. Do not clear previous
            # errors/items or pretend that old connection attempts succeeded.
            self.doc.update(phase=self.activity['outcome'], completed=False)
            self.doc['calendar_error']=decision.reason if decision.status==UNKNOWN else None
            self.note('非交易日，已跳过' if decision.status==CLOSED else '日历未知，禁止提交')
            if decision.status==UNKNOWN:
                enqueue_calendar_issue(self.config,decision)
            return self.save()
        if t<T(9,35):
            self.activity['outcome']='waiting_open'
            self.doc.update(phase='waiting_open',next_due=self.day+'T09:35:00+08:00')
            return self.save()
        lunch=T(11,30)<=t<T(13)
        if self.doc.get('completed') and t<T(15):
            # The daily work is done; only the final 15:05 reconciliation remains.
            self.activity['outcome']='already_complete'
            return self.doc
        self.doc['attempts']+=1
        self.doc['errors']=[]
        self.doc['phase']='running'
        self.save()
        connected=False
        try:
            self.note('开始连接QMT')
            self.broker.connect()
            connected=True
            self.broker.ready()
            self.note('连接与账户状态正常')
            self.synchronize(self.orders())
            raw=self.broker.ipos()
            if not isinstance(raw,dict):
                raise RuntimeError('申购数据不可用，不能当作没有新股新债')
            if any(not isinstance(i,dict) or i.get('type') not in ('STOCK','BOND') for i in raw.values()):
                raise RuntimeError('申购类型或数据结构异常')
            self.activity.update(queried=True,candidate_count=len(raw))
            self.note('申购数据查询完成：'+str(len(raw))+'条')
            if not raw and not self.doc['items']:
                previous=self.doc.get('last_empty_at')
                if previous is None or stamp.timestamp()-previous>=120:
                    self.doc['empty_observations']+=1
                    self.doc['last_empty_at']=stamp.timestamp()
                final=self.doc['empty_observations']>=2
                self.doc.update(phase='no_ipo' if final else ('final_attention' if t>T(14,50) else 'waiting_data'),completed=final)
                self.activity['outcome']=self.doc['phase']
                if lunch:
                    self.doc.update(phase='waiting_afternoon',completed=False,next_due=self.day+'T13:00:00+08:00')
                    self.activity['outcome']='queried_lunch'
                if final: self.summary()
                return self.save()
            if raw:
                self.doc['empty_observations']=0
            missing=set(self.doc['items'])-set(raw)
            if any(self.doc['items'][c].get('status') not in DONE for c in missing):
                self.doc['errors'].append('此前发现的申购项目在最新返回中缺失，保留原记录并继续核对。')
            for code,info in sorted(raw.items()):
                item=self.doc['items'].setdefault(code,{'code':code})
                item.update(name=str(info.get('name',code))[:80],kind=info['type'])
                prior=self.ledger.get(self.account,self.day,code)
                if prior:
                    # Always honor old-version/manual-run intent before any quota calculation.
                    item.update(status=prior['state'],quantity=prior['quantity'],order_id=prior['order_id'])
                    continue
                market='KCB' if info['type']=='STOCK' and code.endswith('.SH') and code.startswith(('688','689','787','789')) else code[-2:]
                if market not in self.config['allowed_markets']:
                    reason='本程序未启用北交所市场，已跳过；未据此判断券商权限' if market=='BJ' else '本程序未启用该市场，已跳过；未据此判断券商权限'
                    item.update(status='SKIPPED_SCOPE',quantity=0,reason=reason)
                    self.activity['scope_skipped']+=1
                    continue
                try:
                    limits=self.broker.limits() if info['type']=='STOCK' else {}
                    plan=build_plan(code,info,limits,self.day,self.config['allowed_markets'])
                    item.update(quantity=plan.quantity,price=plan.price)
                except Exception as exc:
                    item.update(status='RETRYABLE',reason=type(exc).__name__)
                    continue
                # The second read includes manual orders and other programs.
                orders=self.orders()
                existing=[o for o in orders if o['code']==code]
                if existing:
                    item['status']='EXTERNAL_ACCEPTED' if any(o.get('status') in ('reported','succeeded') for o in existing) else 'EXTERNAL_BLOCKED'
                    continue
                if not plan.quantity:
                    item['status']='SKIPPED_QUOTA'
                    continue
                if not submit_window(self.now()) or self.now().date().isoformat()!=self.day:
                    self.defer(item,self.now())
                    continue
                if self.mode=='preview':
                    item['status']='PREVIEW'
                    continue
                self.broker.ready()  # Last read-only readiness check before durable intent.
                current=self.now()
                if not submit_window(current) or current.date().isoformat()!=self.day:
                    self.defer(item,current)
                    continue
                if not self.ledger.reserve(self.account,plan):
                    item['status']='INTENT'
                    continue
                item['status']='INTENT'
                self.save()
                try:
                    order_id=positive_int(self.broker.submit(plan),'委托号')
                except Exception as exc:
                    self.ledger.record(self.account,self.day,code,'UNCERTAIN')
                    item.update(status='UNCERTAIN',reason=type(exc).__name__)
                else:
                    self.ledger.record(self.account,self.day,code,'SUBMITTED',order_id)
                    item.update(status='SUBMITTED',order_id=order_id)
                    self.activity['submitted']+=1
            self.synchronize(self.orders())
            t=self.now().time().replace(tzinfo=None)
            lunch=T(11,30)<=t<T(13)
            self.doc['completed']=bool(self.doc['items']) and all(i.get('status') in DONE for i in self.doc['items'].values()) and not self.doc['errors']
            self.doc['phase']='complete' if self.doc['completed'] else ('final_attention' if t>T(14,50) else 'pending')
            if lunch:
                # Noon observations are useful, but do not suppress the 13:00 refresh.
                self.doc.update(phase='waiting_afternoon',completed=False,next_due=self.day+'T13:00:00+08:00')
                self.activity['outcome']='queried_lunch'
            else:
                if self.doc['completed'] and all(i.get('status')=='SKIPPED_SCOPE' for i in self.doc['items'].values()):
                    self.doc['phase']='no_eligible'
                self.activity['outcome']=self.doc['phase']
            self.note('本轮完成：提交'+str(self.activity['submitted'])+'笔')
            self.summary()
        except Exception as exc:
            self.activity['outcome']='query_error' if connected else 'connection_error'
            self.activity['error_code']='qmt_query_failed' if connected else 'qmt_connection_failed'
            self.doc.update(completed=False,phase='final_attention' if t>T(14,50) else 'retryable_error')
            self.doc['errors']=[f'连接/查询未完成（{type(exc).__name__}）；仅未发起过的项目允许后续尝试。']
            # One notification per error type / 30-minute bucket, not every poll.
            self.message(f'error:{type(exc).__name__}:{int(stamp.timestamp()//1800)}',self.doc['errors'][0])
        finally:
            try:
                self.broker.close()
            except Exception:
                self.doc['errors'].append('本次SDK连接释放失败，请检查进程；没有重启客户端。')
        return self.save()
