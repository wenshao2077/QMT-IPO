"""Local first-use GUI. Never enables tasks or runs account/notification tests."""
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from configuration import ConfigurationError, save_first_use
from runtime import read_json

MESSAGES = {
    'account_required': '请在本机填写资金账号。',
    'account_invalid': '账号包含不支持的控制字符。',
    'mini_qmt_userdata_missing': '请选择现有 miniQMT 的 userdata_mini 文件夹。',
    'webhook_missing_or_invalid': '请在本机填写有效的企业微信机器人 Webhook；不会发送测试消息。',
    'configuration_invalid': '配置无效；至少选择一个市场，运行目录不能放在 QMT 数据目录内。',
    'used_installation_requires_reviewed_account_change': '此安装已有授权或运行历史。首次配置向导不会更换生产账户，请按维护流程处理。',
    'finish_installation_first': '安装尚未完成，请先使用 ResumeNew 接续安装。',
    'configuration_recovery_required': '上次配置写入中断。程序保持禁用，请人工核验，不要删除恢复标记后直接启用。',
    'execution_must_be_disabled': '必须保持执行关闭；此向导不会自动暂停或启用交易。',
    'all_tasks_must_be_disabled': '首次配置要求四个任务全部禁用。请检查安装状态。',
    'another_control_operation_running': '控制台正在进行其他操作，请结束该操作后重试。',
}


class ConfigurationWizard:
    def __init__(self, window, root, saver=save_first_use):
        self.window, self.root, self.saver = window, Path(root), saver
        config = read_json(self.root/'config.json')
        window.title('QMT 打新 · 首次配置（不启用交易）')
        window.minsize(640, 420)
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text='只保存本机配置；不连接账户、不发送通知、不启用申购。', wraplength=620).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 18))
        self.account = tk.StringVar(value='' if config['account_id']=='YOUR_ACCOUNT' else config['account_id'])
        self.qmt = tk.StringVar(value=config['qmt_userdata'])
        self.hook = tk.StringVar(value='')  # Never prefill or reveal the existing secret.
        ttk.Label(frame, text='资金账号').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Entry(frame, textvariable=self.account, show='*').grid(row=1, column=1, columnspan=2, sticky='ew')
        ttk.Label(frame, text='miniQMT 数据目录').grid(row=2, column=0, sticky='w', pady=8)
        ttk.Entry(frame, textvariable=self.qmt).grid(row=2, column=1, sticky='ew')
        ttk.Button(frame, text='选择目录', command=self.choose_directory).grid(row=2, column=2, padx=(8, 0))
        ttk.Label(frame, text='申购范围').grid(row=3, column=0, sticky='w', pady=8)
        markets = ttk.Frame(frame)
        markets.grid(row=3, column=1, columnspan=2, sticky='w')
        self.markets = {}
        for market, title in [('SH','沪市（不含科创板新股）'),('SZ','深市'),('KCB','科创板新股')]:
            var = tk.BooleanVar(value=market in config['allowed_markets'])
            self.markets[market] = var
            ttk.Checkbutton(markets, text=title, variable=var).pack(anchor='w')
        ttk.Label(frame, text='Webhook').grid(row=4, column=0, sticky='w', pady=8)
        ttk.Entry(frame, textvariable=self.hook, show='*').grid(row=4, column=1, columnspan=2, sticky='ew')
        ttk.Label(frame, text='留空保留已有密钥。首次配置必须填写；不要粘贴到 AI 对话。\n'
                  '选择范围不证明券商已授予权限；本工具不覆盖北交所、不卖出、不缴款。',
                  wraplength=620).grid(row=5, column=0, columnspan=3, sticky='w', pady=12)
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky='e', pady=12)
        ttk.Button(buttons, text='取消', command=window.destroy).pack(side='left', padx=8)
        self.save_button = ttk.Button(buttons, text='保存配置，保持禁用', command=self.save)
        self.save_button.pack(side='left')

    def choose_directory(self):
        value = filedialog.askdirectory(parent=self.window, title='选择 userdata_mini 文件夹')
        if value:
            self.qmt.set(value)

    def save(self):
        self.save_button.configure(state='disabled')
        try:
            self.saver(self.root, account_id=self.account.get(), qmt_userdata=self.qmt.get(),
                       allowed_markets=[m for m, v in self.markets.items() if v.get()],
                       webhook=self.hook.get() or None)
        except ConfigurationError as exc:
            code = str(exc)
            messagebox.showerror('配置未完成', MESSAGES.get(code, '配置未保存。错误码：'+code), parent=self.window)
        except Exception:
            messagebox.showerror('配置未完成', '本机状态检查或保存失败。未启用交易，请检查安装及恢复标记。', parent=self.window)
        else:
            self.hook.set('')
            messagebox.showinfo('已保存，尚未启用', '请先运行本地检查。账户只读检查、测试通知和交易启用需要分别确认。', parent=self.window)
            self.window.destroy()
        finally:
            try:
                self.save_button.configure(state='normal')
            except tk.TclError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地首次配置向导，不启用交易')
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    window = tk.Tk()
    try:
        ConfigurationWizard(window, args.root)
        window.mainloop()
    except Exception:
        messagebox.showerror('无法打开配置向导', '请确认安装已完成，并使用该安装的 Python 环境。', parent=window)
        window.destroy()
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
