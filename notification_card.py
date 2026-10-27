"""Small, deterministic result cards. No SDK, network, account or Webhook access."""
import base64
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

PREFIX = 'QMT_IPO_IMAGE_V1\n'
ACCEPTED = {'REPORTED', 'SUCCEEDED', 'CONFIRMED', 'EXTERNAL_ACCEPTED'}
STATUS_LABELS = {
    'REJECTED': '废单待核对', 'CANCELED': '已撤待核对',
    'PENDING': '等待回报', 'SUBMITTED': '已提交待确认',
    'INTENT': '意图待核对', 'UNCERTAIN': '不确定，不重发',
    'PARTIAL': '部分成交待核对', 'SKIPPED_QUOTA': '额度不足，跳过',
    'RETRYABLE': '数据待重试', 'MISSED': '窗口结束，未提交',
    'EXTERNAL_BLOCKED': '异常委托，不重报', 'WAITING_WINDOW': '等待申报时段',
    'BROKER_ERROR': '券商异常待核对',
}
ERROR_STATES = {'REJECTED', 'CANCELED', 'EXTERNAL_BLOCKED', 'BROKER_ERROR'}


def result_cards(doc):
    """Render known business outcomes honestly; system/query errors stay text."""
    items = list(doc.get('items', {}).values())
    if doc.get('errors') or (not items and not doc.get('completed')):
        return None
    if any(i.get('status') not in ACCEPTED | set(STATUS_LABELS) | {'SKIPPED_SCOPE'} for i in items):
        return None
    # The broker adapter maps unrecognized raw status codes to PENDING. Keep
    # the existing alert with its raw code instead of losing that evidence.
    if any(i.get('status') == 'PENDING' and i.get('broker_status_code') is not None for i in items):
        return None
    rows = []
    for item in sorted(items, key=lambda i: i['code']):
        if item['status'] == 'SKIPPED_SCOPE':
            continue
        if item.get('kind') not in ('BOND', 'STOCK'):
            return None
        row = {'code': item['code'], 'name': item.get('name') or item['code'], 'kind': item['kind']}
        if item.get('status') not in ACCEPTED:
            row['status'] = item['status']
        if item.get('broker_error'):
            row['status'] = 'BROKER_ERROR' if item['status'] in ACCEPTED else item['status']
            row['detail'] = str(item['broker_error']).replace('\r', ' ').replace('\n', ' ')[:180]
        rows.append(row)
    if not rows and not doc.get('completed'):
        return None
    count = max(1, (len(rows) + 5) // 6)
    return [{'day': doc['day'], 'items': rows[n*6:(n+1)*6], 'page': n+1, 'pages': count}
            for n in range(count)]


def encode_card(card):
    validate_card(card)
    return PREFIX + json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def validate_card(card):
    if not isinstance(card, dict) or set(card)-{'broker_label'} != {'day', 'items', 'page', 'pages'}:
        raise ValueError('Invalid card')
    if 'broker_label' in card and (not isinstance(card['broker_label'],str) or not 1 <= len(card['broker_label']) <= 16 or any(c in card['broker_label'] for c in ('\r','\n','\x00'))):
        raise ValueError('Invalid broker label')
    date.fromisoformat(card['day'])
    if (type(card['page']) is not int or type(card['pages']) is not int
            or not 1 <= card['page'] <= card['pages'] <= 100):
        raise ValueError('Invalid card page')
    if not isinstance(card['items'], list) or len(card['items']) > 6:
        raise ValueError('Invalid card rows')
    for row in card['items']:
        if (not isinstance(row, dict) or set(row)-{'status', 'detail'} != {'code', 'name', 'kind'}
                or row['kind'] not in ('BOND', 'STOCK')
                or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 80
                or not isinstance(row['code'], str) or len(row['code']) > 12):
            raise ValueError('Invalid card item')
        if 'status' in row and row['status'] not in STATUS_LABELS:
            raise ValueError('Invalid card status')
        if 'detail' in row and (not isinstance(row['detail'], str) or not 1 <= len(row['detail']) <= 180
                                or 'status' not in row or any(c in row['detail'] for c in ('\r', '\n', '\x00'))):
            raise ValueError('Invalid card detail')


def render_png(card):
    validate_card(card)
    # Renderer is deployed at the installation root; development keeps it adjacent.
    here = Path(__file__).resolve().parent
    script = here/'render_notification_card.ps1'
    if not script.is_file():
        script = here.parent/'render_notification_card.ps1'
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    with tempfile.TemporaryDirectory(prefix='qmt-ipo-card-') as folder:
        spec, output = Path(folder)/'card.json', Path(folder)/'card.png'
        labels = {'title': '今日打新', 'bond': '可转债', 'stock': '新股',
                  'success': '申购成功', 'empty': '今日未申购',
                  'empty_detail': '没有已受理的新股或可转债申购',
                  'footer': '申购结果 · 非中签结果'}
        if card.get('broker_label'):labels['title']=card['broker_label']+' · 今日打新'
        rows = []
        for row in card['items']:
            status = row.get('status')
            rows.append(dict(row, status_label=STATUS_LABELS.get(status, labels['success']),
                             status_color=('#C44536' if status in ERROR_STATES else '#A56816')
                             if status else '#16836F'))
        spec.write_text(json.dumps(dict(card, items=rows, labels=labels), ensure_ascii=False), encoding='utf-8')
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-File', str(script),
                        '-InputJson', str(spec), '-OutputPng', str(output)],
                       check=True, capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return output.read_bytes()


def image_payload(content, renderer=None):
    card = json.loads(content[len(PREFIX):])
    validate_card(card)
    png = (renderer or render_png)(card)
    if not isinstance(png, bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > 2*1024*1024:
        raise ValueError('Invalid or oversized card PNG')
    return {'msgtype': 'image', 'image': {
        'base64': base64.b64encode(png).decode('ascii'),
        'md5': hashlib.md5(png).hexdigest()}}
