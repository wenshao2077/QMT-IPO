"""Read-only presentation of immutable run receipts, including pre-3.2 records."""
from datetime import datetime
from pathlib import Path
import json

ACTION_NAMES={'cycle':'自动申购','notify':'通知补发','monitor':'运行监控','backup':'账本备份'}
ISSUES={'daily_work_incomplete':'当日有未完成项目','daily_run_missing':'当日任务漏跑',
        'cycle_stuck':'运行超时','calendar_unavailable':'日历不可用','closeout_failed':'收盘核对未完成',
        'daily_record_missing':'运行记录缺失','notification_backlog':'通知积压','ledger_unavailable':'账本不可读'}


def item_reason(code,item):
    if item.get('status')=='SKIPPED_SCOPE' and code.endswith('.BJ'):
        return '北交所无权限，已跳过'
    return item.get('reason','')


def describe_run(record):
    result=record.get('result') or {}; activity=record.get('activity') or {}
    action=record.get('action');phase=result.get('phase')
    if record.get('status')=='running':return '运行中'
    if record.get('status') in ('failed','startup_failed'):return '运行失败：'+record.get('error_type','未知错误')
    if action=='notify':
        n=result.get('pending_notifications')
        return '通知队列无待发消息' if n==0 else f'仍有 {n} 条待补发' if n is not None else '通知状态未记录'
    if action=='monitor':
        issues=result.get('issues',[])
        if issues:return '监控提示：'+'、'.join(ISSUES.get(i,i) for i in issues)
        return {'not_activated':'未启用，监控待命','outside_window':'监控时段外','market_closed':'非交易日'}.get(result.get('status'),'监控正常')
    if action=='backup':return '备份完成' if result.get('status')=='backup_complete' else '备份结果见详情'
    outcome=activity.get('outcome')
    if outcome=='already_complete':return '本日已处理，本轮跳过'
    if outcome=='queried_lunch':return '已查询，午间不提交'
    if phase=='waiting_afternoon':return '等待13:00（本轮未查询）' if result.get('attempts')==0 else '等待下午申报时段'
    if phase=='waiting_open':return '等待09:35触发'
    if phase=='no_eligible':return '已查询，范围内无申购项目'
    if phase=='no_ipo':return '两轮查询确认无申购记录'
    if phase=='waiting_data':return '首次数据为空，待再次复核'
    if phase=='retryable_error':return '连接或查询失败，待重试'
    if phase=='final_attention':return '收盘仍有未完成项目'
    if phase=='pending':return '部分项目待处理或核对'
    if phase=='market_closed':return '非交易日，未申购'
    if phase=='complete':
        return f"本轮提交 {activity['submitted']} 笔，已核对" if activity.get('submitted') else '处理完成，详见项目结果'
    return '已结束，详见回执'


def make_row(path,record):
    result=record.get('result') or {}; activity=record.get('activity') or {}
    old_skipped=not activity and result.get('phase') in ('waiting_open','waiting_afternoon') and result.get('attempts',0)==0
    duration=record.get('duration_seconds')
    if duration is None and record.get('finished_at') and record.get('started_at'):
        try:duration=max(0,(datetime.fromisoformat(record['finished_at'])-datetime.fromisoformat(record['started_at'])).total_seconds())
        except (TypeError,ValueError):pass
    started=record.get('started_at','')
    try:clock=datetime.fromisoformat(started).strftime('%H:%M:%S')
    except (TypeError,ValueError):clock='未知时间'
    queried=activity.get('queried')
    query_text=(f"返回 {activity.get('candidate_count',0)} 条" if queried else '未查询') if queried is not None else ('未查询' if old_skipped else '未统计')
    submitted=activity.get('submitted',0 if old_skipped else None)
    count_text=query_text+' / '+(str(submitted)+'笔提交' if submitted is not None else '提交数未统计')
    if record.get('action')!='cycle':count_text='—'
    description=describe_run(record)
    lines=[f"任务：{ACTION_NAMES.get(record.get('action'),record.get('action','未知'))}",
           f"开始：{started}",f"结束：{record.get('finished_at') or '尚未结束'}",f"结果：{description}",
           f"本轮统计：{count_text}"]
    if result.get('next_due'):lines.append('下一步时间：'+result['next_due'])
    for step in activity.get('steps',[]):lines.append(step.get('at','')+'  '+step.get('step',''))
    for code,item in sorted(result.get('items',{}).items()):
        lines.append(f"{code} {item.get('name','')}：{item.get('status','未知')}，{item_reason(code,item)}")
    lines.extend(str(e) for e in result.get('errors',[]))
    return {'id':path.name,'started_at':started,'time':clock,'action':ACTION_NAMES.get(record.get('action'),record.get('action','未知')),
            'result':description,'counts':count_text,'duration':'—' if duration is None else f'{duration:.2f}s',
            'detail':'\n'.join(lines),'is_cycle':record.get('action')=='cycle'}


def load_runs(control,day,limit=500):
    folder=Path(control)/'runs'/day
    if not folder.is_dir():return []
    rows=[]
    for path in folder.glob('*.json'):
        try:
            record=json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(record,dict):raise ValueError('invalid receipt')
            rows.append(make_row(path,record))
        except (OSError,ValueError,TypeError,KeyError):
            rows.append({'id':path.name,'started_at':'','time':'—','action':'回执读取','result':'记录不可读，未隐瞒为成功',
                         'counts':'—','duration':'—','detail':path.name,'is_cycle':False})
    return sorted(rows,key=lambda r:(r['started_at'],r['id']),reverse=True)[:limit]
