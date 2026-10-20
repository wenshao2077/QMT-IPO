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


def result_cards(doc):
    """None means keep the error path; [] is not used to hide failures."""
    items = list(doc.get('items', {}).values())
    if not doc.get('completed') or doc.get('errors') or any(i.get('broker_error') for i in items):
        return None
    if any(i.get('status') not in ACCEPTED | {'SKIPPED_SCOPE'} for i in items):
        return None
    rows = []
    for item in sorted(items, key=lambda i: i['code']):
        if item['status'] == 'SKIPPED_SCOPE':
            continue
        if item.get('kind') not in ('BOND', 'STOCK'):
            return None
        rows.append({'code': item['code'], 'name': item.get('name') or item['code'],
                     'kind': item['kind']})
    count = max(1, (len(rows) + 5) // 6)
    return [{'day': doc['day'], 'items': rows[n*6:(n+1)*6], 'page': n+1, 'pages': count}
            for n in range(count)]


def encode_card(card):
    validate_card(card)
    return PREFIX + json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def validate_card(card):
    if not isinstance(card, dict) or set(card) != {'day', 'items', 'page', 'pages'}:
        raise ValueError('Invalid card')
    date.fromisoformat(card['day'])
    if (type(card['page']) is not int or type(card['pages']) is not int
            or not 1 <= card['page'] <= card['pages'] <= 100):
        raise ValueError('Invalid card page')
    if not isinstance(card['items'], list) or len(card['items']) > 6:
        raise ValueError('Invalid card rows')
    for row in card['items']:
        if (not isinstance(row, dict) or set(row) != {'code', 'name', 'kind'}
                or row['kind'] not in ('BOND', 'STOCK')
                or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 80
                or not isinstance(row['code'], str) or len(row['code']) > 12):
            raise ValueError('Invalid card item')


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
        spec.write_text(json.dumps(dict(card, labels=labels), ensure_ascii=False), encoding='utf-8')
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
