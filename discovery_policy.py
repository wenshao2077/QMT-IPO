"""Bounded rediscovery after round completion; does not grant submission permission."""
from datetime import datetime

CHECKPOINTS = ((10, 0), (11, 0), (13, 0), (14, 0), (14, 40), (15, 5))


def next_discovery_at(checked_at, now):
    """Legacy/malformed receipts get one safe re-query, never a fabricated result."""
    try:
        checked = datetime.fromisoformat(checked_at)
        if checked.tzinfo is None or now.tzinfo is None:
            return now
        checked = checked.astimezone(now.tzinfo)
        if checked.date() != now.date() or checked > now:
            return now
    except (TypeError, ValueError):
        return now
    for hour, minute in CHECKPOINTS:
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due > checked:
            return due
    return now.replace(hour=15, minute=5, second=0, microsecond=0)
