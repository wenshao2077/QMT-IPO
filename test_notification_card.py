"""Notification contract tests; no real sender, account or task execution."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from notification_card import PREFIX, encode_card, image_payload, render_png, result_cards
from support import Store, wecom_sender
import test_system as fixtures
from test_system import OPEN, info


def document():
    return {'day': '2026-10-20', 'completed': True, 'errors': [], 'items': {
        '371018.SZ': {'code': '371018.SZ', 'name': '申菱发债', 'kind': 'BOND', 'status': 'CONFIRMED'},
        '920306.BJ': {'code': '920306.BJ', 'name': '天元重工', 'kind': 'STOCK', 'status': 'SKIPPED_SCOPE'}}}


class CardTests(unittest.TestCase):
    def test_success_only_shows_accepted_items(self):
        card = result_cards(document())[0]
        self.assertEqual(len(card['items']), 1)
        self.assertEqual(card['items'][0]['kind'], 'BOND')
        content = encode_card(card)
        for forbidden in ('quantity', 'order_id', '天元重工', 'SKIPPED_SCOPE', 'account'):
            self.assertNotIn(forbidden, content)

    def test_pending_rejected_quota_and_errors_never_become_success(self):
        for status in ('PENDING', 'SUBMITTED', 'UNCERTAIN', 'PARTIAL', 'REJECTED', 'CANCELED', 'SKIPPED_QUOTA'):
            doc = document()
            doc['items']['371018.SZ']['status'] = status
            with self.subTest(status=status):
                self.assertIsNone(result_cards(doc))
        doc = document()
        doc['errors'] = ['query failed']
        self.assertIsNone(result_cards(doc))
        doc = document()
        doc['items']['371018.SZ']['broker_error'] = 'broker rejected request'
        self.assertIsNone(result_cards(doc))

    def test_empty_and_multiple_pages_keep_all_names(self):
        doc = document()
        doc['items'] = {}
        self.assertEqual(result_cards(doc)[0]['items'], [])
        doc['items'] = {str(n): {'code': f'{n:06d}.SZ', 'name': f'新股{n}', 'kind': 'STOCK',
                                 'status': 'REPORTED'} for n in range(13)}
        cards = result_cards(doc)
        self.assertEqual([len(c['items']) for c in cards], [6,6,1])
        self.assertEqual([c['page'] for c in cards], [1,2,3])
        self.assertEqual(sum(len(c['items']) for c in cards), 13)

    def test_http_image_payload_hash_and_acknowledgment(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"errcode":0}'
        png = b'\x89PNG\r\n\x1a\nfake-test-bytes'
        content = encode_card(result_cards(document())[0])
        with patch('notification_card.render_png', return_value=png):
            wecom_sender('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake', opener)(content)
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['msgtype'], 'image')
        self.assertEqual(base64.b64decode(payload['image']['base64']), png)
        self.assertEqual(payload['image']['md5'], hashlib.md5(png).hexdigest())
        self.assertNotIn('text', payload)

    def test_image_failure_remains_pending_and_retry_marks_real_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'state.sqlite3')
            try:
                store.image_event('test', result_cards(document())[0])
                fail = MagicMock(side_effect=RuntimeError('failed'))
                self.assertEqual(store.drain(fail, now=lambda:100), 1)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notification_delivery_receipts').fetchone()[0], 0)
                ok = MagicMock()
                self.assertEqual(store.drain(ok, now=lambda:131), 0)
                self.assertEqual(ok.call_count, 1)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notification_delivery_receipts').fetchone()[0], 1)
            finally:
                store.close()

    def test_bad_or_oversized_image_does_not_fall_back_to_text(self):
        content = encode_card(result_cards(document())[0])
        for raw in (b'bad', b'\x89PNG\r\n\x1a\n'+b'a'*(2*1024*1024)):
            with self.assertRaises(ValueError):
                image_payload(content, lambda _: raw)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows GDI renderer')
    def test_actual_chinese_png_renderer(self):
        png = render_png(result_cards(document())[0])
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertLess(len(png), 2*1024*1024)


class CardBusinessTests(unittest.TestCase):
    # Reuse the synthetic fixture, not its entire test suite.
    setUp = fixtures.DailySystemTests.setUp
    tearDown = fixtures.DailySystemTests.tearDown
    run_cycle = fixtures.DailySystemTests.run_cycle

    def test_reported_to_confirmed_same_visible_result_sends_once(self):
        self.broker.items = {'371018.SZ': info('BOND'), '920306.BJ': info()}
        self.run_cycle()
        self.broker.order_rows[0].update(status='confirmed', raw_status=86)
        self.clock = OPEN.replace(hour=15, minute=5)
        self.run_cycle()
        contents = [r[0] for r in self.ledger.db.execute('SELECT content FROM outbox')]
        self.assertEqual(len(contents), 1)
        self.assertTrue(contents[0].startswith(PREFIX))
        self.assertNotIn('天元重工', contents[0])
        self.assertEqual(len(self.broker.calls), 1)

    def test_pending_uses_error_text_and_excludes_disabled_market(self):
        self.broker.items = {'371018.SZ': info('BOND'), '920306.BJ': info()}
        self.broker.publish = False
        self.run_cycle()
        contents = [r[0] for r in self.ledger.db.execute('SELECT content FROM outbox')]
        self.assertTrue(contents)
        self.assertTrue(all(not c.startswith(PREFIX) for c in contents))
        self.assertNotIn('天元重工', '\n'.join(contents))
