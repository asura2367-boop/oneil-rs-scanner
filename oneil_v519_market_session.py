"""KRX order sessions. A clock window never proves venue or quote eligibility.

KRX after-market started 2026-09-14. The 15:20–16:00 closing/after-close
auction interval is intentionally excluded. NXT routing is not inferred.
"""
import datetime as dt
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
AFTER_START_DATE = dt.date(2026, 9, 14)
TRADING_HOURS = '09:00–15:20 / 16:00–20:00 KST (KRX)'


def execution_session(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return None
    time = local.time().replace(tzinfo=None)
    if dt.time(9) <= time < dt.time(15, 20):
        return 'REGULAR'
    if local.date() >= AFTER_START_DATE and dt.time(16) <= time < dt.time(20):
        return 'AFTER'
    return None


def limit_order_code(session):
    if session == 'REGULAR':
        return '00'
    if session == 'AFTER':
        return '41'
    raise ValueError('OUTSIDE_KRX_ORDER_SESSION')


def session_close(now):
    session = execution_session(now)
    local = now.astimezone(KST)
    if session == 'REGULAR':
        return local.replace(hour=15, minute=20, second=0, microsecond=0)
    if session == 'AFTER':
        return local.replace(hour=20, minute=0, second=0, microsecond=0)
    raise ValueError('OUTSIDE_KRX_ORDER_SESSION')
