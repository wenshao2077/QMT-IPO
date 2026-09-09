"""Shared durable ledger/outbox; retains V2 on-disk compatibility for upgrades."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler

CHINA = timezone(timedelta(hours=8))


def china_now():
    return datetime.now(CHINA)


def positive_int(value, label, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError(f'{label}不能为布尔值')
    number = float(value)
    if not math.isfinite(number) or not number.is_integer() or number < (0 if allow_zero else 1):
        raise ValueError(f'{label}必须为有效整数')
    return int(number)


def purchase_day(value):
    text = str(value).strip()
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('申购日期缺失或格式未知')


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    kind: str
    quantity: int
    price: float
    day: str

    @property
    def remark(self):
        return f'IPOv2_{self.day.replace("-", "")}_{self.code}'


def build_plan(code, info, limits, day, allowed_markets=('SH', 'SZ')):
    if not re.fullmatch(r'\d{6}\.(SH|SZ)', code):
        raise ValueError('仅支持沪深申购代码')
    if purchase_day(info.get('purchaseDate')) != day:
        raise ValueError('不是当日申购项目')
    kind = info.get('type')
    if kind not in ('STOCK', 'BOND'):
        raise ValueError('不支持的申购类型')
    market = code[-2:]
    if kind == 'STOCK' and market == 'SH' and code.startswith(('688', '689', '787', '789')):
        market = 'KCB'
    if market not in allowed_markets:
        raise ValueError('该市场未在配置中启用')
    cap = positive_int(info.get('maxPurchaseNum'), '发行上限')
    # No guessed 500/1000-share or bond lot: use issuer metadata.
    lot = positive_int(info.get('minPurchaseNum'), '申购单位')
    price = float(info.get('issuePrice'))
    if not math.isfinite(price) or price <= 0:
        raise ValueError('发行价无效')
    if kind == 'STOCK':
        if not isinstance(limits, dict) or market not in limits:
            raise ValueError('缺少账户对应市场新股额度')
        cap = min(cap, positive_int(limits[market], '账户额度', allow_zero=True))
    if kind == 'BOND':
        cap = min(cap, 10000)  # Current XtQuant official contract: max 10,000 bonds.
    # BOND subscription uses issuance cap, never the stock market-value quota.
    qty = cap // lot * lot
    return Plan(code, str(info.get('name', code))[:80], kind, qty, price, day)


def split_message(text, budget=1700):
    """Keep complete UTF-8 characters; reserve room for title/page/event identity."""
    parts, chars, used = [], [], 0
    for char in text:
        size = len(char.encode('utf-8'))
        if used + size > budget:
            parts.append(''.join(chars))
            chars, used = [], 0
        chars.append(char)
        used += size
    if chars or not parts:
        parts.append(''.join(chars))
    return parts


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS intents (
                account TEXT NOT NULL, day TEXT NOT NULL, code TEXT NOT NULL,
                state TEXT NOT NULL, order_id INTEGER, quantity INTEGER NOT NULL,
                updated REAL NOT NULL, PRIMARY KEY(account, day, code));
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '');
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _event(self, key, content):
        identity = hashlib.sha256(key.encode()).hexdigest()[:16]
        pages = split_message(content)
        for i, page in enumerate(pages, 1):
            body = f'【QMT打新】事件 {identity} · {i}/{len(pages)}\n{page}'
            assert len(body.encode('utf-8')) <= 2000
            self.db.execute('INSERT OR IGNORE INTO outbox(id,content) VALUES(?,?)',
                            (f'{identity}:{i:06d}', body))

    def event(self, key, content):
        with self.transaction():
            self._event(key, content)

    def get(self, account, day, code):
        return self.db.execute('SELECT * FROM intents WHERE account=? AND day=? AND code=?',
                               (account, day, code)).fetchone()

    def reserve(self, account, plan):
        # Commit intent BEFORE crossing the broker boundary. A crash never releases it.
        with self.transaction():
            inserted = self.db.execute(
                'INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,?,?)',
                (account, plan.day, plan.code, 'INTENT', None, plan.quantity, time.time())).rowcount
            if inserted:
                self._event(f'{account}:{plan.day}:{plan.code}:INTENT',
                            f'{plan.day} {plan.code} {plan.name}\n申购请求已登记，尚不代表提交或受理。')
        return bool(inserted)

    def state(self, account, day, code, state, message, order_id=None):
        with self.transaction():
            self.db.execute('''UPDATE intents SET state=?,order_id=COALESCE(?,order_id),updated=?
                WHERE account=? AND day=? AND code=?''',
                (state, order_id, time.time(), account, day, code))
            self._event(f'{account}:{day}:{code}:{state}:{order_id}',
                        f'{day} {code}\n{message}')

    def records(self, account):
        return self.db.execute('SELECT * FROM intents WHERE account=? ORDER BY day,code',
                               (account,)).fetchall()

    def pending_notifications(self):
        return self.db.execute('SELECT COUNT(*) FROM outbox WHERE delivered=0').fetchone()[0]

    def drain(self, send, now=time.time, sleep=time.sleep, limit=15):
        """Durable at-least-once delivery. A lost HTTP response can repeat a message,
        but never an order. Stable event IDs allow identifying repeated notices.
        """
        rows = self.db.execute('SELECT * FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT ?',
                               (limit,)).fetchall()
        for index, row in enumerate(rows):
            if row['due'] > now():
                break
            if index:
                sleep(3.2)  # Stay below WeCom's 20 requests/minute per robot.
            try:
                send(row['content'])
            except Exception as exc:
                # Do not persist raw URLs, tokens, account IDs or exception messages.
                attempts = row['attempts'] + 1
                with self.transaction():
                    self.db.execute('UPDATE outbox SET attempts=?,due=?,error=? WHERE id=?',
                                    (attempts, now() + min(30 * 2 ** min(attempts-1, 7), 3600),
                                     type(exc).__name__, row['id']))
                break  # Keep page ordering and avoid hammering an unhealthy endpoint.
            else:
                with self.transaction():
                    self.db.execute('UPDATE outbox SET delivered=1,error=? WHERE id=?', ('', row['id']))
        return self.pending_notifications()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def wecom_sender(webhook, opener=None):
    parsed = urlparse(webhook)
    if (parsed.scheme != 'https' or parsed.netloc != 'qyapi.weixin.qq.com'
            or parsed.path != '/cgi-bin/webhook/send' or parsed.fragment
            or not parse_qs(parsed.query).get('key')):
        raise ValueError('Webhook 必须是企业微信 HTTPS 群机器人地址')
    opener = opener or build_opener(NoRedirect())

    def send(content):
        request = Request(webhook, data=json.dumps(
            {'msgtype': 'text', 'text': {'content': content}}, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
        try:
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError('企业微信 HTTP 状态异常')
                result = json.loads(response.read(65536))
            if not isinstance(result, dict) or type(result.get('errcode')) is not int or result['errcode'] != 0:
                raise RuntimeError('企业微信业务回执未确认成功')
        except Exception:
            raise RuntimeError('企业微信通知未确认送达，保留待发送记录') from None
    return send
