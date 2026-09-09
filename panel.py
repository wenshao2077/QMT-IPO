"""Native desktop UI. Opening this window is read-only; trading needs a user click."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from panel_backend import Backend, ITEM_STATES, PHASES, TASK_NAMES, plan_state
from run_history import item_reason

BG='#eef2f7'; WHITE='#ffffff'; INK='#17243b'; MUTED='#69788e'; BLUE='#2859d8'
SIDEBAR='#14213a'; GREEN='#087e67'; AMBER='#9a6700'; RED='#bb3545'; LINE='#dce3ed'
FONT=('Microsoft YaHei UI',10); SMALL=('Microsoft YaHei UI',9)


def label(parent,text='',size=10,bold=False,fg=INK,bg=WHITE,**kwargs):
    return tk.Label(parent,text=text,font=('Microsoft YaHei UI',size,'bold' if bold else 'normal'),
                    fg=fg,bg=bg,anchor='w',**kwargs)


def button(parent,text,command,primary=False):
    return tk.Button(parent,text=text,command=command,font=('Microsoft YaHei UI',10,'bold'),
                     relief='flat',bd=0,padx=20,pady=10,cursor='hand2',
                     bg=BLUE if primary else '#e8edf5',fg=WHITE if primary else INK,
                     activebackground='#2049b5' if primary else '#dae2ee',
                     activeforeground=WHITE if primary else INK,disabledforeground='#91a0b5')


class ConsentDialog(tk.Toplevel):
    def __init__(self,parent,title,text,confirm_text):
        super().__init__(parent)
        self.result=False
        self.title(title);self.configure(bg=WHITE);self.resizable(False,False)
        self.transient(parent);self.protocol('WM_DELETE_WINDOW',self.cancel)
        box=tk.Frame(self,bg=WHITE,padx=28,pady=24);box.pack(fill='both',expand=True)
        label(box,title,17,True).pack(fill='x',pady=(0,18))
        label(box,text,11,wraplength=520,justify='left').pack(fill='x',pady=(0,24))
        actions=tk.Frame(box,bg=WHITE);actions.pack(fill='x')
        cancel=button(actions,'暂不操作',self.cancel);cancel.pack(side='left')
        button(actions,confirm_text,self.accept,True).pack(side='right')
        self.update_idletasks()
        w=max(570,self.winfo_reqwidth());h=self.winfo_reqheight()
        x=parent.winfo_rootx()+(parent.winfo_width()-w)//2
        y=parent.winfo_rooty()+(parent.winfo_height()-h)//2
        self.geometry(f'{w}x{h}+{max(0,x)}+{max(0,y)}')
        self.bind('<Escape>',lambda e:self.cancel());cancel.focus_set();self.grab_set()
    def accept(self):self.result=True;self.destroy()
    def cancel(self):self.destroy()


class Panel:
    def __init__(self,root,backend,autorefresh=True,confirm=None):
        self.root,self.backend=root,backend
        self.confirm=confirm or self.ask
        self.snapshot=None;self.busy=False;self.refreshing=False;self.closed=False;self.feedback_latched=False;self.action_count=0
        self.events=queue.Queue();self.buttons={};self.cards={};self.run_rows={}
        self.after_ids=set()
        root.title('自动打新控制台');root.geometry('1160x780');root.minsize(1000,680);root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('TNotebook',background=BG,borderwidth=0)
        style.configure('TNotebook.Tab',font=FONT,padding=(18,10),background='#e7edf5',foreground=MUTED)
        style.map('TNotebook.Tab',background=[('selected',WHITE)],foreground=[('selected',INK)])
        style.configure('Treeview',font=SMALL,rowheight=34,background=WHITE,fieldbackground=WHITE,foreground=INK,borderwidth=0)
        style.configure('Treeview.Heading',font=('Microsoft YaHei UI',9,'bold'),background='#f2f5fa',foreground=MUTED,relief='flat')
        style.map('Treeview',background=[('selected','#e2ebff')],foreground=[('selected',INK)])
        sidebar=tk.Frame(root,bg=SIDEBAR,width=190);sidebar.pack(side='left',fill='y');sidebar.pack_propagate(False)
        label(sidebar,'QMT',28,True,fg='#80b9ff',bg=SIDEBAR).pack(anchor='w',padx=26,pady=(30,2))
        label(sidebar,'自动打新',19,True,fg=WHITE,bg=SIDEBAR).pack(anchor='w',padx=26)
        label(sidebar,'本机控制台',10,fg='#a8b8d2',bg=SIDEBAR).pack(anchor='w',padx=26,pady=(6,30))
        tk.Frame(sidebar,bg='#334460',height=1).pack(fill='x',padx=24,pady=8)
        for text in ('每天自动运行','新股 + 新债','回执与通知','任务异常监控'):
            label(sidebar,'  •  '+text,10,fg='#cad6e8',bg=SIDEBAR).pack(fill='x',padx=18,pady=10)
        label(sidebar,'桌面版 3.2\n不开放网络端口',9,fg='#94a7c5',bg=SIDEBAR,justify='left').pack(side='bottom',anchor='w',padx=26,pady=26)
        content=tk.Frame(root,bg=BG,padx=26,pady=22);content.pack(side='left',fill='both',expand=True)
        head=tk.Frame(content,bg=BG);head.pack(fill='x',pady=(0,16))
        label(head,'每日自动打新',23,True,bg=BG).pack(side='left')
        self.clock=label(head,'',9,fg=MUTED,bg=BG);self.clock.pack(side='right',anchor='s')
        hero=tk.Frame(content,bg=WHITE,padx=22,pady=18,highlightbackground=LINE,highlightthickness=1);hero.pack(fill='x')
        self.status=label(hero,'正在读取实际运行状态…',17,True,fg=AMBER);self.status.pack(fill='x')
        self.subtitle=label(hero,'打开控制台不会自动授权，也不会提交申购。',10,fg=MUTED,wraplength=780,justify='left');self.subtitle.pack(fill='x',pady=(7,14))
        actions=tk.Frame(hero,bg=WHITE);actions.pack(fill='x')
        self.buttons['enable']=button(actions,'授权并启用每日打新',self.enable,True);self.buttons['enable'].pack(side='left',padx=(0,10))
        self.buttons['pause']=button(actions,'暂停每日打新',self.pause);self.buttons['pause'].pack(side='left',padx=(0,10))
        self.buttons['check']=button(actions,'检查 QMT 连接',self.check);self.buttons['check'].pack(side='right')
        self.feedback=label(hero,'正在检查计划任务…',9,fg=MUTED,wraplength=780,justify='left');self.feedback.pack(fill='x',pady=(12,0))
        self.last_run=label(hero,'最近自动触发：待读取',9,fg=MUTED,wraplength=780,justify='left');self.last_run.pack(fill='x',pady=(6,0))
        metrics=tk.Frame(content,bg=BG);metrics.pack(fill='x',pady=15)
        for i,(key,title) in enumerate((('today','今日处理'),('next','下一次触发'),('notify','通知队列'))):
            metrics.columnconfigure(i,weight=1,uniform='cards')
            card=tk.Frame(metrics,bg=WHITE,padx=16,pady=12,highlightbackground=LINE,highlightthickness=1)
            card.grid(row=0,column=i,sticky='nsew',padx=(0,12 if i<2 else 0))
            label(card,title,9,fg=MUTED).pack(fill='x')
            value=label(card,'—',17,True);value.pack(fill='x',pady=(5,0));self.cards[key]=value
        notebook=ttk.Notebook(content);notebook.pack(fill='both',expand=True)
        runs=tk.Frame(notebook,bg=WHITE,padx=14,pady=14)
        daily=tk.Frame(notebook,bg=WHITE,padx=14,pady=14)
        settings=tk.Frame(notebook,bg=WHITE,padx=20,pady=16)
        history=tk.Frame(notebook,bg=WHITE,padx=16,pady=16)
        notebook.add(runs,text='运行记录');notebook.add(daily,text='今日明细');notebook.add(settings,text='计划与环境');notebook.add(history,text='授权/暂停记录')
        self.run_note=label(runs,'每次自动触发都会列出；双击查看详情。',9,fg=MUTED);self.run_note.pack(fill='x',pady=(0,8))
        runbox=tk.Frame(runs,bg=WHITE);runbox.pack(fill='both',expand=True)
        self.run_table=ttk.Treeview(runbox,columns=('time','action','result','counts','duration'),show='headings',height=6,selectmode='browse')
        for col,title,width in zip(('time','action','result','counts','duration'),('触发时间','任务','实际结果','本轮查询 / 提交','耗时'),(85,90,240,160,65)):
            self.run_table.heading(col,text=title);self.run_table.column(col,width=width,minwidth=55,stretch=col=='result')
        runscroll=ttk.Scrollbar(runbox,orient='vertical',command=self.run_table.yview);self.run_table.configure(yscrollcommand=runscroll.set)
        self.run_table.pack(side='left',fill='both',expand=True);runscroll.pack(side='right',fill='y')
        self.run_table.bind('<Double-1>',self.show_run_detail)
        self.daily_note=label(daily,'尚无今日实盘记录。未启用不代表今天没有申购项目。',9,fg=MUTED,wraplength=780,justify='left');self.daily_note.pack(fill='x',pady=(0,10))
        tablebox=tk.Frame(daily,bg=WHITE);tablebox.pack(fill='both',expand=True)
        columns=('code','name','kind','quantity','status')
        self.table=ttk.Treeview(tablebox,columns=columns,show='headings',height=7,selectmode='browse')
        for col,title,width in zip(columns,('代码','证券名称','类型','数量','处理状态'),(112,170,70,85,215)):
            self.table.heading(col,text=title);self.table.column(col,width=width,minwidth=55,stretch=col in ('name','status'))
        scroll=ttk.Scrollbar(tablebox,orient='vertical',command=self.table.yview);self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        settings_scroll=ttk.Scrollbar(settings,orient='vertical');settings_scroll.pack(side='right',fill='y')
        self.environment=tk.Text(settings,wrap='word',font=FONT,bg=WHITE,fg=INK,relief='flat',height=8,state='disabled',yscrollcommand=settings_scroll.set)
        self.environment.pack(side='left',fill='both',expand=True);settings_scroll.configure(command=self.environment.yview)
        self.history=tk.Text(history,wrap='word',font=SMALL,bg=WHITE,fg=INK,relief='flat',height=9,padx=3,pady=3,state='disabled')
        self.history.pack(fill='both',expand=True)
        bottom=tk.Frame(content,bg=BG);bottom.pack(side='bottom',fill='x',pady=(14,0),before=notebook)
        bottom.columnconfigure(0,weight=1)
        label(bottom,'关闭面板不停止每日计划；暂停请使用上方按钮。',9,fg=MUTED,bg=BG,wraplength=460,justify='left').grid(row=0,column=0,sticky='w',padx=(0,12))
        self.buttons['logs']=button(bottom,'打开日志目录',self.open_logs);self.buttons['logs'].grid(row=0,column=1,sticky='e')
        for name in ('enable','pause','check'):self.buttons[name].configure(state='disabled')
        self.later(100,self.poll)
        if autorefresh:self.later(100,self.refresh);self.later(10000,self.timer)

    def later(self,milliseconds,callback):
        box=[None]
        def wrapped():
            self.after_ids.discard(box[0])
            if not self.closed:callback()
        box[0]=self.root.after(milliseconds,wrapped);self.after_ids.add(box[0])

    def stop(self):
        self.closed=True
        for key in self.after_ids:
            try:self.root.after_cancel(key)
            except tk.TclError:pass
        self.after_ids.clear()

    def ask(self,title,text,confirm_text):
        dialog=ConsentDialog(self.root,title,text,confirm_text)
        self.root.wait_window(dialog)
        return dialog.result

    def async_job(self,kind,work):
        def run():
            try:self.events.put((kind,work(),None))
            except Exception as exc:self.events.put((kind,None,exc))
        threading.Thread(target=run,daemon=True).start()

    def refresh(self):
        if self.closed or self.refreshing or self.busy:return
        self.refreshing=True;self.async_job('snapshot',self.backend.snapshot)

    def timer(self):
        if self.closed:return
        self.refresh();self.later(10000,self.timer)

    def poll(self):
        if self.closed:return
        try:
            while True:
                kind,data,error=self.events.get_nowait()
                if kind=='snapshot':
                    self.refreshing=False
                    if error or not data or not data.get('ok'):
                        self.snapshot=None;self.status.configure(text='运行状态暂不可用',fg=RED)
                        self.feedback.configure(text='未把读取失败当成“未启用”。请稍候自动重试，或查看日志。',fg=RED)
                        self.update_buttons()
                    else:self.render(data)
                else:
                    self.busy=False
                    self.feedback_latched=True
                    if error:
                        self.feedback.configure(text='操作未完成：'+type(error).__name__+'。未确认的状态请以刷新结果为准。',fg=RED)
                    elif kind=='check':
                        ok=data.get('account_ready') is True
                        self.feedback.configure(text=('QMT连接与账户登录正常；本次只读检查，未提交申购。' if ok else 'QMT只读检查未通过，请核对客户端登录。'),fg=GREEN if ok else RED)
                    else:
                        ok=data.get('ok') is True
                        text=('已保存，正在核对实际任务状态。' if ok else data.get('error','操作未完成'))
                        self.feedback.configure(text=text,fg=GREEN if ok else RED)
                        if ok and data.get('snapshot'):self.render(data['snapshot'],preserve_feedback=True)
                    self.update_buttons();self.refresh()
        except queue.Empty:pass
        self.clock.configure(text=datetime.now().strftime('%Y-%m-%d  %H:%M:%S'))
        self.later(100,self.poll)

    def update_buttons(self):
        state,_=plan_state(self.snapshot)
        self.buttons['enable'].configure(state='normal' if not self.busy and state in ('disabled','inconsistent') else 'disabled')
        self.buttons['pause'].configure(state='normal' if not self.busy and state in ('enabled','inconsistent') else 'disabled')
        self.buttons['check'].configure(state='normal' if not self.busy and self.snapshot else 'disabled')
        self.buttons['enable'].configure(text='每日计划已启用' if state=='enabled' else '授权并启用每日打新')

    def render(self,data,preserve_feedback=False):
        self.snapshot=data;state,title=plan_state(data)
        self.status.configure(text=title,fg=GREEN if state=='enabled' else AMBER if state=='disabled' else RED)
        descriptions={
            'enabled':'Windows 已接管每日运行。无需每天打开面板或再次确认。',
            'disabled':'点击授权后，Windows 将按每日计划运行；现在不会自动提交申购。',
            'inconsistent':'实际开关与任务状态不一致。请用控制按钮处理，不要重复手动申购。',
            'unknown':'无法确认实际状态，请先恢复状态读取。'}
        self.subtitle.configure(text=descriptions[state])
        if not preserve_feedback and not self.busy and not self.feedback_latched:
            self.feedback.configure(text=('首次控制需要Windows系统授权，程序会自动提示，不用手动以管理员运行。' if data.get('needs_admin') else '任务管理权限已就绪。所有控制操作都有结果回读和记录。'),fg=MUTED)
        doc=data.get('daily')
        self.cards['today'].configure(text=PHASES.get(doc.get('phase'),doc.get('phase')) if doc else '尚未开始')
        cycle=next((t for t in data.get('tasks',[]) if t['name']=='QmtIPO3-Cycle'),{})
        upcoming='待启用'
        if cycle.get('enabled') and cycle.get('next_run'):
            try:
                stamp=datetime.fromisoformat(cycle['next_run'])
                if stamp.year>=2020:upcoming=stamp.strftime('%m-%d %H:%M')
            except ValueError:upcoming='待刷新'
        self.cards['next'].configure(text=upcoming)
        pending=data.get('pending_notifications')
        self.cards['notify'].configure(text='待读取' if pending is None else '全部已送达' if pending==0 else f'{pending} 条待发送')
        self.run_rows={row['id']:row for row in data.get('runs',[])}
        self.run_table.delete(*self.run_table.get_children())
        for row in data.get('runs',[]):
            self.run_table.insert('','end',iid=row['id'],values=tuple(row[k] for k in ('time','action','result','counts','duration')))
        latest=next((row for row in data.get('runs',[]) if row.get('is_cycle')),None)
        self.last_run.configure(text=('最近自动触发：'+latest['time']+'  '+latest['result']+'  |  '+latest['counts'] if latest else '最近自动触发：今天尚无运行回执'))
        checked=data.get('checked_at','')
        try:checked=datetime.fromisoformat(checked).strftime('%H:%M:%S')
        except (TypeError,ValueError):checked='未知'
        self.run_note.configure(text=f"今日 {len(data.get('runs',[]))} 次任务记录 · 数据更新 {checked} · 每10秒刷新 · 双击看详情")
        self.table.delete(*self.table.get_children())
        for code,item in sorted((doc or {}).get('items',{}).items()):
            self.table.insert('','end',values=(code,item.get('name',''),{'STOCK':'新股','BOND':'新债'}.get(item.get('kind'),'—'),
                str(item.get('quantity','—'))+(' 张' if item.get('kind')=='BOND' else ' 股'),item_reason(code,item) if item.get('status') in ('SKIPPED_SCOPE','WAITING_WINDOW') and item_reason(code,item) else ITEM_STATES.get(item.get('status'),item.get('status','待处理'))))
        self.daily_note.configure(text=('记录日期：'+doc['day']+'。券商已报/已成不代表中签。' if doc else '尚无今日实盘记录。未启用不代表今天没有申购项目。'))
        if 'daily_unavailable' in data.get('data_errors',[]):self.daily_note.configure(text='今日记录暂时无法读取，不能据此判断没有申购。请查看日志。')
        qmt='进程可见（不等于账户已登录）' if data.get('qmt_process_present') and data.get('quote_process_present') else '未检测到完整客户端进程'
        environment=f"账户尾号：{data.get('account_tail','—')}\n启用市场：{' / '.join(data.get('markets',[]))}\nQMT：{qmt}\n客户端目录：{data.get('qmt_path','—')}\n企业微信：{'已配置' if data.get('webhook_configured') else '未配置'}\n\n每日流程\n09:35 自动开始；未完成项目每5分钟继续处理\n15:05 最终核对券商回报\n16:20 一致性备份；通知失败留队补发\n\n只做申购，不自动卖出或缴款。运行中不重启QMT。"
        self.environment.configure(state='normal');self.environment.delete('1.0','end');self.environment.insert('1.0',environment);self.environment.configure(state='disabled')
        rows=[]
        operation_names={'Enable':'授权并启用','Pause':'暂停每日计划','Prepare':'准备任务管理权限'}
        for item in data.get('history',[]):
            rows.append(f"{item.get('time','')}  {operation_names.get(item.get('operation'),item.get('operation','操作'))}  {'成功' if item.get('ok') else '未完成'}\n{item.get('error','')}")
        self.history.configure(state='normal');self.history.delete('1.0','end')
        self.history.insert('1.0','\n\n'.join(rows) or '暂无控制操作记录。\n授权、暂停和权限准备的结果会显示在这里。')
        self.history.configure(state='disabled');self.update_buttons()

    def show_run_detail(self,event=None):
        selection=self.run_table.selection()
        if not selection or selection[0] not in self.run_rows:return
        row=self.run_rows[selection[0]]
        dialog=tk.Toplevel(self.root);dialog.title('运行详情 · '+row['time']);dialog.geometry('820x480');dialog.configure(bg=WHITE)
        dialog.transient(self.root)
        content=tk.Frame(dialog,bg=WHITE,padx=18,pady=18);content.pack(fill='both',expand=True)
        scrollbar=ttk.Scrollbar(content,orient='vertical');scrollbar.pack(side='right',fill='y')
        text=tk.Text(content,wrap='word',font=FONT,relief='flat',bg=WHITE,fg=INK,yscrollcommand=scrollbar.set)
        text.pack(side='left',fill='both',expand=True);scrollbar.configure(command=text.yview)
        text.insert('1.0',row['detail']);text.configure(state='disabled')

    def enable(self):
        if self.busy or not self.snapshot:return
        text=(f"你将授权账户尾号 {self.snapshot.get('account_tail','—')} 持续每日自动申购新股、新债。\n\n"
              '09:35开始，未完成项目按计划继续处理；新股使用账户额度，新债按发行上限且最多10,000张。\n\n'
              '只做申购，不自动卖出或缴款。关闭控制台不会停止计划；需要停止时请点击“暂停每日打新”。')
        if not self.confirm('授权每日自动打新',text,'同意授权并启用'):return
        self.action_count+=1
        self.busy=True;self.update_buttons()
        self.feedback.configure(text='正在保存授权并配置计划；如Windows提示系统授权，请在系统窗口确认。',fg=BLUE)
        self.async_job('change',lambda:self.backend.change('Enable',confirmed=True))

    def pause(self):
        if self.busy:return
        text='暂停后不再启动新的申购轮次。已经开始的一轮可能完成，已提交委托不会被撤销；通知补发与备份可继续运行。'
        if not self.confirm('暂停每日自动打新',text,'确认暂停'):return
        self.action_count+=1
        self.busy=True;self.update_buttons();self.feedback.configure(text='正在暂停后续申购计划…',fg=BLUE)
        self.async_job('change',lambda:self.backend.change('Pause',confirmed=True))

    def check(self):
        if self.busy:return
        self.busy=True;self.update_buttons();self.feedback.configure(text='正在只读检查QMT连接，不会提交申购…',fg=BLUE)
        self.async_job('check',self.backend.check_connection)

    def open_logs(self):
        try:self.backend.open_logs()
        except Exception as exc:messagebox.showerror('日志目录',str(exc),parent=self.root)

    def close(self):
        if self.busy and not messagebox.askyesno('关闭控制台','控制操作尚在进行，关闭窗口不会取消已开始的操作。仍要关闭吗？',parent=self.root):return
        self.stop();self.root.destroy()


def single_window():
    import sys
    if sys.platform!='win32':return None,False
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,w.BOOL,w.LPCWSTR];kernel.CreateMutexW.restype=w.HANDLE
    handle=kernel.CreateMutexW(None,False,'Local\\QmtIpoDesktopPanel')
    existing=ctypes.get_last_error()==183
    if existing:
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.FindWindowW.argtypes=[w.LPCWSTR,w.LPCWSTR];user.FindWindowW.restype=w.HWND
        user.ShowWindow.argtypes=[w.HWND,ctypes.c_int];user.SetForegroundWindow.argtypes=[w.HWND]
        window=user.FindWindowW(None,'自动打新控制台')
        if window:user.ShowWindow(window,9);user.SetForegroundWindow(window)
    return handle,existing


def main():
    parser=argparse.ArgumentParser(description='自动打新桌面控制台')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--smoke-report',type=Path,help='只记录界面加载结果，不点击控制按钮')
    args=parser.parse_args()
    handle,existing=single_window()
    if existing:return
    root=tk.Tk();panel=Panel(root,Backend(args.root))
    if args.smoke_report:
        def report():
            if panel.snapshot is None:
                root.after(500,report);return
            from runtime import atomic_json
            root.update_idletasks()
            atomic_json(args.smoke_report,{'loaded':True,'state':plan_state(panel.snapshot)[0],
                'needs_admin':panel.snapshot.get('needs_admin'),
                'buttons_mapped':{k:bool(v.winfo_ismapped()) for k,v in panel.buttons.items()},
                'window_size':[root.winfo_width(),root.winfo_height()],'run_table_height':panel.run_table.winfo_height(),
                'run_rows_visible':len(panel.run_rows),'latest_trigger_text':panel.last_run.cget('text'),
                'financial_actions_invoked':bool(panel.action_count)})
        root.after(1000,report)
    root.mainloop()


if __name__=='__main__':main()
