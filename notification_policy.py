"""Conservative outbox suppression, NEVER deletion or fake delivery.

Only pure availability alerts for a proven closed event-date are suppressible.
Any order intent on that day, ambiguity or unavailable audit ledger keeps them.
"""
from datetime import date
import re
import sqlite3
from pathlib import Path
import time

from market_calendar import CLOSED, CalendarService
from support import Store

_HEADER = r'【QMT打新】事件 [a-f0-9]{16} · 1/1\n'
_CONNECTION = re.compile(_HEADER + r'(\d{4}-\d{2}-\d{2}) 自动打新V3【(?:实盘|预览、不下单)】\n'
                         r'连接/查询未完成（[A-Za-z_][A-Za-z0-9_]*）；仅未发起过的项目允许后续尝试。\Z')
_MONITOR = re.compile(_HEADER + r'(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\n自动打新异常：(.+)。请查看每日状态；监控未重启、未补单。\Z')
_PURE_AVAILABILITY = {'今日定时任务漏跑', '今日仍有未完成项目', '收盘核对未完成', '今日运行记录缺失'}


def suppress_closed_alerts(store, config):
    calendar = CalendarService(config)
    audit_path = Path(config['state_dir'])/'state.sqlite3'
    if not audit_path.is_file():
        return 0
    try:
        audit = sqlite3.connect(audit_path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    except sqlite3.Error:
        return 0  # No audit proof: retain every original message.
    suppressed = 0
    try:
        rows = store.db.execute('''SELECT o.* FROM outbox o
            LEFT JOIN notification_suppressions s ON o.id=s.id
            WHERE o.delivered=0 AND s.id IS NULL
            AND (o.content LIKE '%连接/查询未完成%' OR o.content LIKE '%自动打新异常：%')''').fetchall()
        for row in rows:
            match = _CONNECTION.fullmatch(row['content'])
            if not match:
                match = _MONITOR.fullmatch(row['content'])
                if not match or not set(match[2].split('；')) <= _PURE_AVAILABILITY:
                    continue
            day = match[1]
            try:
                date.fromisoformat(day)
                if audit.execute('SELECT 1 FROM intents WHERE day=? LIMIT 1', (day,)).fetchone():
                    continue
                decision = calendar.decide(day)
                if decision.status != CLOSED:
                    continue
            except (ValueError, sqlite3.Error):
                continue
            with store.transaction():
                count = store.db.execute('''INSERT OR IGNORE INTO notification_suppressions
                    (id,reason,suppressed_at,calendar_source) VALUES(?,?,?,?)''',
                    (row['id'], 'closed_day_availability_only; original retained; not delivered',
                     time.time(), decision.source)).rowcount
            suppressed += count
    finally:
        audit.close()
    return suppressed


def enqueue_calendar_issue(config, decision):
    """Cycle and monitor share this single outbox and event identity."""
    import hashlib
    account = hashlib.sha256(config.get('account_id', '').encode()).hexdigest()[:24]
    store = Store(Path(config['control_dir'])/'monitor.sqlite3')
    try:
        store.event(f'calendar:{account}:{decision.issue_key}',
                    f'QMT打新：交易日历异常（{decision.code}）\n首次观察日期：{decision.day}\n'
                    f'{decision.reason}\n日历未确认前不连接交易账户、不查询申购、不提交。')
    finally:
        store.close()


def notification_stats(path):
    """Read old/new ledgers without creating or migrating them."""
    path = Path(path)
    if not path.is_file():
        return {'pending': None, 'failed': None, 'suppressed': None}
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        excluded = (' AND id NOT IN (SELECT id FROM notification_suppressions)'
                    if 'notification_suppressions' in tables else '')
        pending = conn.execute('SELECT COUNT(*) FROM outbox WHERE delivered=0'+excluded).fetchone()[0]
        failed = conn.execute('SELECT COUNT(*) FROM outbox WHERE delivered=0 AND attempts>0'+excluded).fetchone()[0]
        suppressed = conn.execute('SELECT COUNT(*) FROM notification_suppressions').fetchone()[0] if excluded else 0
        return {'pending': pending, 'failed': failed, 'suppressed': suppressed}
    finally:
        conn.close()
