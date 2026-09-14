"""Bounded LOCAL calendar look-ahead. Never downloads or connects to an account.

Knowing that Saturday is closed does not prove Monday's calendar. Coverage here
means dates actually checked (including weekday conflicts), not requested years.
"""
from datetime import timedelta
import hashlib

from market_calendar import CalendarService, UNKNOWN, iso_day
from support import china_now


def coverage_report(config, day=None, *, horizon_days=120, warning_days=90, service=None):
    if type(horizon_days) is not int or not 1 <= horizon_days <= 370:
        raise ValueError('invalid_calendar_horizon')
    if type(warning_days) is not int or not 0 <= warning_days <= horizon_days:
        raise ValueError('invalid_calendar_warning_days')
    today = iso_day(day or china_now().date().isoformat())
    service = service or CalendarService(config)
    verified_through = None
    unknown = None
    checked = 0
    for offset in range(horizon_days + 1):
        stamp = today + timedelta(days=offset)
        decision = service.decide(stamp.isoformat())
        checked += 1
        if decision.status == UNKNOWN:
            unknown = decision
            break
        verified_through = stamp.isoformat()
    first_unknown = unknown.day if unknown else None
    remaining = (iso_day(first_unknown) - today).days if first_unknown else None
    # On a weekend an unknown upcoming Monday is an actionable maintenance issue,
    # even though today's CLOSED gate correctly avoids account connections.
    next_weekday = today
    while next_weekday.weekday() >= 5:
        next_weekday += timedelta(days=1)
    status = ('blocked' if unknown and iso_day(first_unknown) <= next_weekday else
              'expiring' if remaining is not None and remaining <= warning_days else 'ready')
    severity = ('critical' if status == 'blocked' else 'urgent' if remaining is not None and remaining <= 7
                else 'warning' if remaining is not None and remaining <= 30
                else 'notice' if status == 'expiring' else 'none')
    issue_code = ('calendar_conflict_ahead' if unknown and unknown.code == 'calendar_conflict'
                  else 'calendar_coverage_missing' if status == 'blocked'
                  else 'calendar_coverage_expiring' if status == 'expiring' else None)
    key = hashlib.sha256(f'{first_unknown}:{issue_code}:{severity}'.encode()).hexdigest()[:24] if issue_code else None
    return {'schema_version': 1, 'status': status, 'severity': severity, 'issue_code': issue_code,
            'issue_key': key, 'checked_from': today.isoformat(), 'checked_days': checked,
            'horizon_days': horizon_days, 'verified_through': verified_through,
            'first_unknown_day': first_unknown, 'days_to_first_unknown': remaining,
            'horizon_fully_verified': unknown is None,
            'next_action': 'review_and_import_annual_pack' if issue_code else 'none',
            'coverage_not_extrapolated': True, 'account_connected': False, 'downloaded': False}
