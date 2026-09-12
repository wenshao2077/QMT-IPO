"""Fail-closed SH/SZ calendar gate. Importing and decide() never import the SDK.

A list's requested end date is NOT evidence of returned coverage. Legacy caches
are usable only after whole-interval comparison with a reviewed annual pack.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

from runtime import atomic_json, read_json, single_instance
from support import CHINA, china_now

OPEN, CLOSED, UNKNOWN = 'trading_day', 'market_closed', 'unknown'
SDK_SOURCE = 'https://dict.thinktrader.net/nativeApi/xtdata.html'
HOLIDAYS = {'new_year', 'spring_festival', 'qingming', 'labour',
            'dragon_boat', 'mid_autumn', 'national'}
MAX_BYTES = 256 * 1024


def iso_day(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date_format')
    return date.fromisoformat(value)


def digest(doc: dict) -> str:
    payload = {k: v for k, v in doc.items() if k != 'sha256'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def sealed(doc: dict) -> dict:
    return dict(doc, sha256=digest(doc))


def load_document(path: Path) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('calendar_too_large')
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise ValueError('calendar_structure')
    return doc


def _days(values, start, end):
    if not isinstance(values, list) or not values or len(values) > 10000:
        raise ValueError('calendar_dates_empty_or_invalid')
    parsed = [iso_day(x) for x in values]
    if values != sorted(set(values)):
        raise ValueError('calendar_dates_not_unique_sorted')
    if any(not start <= x <= end or x.weekday() >= 5 for x in parsed):
        raise ValueError('calendar_dates_out_of_range_or_weekend')
    return set(values)


def validate_document(doc: dict) -> dict:
    """Validate declared coverage + provenance + integrity, not just a year hit.

    The checksum detects damaged/truncated files; it is NOT a digital signature.
    Annual announcement transcription must still receive human review.
    """
    if doc.get('schema_version') != 2 or doc.get('sha256') != digest(doc):
        raise ValueError('calendar_schema_or_checksum')
    start, end = iso_day(doc.get('covered_from')), iso_day(doc.get('covered_through'))
    if start > end or (end - start).days > 366:
        raise ValueError('calendar_coverage')
    if doc.get('markets') != ['SH', 'SZ']:
        raise ValueError('calendar_market_scope')
    source = doc.get('source')
    if not isinstance(source, dict):
        raise ValueError('calendar_source')
    kind = source.get('kind')
    if kind == 'exchange_annual':
        year = doc.get('year')
        if type(year) is not int or start != date(year, 1, 1) or end != date(year, 12, 31):
            raise ValueError('annual_coverage_must_be_explicit_full_year')
        if doc.get('complete_year') is not True or set(doc.get('holidays', {})) != HOLIDAYS:
            raise ValueError('annual_holiday_categories_incomplete')
        notices = source.get('announcements', {})
        for market, suffix in [('SH', 'sse.com.cn'), ('SZ', 'szse.cn')]:
            notice = notices.get(market, {})
            parsed = urlparse(notice.get('url', ''))
            if (parsed.scheme != 'https' or parsed.username or parsed.password
                    or parsed.port not in (None, 443) or parsed.query or parsed.fragment
                    or not (parsed.hostname == suffix or (parsed.hostname or '').endswith('.' + suffix))
                    or not notice.get('title') or not notice.get('notice_id')):
                raise ValueError('annual_announcement_source')
            if iso_day(notice.get('published_on')) > end:
                raise ValueError('annual_publication_date')
        iso_day(source.get('reviewed_on'))
        closures = set()
        ranges = list(doc['holidays'].values()) + doc.get('extra_closures', [])
        for span in ranges:
            if not isinstance(span, list) or len(span) != 2:
                raise ValueError('annual_holiday_range')
            lo, hi = map(iso_day, span)
            if not start <= lo <= hi <= end:
                raise ValueError('annual_holiday_out_of_coverage')
            closures.update((lo + timedelta(days=i)).isoformat() for i in range((hi-lo).days+1))
        dates = {(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)
                 if (start + timedelta(days=i)).weekday() < 5}
        dates -= closures
        if len(dates) < 150:
            raise ValueError('annual_calendar_implausibly_empty')
        return {'start': start, 'end': end, 'days': {'SH': dates, 'SZ': dates},
                'source': 'exchange_annual:' + str(year), 'sha256': doc['sha256']}
    if kind == 'qmt_history':
        if source.get('url') != SDK_SOURCE or source.get('coverage_basis') != 'first_and_last_returned':
            raise ValueError('qmt_source')
        requested_from = iso_day(source.get('requested_from'))
        requested_to = iso_day(source.get('requested_through'))
        refreshed = datetime.fromisoformat(doc.get('refreshed_at', ''))
        if refreshed.tzinfo is None or requested_to > refreshed.astimezone(CHINA).date():
            raise ValueError('qmt_history_cannot_prove_future')
        if not requested_from <= start <= end <= requested_to:
            raise ValueError('qmt_requested_coverage')
        market_days = doc.get('days_by_market', {})
        days = {m: _days(market_days.get(m), requested_from, requested_to) for m in ('SH', 'SZ')}
        # Never accept an operator-supplied end beyond what both calls returned.
        if (start.isoformat() != max(min(v) for v in days.values())
                or end.isoformat() != min(max(v) for v in days.values())):
            raise ValueError('qmt_coverage_exceeds_returned_data')
        return {'start': start, 'end': end, 'days': days,
                'source': 'qmt_history', 'sha256': doc['sha256']}
    raise ValueError('calendar_source_unsupported')


@dataclass(frozen=True)
class CalendarDecision:
    day: str
    status: str
    source: str
    covered_from: str | None
    covered_through: str | None
    reason: str
    code: str = ''
    evidence: str = ''
    warnings: tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)

    @property
    def issue_key(self):
        # No polling time/day bucket. A persistent fault has one event identity.
        return hashlib.sha256(f'{self.day[:4]}:{self.code}:{self.evidence}'.encode()).hexdigest()[:24]


class CalendarService:
    def __init__(self, config, bundle_dir=None):
        self.state = Path(config['state_dir'])
        self.bundle = Path(bundle_dir or config.get('calendar_dir') or Path(__file__).with_name('calendars'))
        self.markets = sorted({'SH' if m == 'KCB' else m for m in config.get('allowed_markets', ['SH', 'SZ'])})
        if not self.markets or set(self.markets) - {'SH', 'SZ'}:
            raise ValueError('calendar_market_scope')

    def annual(self, year):
        errors = []
        for path in (self.state/'calendars'/f'{year}.json', self.bundle/f'{year}.json'):
            try:
                doc = load_document(path)
                result = validate_document(doc)
                if doc['source']['kind'] != 'exchange_annual' or doc['year'] != year:
                    raise ValueError('annual_pack_identity')
                return result, errors
            except FileNotFoundError:
                continue
            except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError) as exc:
                errors.append('annual_pack_invalid:' + type(exc).__name__)
        return None, errors

    def _decision(self, day, loaded, warnings=()):
        target = iso_day(day)
        if not loaded['start'] <= target <= loaded['end']:
            return None
        flags = {day in loaded['days'][m] for m in self.markets}
        if len(flags) != 1:
            return CalendarDecision(day, UNKNOWN, loaded['source'], str(loaded['start']), str(loaded['end']),
                                    '沪深日历不一致；更新并核对交易所公告，禁止提交。',
                                    'calendar_conflict', loaded['sha256'], tuple(warnings))
        opened = flags.pop()
        return CalendarDecision(day, OPEN if opened else CLOSED, loaded['source'],
                                str(loaded['start']), str(loaded['end']),
                                '有效覆盖区间内的交易日。' if opened else '有效覆盖区间内的休市日。',
                                evidence=loaded['sha256'], warnings=tuple(warnings))

    def decide(self, day):
        target = iso_day(day)
        # Securities markets do not adopt State Council make-up working weekends.
        if target.weekday() >= 5:
            return CalendarDecision(day, CLOSED, 'SH/SZ:weekend_rule', day, day,
                                    '周末证券市场休市；不采用调休工作日。', evidence='weekend-rule-v1')
        annual, warnings = self.annual(target.year)
        annual_decision = self._decision(day, annual, warnings) if annual else None
        local_decision = None
        evidence = 'missing'
        coverage = (None, None)
        code = 'calendar_missing'
        try:
            doc = load_document(self.state/'calendar.json')
            evidence = digest({k:v for k,v in doc.items() if k not in ('refreshed_at','sha256')})
            if doc.get('schema_version') == 2:
                loaded = validate_document(doc)
            else:
                # V3.2 wrote no coverage start or verifiable source. Reuse only
                # when EVERY day in the claimed interval matches the annual pack.
                year = doc.get('year')
                if not annual or type(year) is not int or year != target.year:
                    raise ValueError('legacy_calendar_needs_annual_verification')
                start, end = date(year, 1, 1), iso_day(doc.get('covered_through'))
                if not start <= end <= date(year, 12, 31):
                    raise ValueError('legacy_coverage')
                dates = _days(doc.get('days'), start, end)
                expected = {x for x in annual['days']['SH'] if start.isoformat() <= x <= end.isoformat()}
                if dates != expected:
                    raise ValueError('legacy_calendar_does_not_match_reviewed_year')
                loaded = dict(annual, start=start, end=end, source='legacy_qmt+exchange_verified')
                warnings.append('旧缓存已按完整覆盖区间交叉核验，原文件未改写。')
            coverage = (str(loaded['start']), str(loaded['end']))
            local_decision = self._decision(day, loaded, warnings)
            if local_decision is None:
                code = 'calendar_out_of_coverage'
                warnings.append('本地缓存未覆盖所查日期。')
        except FileNotFoundError:
            warnings.append('本地缓存缺失。')
        except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError) as exc:
            code = 'calendar_invalid'
            warnings.append('本地缓存校验失败：' + type(exc).__name__ + '；不以缺少日期推断休市。')
        if local_decision:
            if (annual_decision and (local_decision.status != annual_decision.status)):
                return CalendarDecision(day, UNKNOWN, 'qmt/exchange conflict', *coverage,
                                        '本地日历与年度公告不一致；人工核对并更新后再运行。',
                                        'calendar_conflict', evidence, tuple(warnings))
            return local_decision
        if annual_decision:
            return CalendarDecision(day, annual_decision.status, annual_decision.source,
                                    annual_decision.covered_from, annual_decision.covered_through,
                                    annual_decision.reason, evidence=annual_decision.evidence,
                                    warnings=tuple(warnings))
        return CalendarDecision(day, UNKNOWN, 'unavailable', *coverage,
                                '日历缺失、损坏或覆盖期不足；运行 calendar_tool.py refresh，'
                                '或导入经核对的年度交易所日历；禁止提交。',
                                code, evidence, tuple(warnings))


def refresh_qmt(config, day, provider=None, allow_download=False, now=None):
    """Data-only SDK access, never XtQuantTrader/StockAccount or broker.connect().

    History is bounded by the FIRST/LAST actual dates for each market, never the
    requested year end. Future SDK output is cross-checked, not trusted alone.
    """
    target = iso_day(day)
    now = now or china_now()
    if target > now.astimezone(CHINA).date():
        raise ValueError('refresh_future_day_not_allowed')
    if provider is None:
        from xtquant import xtdata
        provider = xtdata
    start = date(target.year, 1, 1)
    dates = {}
    for market in ('SH', 'SZ'):
        raw = provider.get_trading_dates(market, start.strftime('%Y%m%d'), target.strftime('%Y%m%d'), -1)
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError('qmt_history_empty')
        parsed = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1e12 <= value < 1e13:
                raise ValueError('qmt_history_not_milliseconds')
            parsed.append(datetime.fromtimestamp(value / 1000, CHINA).date().isoformat())
        dates[market] = sorted(set(parsed))
        _days(dates[market], start, target)
    doc = sealed({'schema_version': 2, 'markets': ['SH', 'SZ'],
                  'covered_from': max(min(v) for v in dates.values()),
                  'covered_through': min(max(v) for v in dates.values()),
                  'days_by_market': dates, 'refreshed_at': now.isoformat(),
                  'source': {'kind': 'qmt_history', 'url': SDK_SOURCE,
                             'requested_from': str(start), 'requested_through': str(target),
                             'coverage_basis': 'first_and_last_returned'}})
    validate_document(doc)
    future_check = 'not_requested'
    if allow_download:
        # Documented download may use the network. It is NEVER part of decide().
        provider.download_holiday_data()
        annual, _ = CalendarService(config).annual(target.year)
        if not annual:
            raise ValueError('future_calendar_needs_reviewed_annual_pack')
        for market in ('SH', 'SZ'):
            raw = provider.get_trading_calendar(market, f'{target.year}0101', f'{target.year}1231')
            if not isinstance(raw, (list, tuple)):
                raise ValueError('qmt_future_calendar_structure')
            parsed = []
            for value in raw:
                if not isinstance(value, str) or not re.fullmatch(r'\d{8}', value):
                    raise ValueError('qmt_future_calendar_date_format_needs_local_verification')
                parsed.append(datetime.strptime(value, '%Y%m%d').date().isoformat())
            if len(parsed) != len(set(parsed)) or set(parsed) != annual['days'][market]:
                raise ValueError('qmt_future_calendar_mismatch')
        future_check = 'matched_reviewed_annual_pack_no_coverage_inference'
    with single_instance(Path(config['state_dir'])/'calendar-write.lock'):
        atomic_json(Path(config['state_dir'])/'calendar.json', doc)
    return {'status': 'updated', 'covered_from': doc['covered_from'],
            'covered_through': doc['covered_through'], 'future_check': future_check}


def ensure_calendar(config, day, now=None, runner=subprocess.run):
    """An unknown weekday may refresh data in a bounded subprocess, without an
    account connection. Missing calendar -> data refresh -> re-evaluate: no loop.
    """
    service = CalendarService(config)
    result = service.decide(day)
    if result.status != UNKNOWN or result.code == 'calendar_conflict':
        return result
    now = now or china_now()
    state = Path(config['state_dir'])
    try:
        with single_instance(state/'calendar-refresh.lock'):
            try:
                previous = read_json(state/'calendar-refresh.json')
                if 0 <= now.timestamp() - previous['attempted_at'] < 1800:
                    return result
            except (OSError, ValueError, KeyError, TypeError):
                pass
            record = {'attempted_at': now.timestamp(), 'status': 'running'}
            atomic_json(state/'calendar-refresh.json', record)
            command = [sys.executable, '-B', str(Path(__file__).with_name('calendar_tool.py')),
                       'refresh', '--state-dir', str(state), '--day', day]
            if config.get('calendar_dir'):
                command += ['--calendar-dir', config['calendar_dir']]
            try:
                process = runner(command, capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                record['status'] = 'finished' if process.returncode == 0 else 'refresh_failed'
            except Exception as exc:
                record.update(status='refresh_failed', error_type=type(exc).__name__)
            atomic_json(state/'calendar-refresh.json', record)
    except OSError:
        pass  # Another refresher or an unwritable cache cannot grant permission.
    return service.decide(day)
