"""Scheduled entry: cycle, notify, monitor, backup, status. No interactive prompt.

Default cycle is preview. Live requires --live AND enable_execution=true, provided
only after the owner's one-time task activation. Never call live mode in QA.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import time
import uuid

from broker import Broker, ReadOnlyBroker
from coordinator import Coordinator, Ledger
from runtime import atomic_json, backup, read_json, single_instance, validate_config
from support import Store, china_now, wecom_sender

VERSION='3.2.0'


def inspect_health(config,now=None):
    now=now or china_now(); root=Path(config['control_dir'])
    active=config.get('enable_execution') is True
    info={'day':now.date().isoformat(),'checked_at':now.isoformat(),'active':active,'issues':[]}
    if not active:
        return info|{'status':'not_activated'}
    if now.weekday()>=5 or not '09:25'<=now.strftime('%H:%M')<='16:20':
        return info|{'status':'outside_window'}
    try:
        calendar=read_json(Path(config['state_dir'])/'calendar.json')
        if info['day']>calendar['covered_through']:
            raise ValueError('stale calendar')
        if info['day'] not in calendar['days']:
            return info|{'status':'market_closed'}
    except Exception:
        info['issues'].append('calendar_unavailable')
    try:
        receipt=read_json(root/'latest-cycle-live.json')
        age=(now-datetime.fromisoformat(receipt['started_at'])).total_seconds()
        if receipt.get('status')=='running' and age>210:
            info['issues'].append('cycle_stuck')
        if receipt['day']!=info['day']:
            raise ValueError('prior day')
    except Exception:
        if now.strftime('%H:%M')>='09:45':
            info['issues'].append('daily_run_missing')
    try:
        doc=read_json(root/'daily'/f"{info['day']}-live.json")
        info['phase']=doc['phase']
        waiting=False
        if doc['phase'] in ('waiting_open','waiting_afternoon') and doc.get('next_due') and not doc.get('errors'):
            due=datetime.fromisoformat(doc['next_due'])
            safe={'SKIPPED_SCOPE','SKIPPED_QUOTA','WAITING_WINDOW','REPORTED','SUCCEEDED','EXTERNAL_ACCEPTED','PREVIEW'}
            waiting=now<due and all(i.get('status') in safe for i in doc.get('items',{}).values())
        if not doc.get('completed') and not waiting and now.strftime('%H:%M')>='09:45':
            info['issues'].append('daily_work_incomplete')
        if not doc.get('completed') and now.strftime('%H:%M')>='15:10':
            info['issues'].append('closeout_failed')
    except Exception:
        if now.strftime('%H:%M')>='09:45':
            info['issues'].append('daily_record_missing')
    try:
        conn=sqlite3.connect((Path(config['state_dir'])/'state.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            count,attempts=conn.execute('SELECT COUNT(*),COALESCE(MAX(attempts),0) FROM outbox WHERE delivered=0').fetchone()
            if count and attempts>=2:
                info['issues'].append('notification_backlog')
            info['pending_notifications']=count
        finally: conn.close()
    except Exception:
        info['issues'].append('ledger_unavailable')
    return info|{'status':'attention' if info['issues'] else 'healthy'}


def watch(config,send=False,now=None):
    now=now or china_now(); root=Path(config['control_dir'])
    snapshot=inspect_health(config,now)
    statepath=root/'monitor-state.json'
    before=read_json(statepath) if statepath.exists() else {'issues':[],'count':0,'alerted':False,'last_alert':0}
    if snapshot['status'] not in ('healthy','attention'):
        atomic_json(root/'health.json',snapshot)
        return snapshot
    issues=sorted(snapshot['issues'])
    count=before.get('count',0)+1 if issues==before.get('issues') else 1
    alerted=before.get('alerted',False); last=before.get('last_alert',0)
    text=None
    if issues and count>=2 and (issues!=before.get('last_alert_issues') or now.timestamp()-last>=1800):
        labels={'calendar_unavailable':'日历不可用','cycle_stuck':'任务长时间未结束',
                'daily_run_missing':'今日定时任务漏跑','daily_work_incomplete':'今日仍有未完成项目',
                'closeout_failed':'收盘核对未完成','daily_record_missing':'今日运行记录缺失',
                'notification_backlog':'通知连续失败并积压','ledger_unavailable':'去重账本不可读'}
        text='自动打新异常：'+'；'.join(labels.get(i,i) for i in issues)+'。请查看每日状态；监控未重启、未补单。'
        last=now.timestamp(); alerted=True
    elif not issues and alerted:
        text='自动打新链路恢复；以每日明细与券商委托状态为准。'
        alerted=False
    # A separate DB means the monitor can alert even if the main ledger is unavailable.
    store=Store(root/'monitor.sqlite3')
    try:
        if text:
            store.event(f'monitor:{now.date()}:{last}:{bool(issues)}',f'{now:%Y-%m-%d %H:%M}\n{text}')
        atomic_json(statepath,{'issues':issues,'count':count,'alerted':alerted,'last_alert':last,
                               'last_alert_issues':issues if text and issues else before.get('last_alert_issues')})
        if send:
            store.drain(wecom_sender(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip()))
        snapshot['pending_monitor_notifications']=store.pending_notifications()
    finally: store.close()
    atomic_json(root/'health.json',snapshot)
    return snapshot


def dispatch(action,config,live=False,send=False,broker_factory=None,now=china_now):
    config=validate_config(config)
    if live and config['enable_execution'] is not True:
        raise ValueError('Live task has not been activated')
    if live and not send:
        raise ValueError('Live mode requires notifications')
    if live and action!='cycle':
        raise ValueError('Only cycle supports live mode')
    root=Path(config['control_dir']); shared=Path(config['state_dir'])
    if live:
        # An upgraded live worker must never silently reinitialize a lost ledger.
        path=shared/'state.sqlite3'
        if not path.is_file():
            raise ValueError('Shared order ledger missing; refuse automatic recreation')
        check=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            tables={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'intents','outbox'}<=tables:
                raise ValueError('Shared order ledger schema is missing')
        finally:check.close()
    mode='live' if live or (action=='status' and config['enable_execution']) else 'preview'
    if action=='status':
        path=root/'daily'/f'{now().date().isoformat()}-{mode}.json'
        return read_json(path) if path.exists() else {'status':'not_run','mode':mode}
    # Reuse V2's single-instance lock for all order/notification operations.
    lock=root/'monitor.lock' if action=='monitor' else shared/'instance.lock'
    with single_instance(lock):
        started=now(); rid=uuid.uuid4().hex
        receipt={'version':VERSION,'day':str(started.date()),'action':action,'mode':mode,
                 'run_id':rid,'started_at':started.isoformat(),'status':'running'}
        receiptpath=root/'runs'/str(started.date())/(action+'-'+rid+'.json')
        latest=root/f'latest-{action}-{mode}.json'
        atomic_json(receiptpath,receipt); atomic_json(latest,receipt)
        result={}
        try:
            if action=='monitor':
                result=watch(config,send,now())
            elif action=='backup':
                target=root/'backups'/(str(started.date())+'-'+rid[:8]+'.sqlite3')
                backup(shared/'state.sqlite3',target)
                result={'status':'backup_complete','file':target.name}
            else:
                store=Ledger(shared/'state.sqlite3')
                try:
                    if action=='cycle':
                        factory=broker_factory or (Broker if live else ReadOnlyBroker)
                        coordinator=Coordinator(config,factory(config),store,now,mode)
                        result=coordinator.run()
                        receipt['activity']=getattr(coordinator,'activity',{})
                    elif action!='notify':
                        raise ValueError('Unknown action')
                    if send:
                        pending=store.drain(wecom_sender(Path(config['webhook_file']).read_text(encoding='utf-8-sig').strip()))
                    else: pending=store.pending_notifications()
                    result=dict(result,pending_notifications=pending)
                finally: store.close()
            receipt.update(status='finished',finished_at=now().isoformat(),result=result)
        except Exception as exc:
            receipt.update(status='failed',finished_at=now().isoformat(),error_type=type(exc).__name__)
        receipt['duration_seconds']=max(0,(datetime.fromisoformat(receipt['finished_at'])-started).total_seconds())
        atomic_json(receiptpath,receipt); atomic_json(latest,receipt)
        return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description='每日自动打新：计划任务驱动，默认只读预览')
    parser.add_argument('action',choices=['cycle','notify','monitor','backup','status'])
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--send',action='store_true')
    args=parser.parse_args(argv)
    try:
        with redirect_stdout(io.StringIO()):
            result=dispatch(args.action,read_json(args.config),args.live,args.send)
        print(json.dumps(result,ensure_ascii=False))
        if result.get('status')=='failed': return 1
        detail=result.get('result',result)
        if detail.get('phase') in ('pending','retryable_error','final_attention'): return 2
        if detail.get('pending_notifications',0): return 3
        return 0
    except Exception as exc:
        print(json.dumps({'status':'startup_failed','error_type':type(exc).__name__}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
