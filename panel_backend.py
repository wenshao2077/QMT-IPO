"""Desktop control boundary. Construction/snapshot never enables or runs trading.

Only explicit, confirmed GUI actions call Enable/Pause. Tests use fake controllers.
"""
import base64
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from runtime import read_json
from run_history import load_runs
from support import china_now
from market_calendar import CalendarService
from notification_policy import notification_stats, combined_notification_stats
from calendar_health import coverage_report

TASK_NAMES=('QmtIPO3-Cycle','QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup')
PHASES={'complete':'截至本轮处理完成','no_ipo':'截至本轮未发现申购项目','no_eligible':'无范围内项目','pending':'部分项目待处理',
        'retryable_error':'等待连接或数据恢复','final_attention':'收盘仍需核对',
        'waiting_open':'等待开盘后触发','waiting_afternoon':'等待下午交易时段',
        'market_closed':'非交易日，已跳过','calendar_unknown':'日历未知，禁止提交','waiting_data':'等待数据复核','running':'正在处理'}
ITEM_STATES={'REPORTED':'券商已报','SUCCEEDED':'券商已成','EXTERNAL_ACCEPTED':'已有委托，已跳过',
             'REJECTED':'废单待核对','CANCELED':'已撤待核对','PENDING':'等待回报',
             'SUBMITTED':'已提交待确认','INTENT':'请求已登记','UNCERTAIN':'结果待核对',
             'PARTIAL':'部分成交待核对','SKIPPED_QUOTA':'额度不足','SKIPPED_SCOPE':'不在启用范围',
             'PREVIEW':'仅预览','RETRYABLE':'等待重试','MISSED':'已错过窗口','EXTERNAL_BLOCKED':'已有异常委托',
             'WAITING_WINDOW':'已查询，等待13:00'}


def plan_state(snapshot):
    """Never infer enabled merely from a config boolean or an inaccessible task."""
    if not snapshot or not snapshot.get('ok'):
        return 'unknown','状态暂不可用'
    tasks={r['name']:r for r in snapshot.get('tasks',[])}
    if any(name not in tasks or not tasks[name].get('exists') for name in TASK_NAMES):
        return 'inconsistent','计划任务待修复'
    cycle=tasks['QmtIPO3-Cycle']['enabled']
    flag=snapshot.get('execution_enabled',False)
    if flag and cycle and all(tasks[n]['enabled'] for n in TASK_NAMES):
        return 'enabled','每日自动打新已启用'
    if not flag and not cycle:
        return 'disabled','每日自动打新未启用'
    return 'inconsistent','配置与任务状态不一致'


def _ps_quote(value):
    return "'"+str(value).replace("'","''")+"'"


class WindowsController:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

    def command(self,operation,request_id=None,confirmed=False):
        if operation not in ('Snapshot','Prepare','Enable','Pause'):
            raise ValueError('不支持的控制操作')
        if operation in ('Enable','Pause') and not confirmed:
            raise ValueError('需要本人点击确认按钮')
        request_id=request_id or uuid.uuid4().hex
        if not re.fullmatch('[a-f0-9]{32}',request_id):
            raise ValueError('请求编号无效')
        # Read UTF-8 explicitly so native Windows PowerShell 5.1 does not misdecode Chinese.
        command="& ([scriptblock]::Create([IO.File]::ReadAllText("+_ps_quote(self.root/'panel_tasks.ps1')+",[Text.Encoding]::UTF8))) -Root "+_ps_quote(self.root)+" -Operation "+operation+" -RequestId "+request_id
        if confirmed:command+=' -Consent'
        encoded=base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        return [str(self.powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',encoded]

    def call(self,operation,confirmed=False):
        process=subprocess.run(self.command(operation,confirmed=confirmed),capture_output=True,
                               encoding='utf-8-sig',errors='replace',timeout=45,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for line in reversed(process.stdout.splitlines()):
            try:
                result=json.loads(line)
                if isinstance(result,dict):return result
            except ValueError:pass
        return {'ok':False,'error':'Windows未返回可读的控制结果，请查看程序日志。'}

    def elevate_prepare(self):
        if sys.platform!='win32':raise RuntimeError('仅Windows支持系统授权')
        from ctypes import wintypes as w
        class ShellExecuteInfo(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),
                      ('lpFile',w.LPCWSTR),('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),
                      ('nShow',ctypes.c_int),('hInstApp',w.HINSTANCE),('lpIDList',ctypes.c_void_p),
                      ('lpClass',w.LPCWSTR),('hkeyClass',w.HKEY),('dwHotKey',w.DWORD),
                      ('hIcon',w.HANDLE),('hProcess',w.HANDLE)]
        rid=uuid.uuid4().hex
        args=self.command('Prepare',rid)
        info=ShellExecuteInfo();info.cbSize=ctypes.sizeof(info);info.fMask=0x40
        info.lpVerb='runas';info.lpFile=args[0]
        info.lpParameters=subprocess.list2cmdline(args[1:]);info.lpDirectory=str(self.root);info.nShow=0
        shell=ctypes.WinDLL('shell32',use_last_error=True)
        shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ShellExecuteInfo)];shell.ShellExecuteExW.restype=w.BOOL
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            error=ctypes.get_last_error()
            if error==1223:return {'ok':False,'error':'已取消Windows系统授权；没有启用每日申购。'}
            raise ctypes.WinError(error)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
        kernel.CloseHandle.argtypes=[w.HANDLE]
        try:
            if kernel.WaitForSingleObject(info.hProcess,180000)==258:
                return {'ok':False,'error':'系统授权仍未结束。请完成或关闭授权窗口，再刷新状态。'}
        finally:kernel.CloseHandle(info.hProcess)
        receipt=Path(read_json(self.root/'config.json')['control_dir'])/'panel-actions'/(rid+'.json')
        return read_json(receipt) if receipt.exists() else {'ok':False,'error':'系统权限准备未完成；没有启用每日申购。'}


class Backend:
    def __init__(self,root,controller=None):
        self.root=Path(root).resolve()
        self.controller=controller or WindowsController(self.root)

    def snapshot(self):
        snap=self.controller.call('Snapshot')
        if not snap.get('ok'):return snap
        now=china_now();day=now.strftime('%Y-%m-%d')
        control=Path(snap['control_dir'])
        daily=control/'daily'/(day+'-live.json')
        snap['daily']=None;snap['data_errors']=[]
        try:
            if daily.is_file():snap['daily']=read_json(daily)
        except (OSError,ValueError):snap['data_errors'].append('daily_unavailable')
        try:
            cfg=read_json(self.root/'config.json')
        except (OSError,ValueError):
            cfg={'state_dir':snap['state_dir'],'control_dir':snap['control_dir'],
                 'enable_execution':snap.get('execution_enabled',False),
                 'allowed_markets':snap.get('markets') or ['SH','SZ']}
        snap['calendar']=CalendarService(cfg).decide(day).to_dict()
        combined=combined_notification_stats(cfg)
        snap['notification_status']=combined['queues']
        snap['pending_notifications']=combined['pending']
        snap['failed_notifications']=combined['failed']
        snap['suppressed_notifications']=combined['suppressed']
        snap['last_notification_confirmed_at']=combined['last_confirmed_at']
        snap['notifications_readable']=combined['readable']
        if not combined['readable']:
            snap['data_errors'].append('notification_status_unavailable')
        try:
            snap['calendar_coverage']=coverage_report(cfg,day)
            from app import inspect_health
            snap['local_health']=inspect_health(cfg,now)
        except (OSError,ValueError,TypeError,KeyError):
            snap['local_health']={'status':'unknown','issues':['local_health_unavailable']}
        try:snap['latest_cycle']=read_json(control/'latest-cycle-live.json')
        except (OSError,ValueError):snap['latest_cycle']=None
        history=[]
        events=control/'panel-actions'
        if events.is_dir():
            for path in sorted(events.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                if not re.fullmatch('[a-f0-9]{32}.json',path.name):continue
                try:row=read_json(path)
                except (OSError,ValueError):continue
                history.append({'operation':row.get('operation'),'ok':row.get('ok'),
                                'time':row.get('finished_at'),'error':row.get('error','')})
                if len(history)>=8:break
        snap['history']=history
        snap['runs']=load_runs(control,day)
        return snap

    def change(self,operation,confirmed=False):
        if operation not in ('Enable','Pause') or not confirmed:
            raise ValueError('授权或暂停必须由本人在界面确认')
        snap=self.controller.call('Snapshot')
        if not snap.get('ok'):return snap
        if snap.get('needs_admin'):
            prepared=self.controller.elevate_prepare()
            if not prepared.get('ok'):return prepared
            snap=self.controller.call('Snapshot')
            if not snap.get('ok') or snap.get('needs_admin'):
                return {'ok':False,'error':'权限准备后仍无法管理任务，未改变实盘开关。'}
        return self.controller.call(operation,confirmed=True)

    def check_connection(self):
        command=[str(self.root/'.venv/Scripts/python.exe'),'-B',str(self.root/'code/probe_readonly.py'),
                 '--config',str(self.root/'config.json')]
        env=dict(os.environ,PYTHONIOENCODING='utf-8')
        result=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',env=env,
                              timeout=40,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for line in reversed(result.stdout.splitlines()):
            try:
                data=json.loads(line)
                if isinstance(data,dict):return data
            except ValueError:pass
        return {'status':'failed','error_type':'NoReadableResponse'}

    def validate_configuration(self):
        from diagnostics import configuration_report
        return configuration_report(read_json(self.root/'config.json'))

    def test_notification(self,confirmed=False):
        if not confirmed:raise ValueError('发送测试消息必须明确确认')
        from diagnostics import test_notification
        return test_notification(read_json(self.root/'config.json'),confirmed=True)

    def export_support(self, output, confirmed=False):
        from maintenance import export_support
        return export_support(self.root, output, confirmed=confirmed,
                              task_provider=lambda _: self.controller.call('Snapshot'))

    def recovery_plan(self):
        from maintenance import recovery_plan
        return recovery_plan(self.root, task_provider=lambda _: self.controller.call('Snapshot'))

    def open_logs(self):
        config=read_json(self.root/'config.json')
        os.startfile(str(Path(config['control_dir'])))
