"""V5.19 durable order submission bridge.

IMPORTANT: staged integration, not a standalone strategy or live-trading approval.
No order is transmitted until all explicit gates are satisfied. The broker may
accept an order before a network timeout: unknown results are NEVER retried.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from zoneinfo import ZoneInfo

from v519_pg_ledger import connect, reserve, LedgerBlocked
from oneil_v519_live_runner import token, live_submit

KST = ZoneInfo('Asia/Seoul')
CODE = re.compile(r'^[0-9]{6}$')


def require_approval():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise LedgerBlocked('EXPECTED_GITHUB_ACTIONS')
    if not (os.environ.get('LIVE_ENABLED') == 'true' and
            os.environ.get('KIS_ALLOW_ORDER') == 'true' and
            os.environ.get('I_APPROVE_LIVE_KIS_ORDERS') == 'YES'):
        raise LedgerBlocked('LIVE_SWITCHES_OFF')
    # Do not accept a self-attested flag as evidence of a complete risk review.
    # This stage deliberately does not lift the independent risk gate.
    raise LedgerBlocked('INDEPENDENT_ACCOUNT_RISK_GATE_NOT_INTEGRATED')


def validate(ticker, side, qty, price, signal):
    if not CODE.fullmatch(ticker) or side not in ('BUY', 'SELL'):
        raise LedgerBlocked('INVALID_ORDER')
    if type(qty) is not int or type(price) is not int or qty <= 0 or price <= 0:
        raise LedgerBlocked('INVALID_SIZE')
    if not signal or len(signal) > 128:
        raise LedgerBlocked('SIGNAL_REQUIRED')
    now = dt.datetime.now(KST)
    if now.weekday() >= 5 or not dt.time(9, 0) <= now.time() < dt.time(15, 30):
        raise LedgerBlocked('OUTSIDE_REGULAR_SESSION')
    return now


def deterministic_id(account, day, ticker, side, signal):
    return hashlib.sha256(f'v519|{account}|{day}|{ticker}|{side}|{signal}'.encode()).hexdigest()


def prepare_only(args):
    """Validate proposal without reserving or sending a broker order."""
    now = validate(args.ticker, args.side, args.quantity, args.price, args.signal_id)
    return {'phase': 'PROPOSAL', 'gate': 'DENY', 'orders_sent': 0,
            'intent_id': deterministic_id('REDACTED', now.date(), args.ticker,
                                          args.side, args.signal_id)}


def execute(args):
    # Explicitly deny before connecting to database or broker. This is the
    # remaining integration boundary, not a claim of completed live trading.
    require_approval()
    raise AssertionError('unreachable')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ticker', required=True)
    p.add_argument('--side', required=True, choices=['BUY', 'SELL'])
    p.add_argument('--quantity', required=True, type=int)
    p.add_argument('--price', required=True, type=int)
    p.add_argument('--signal-id', required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    try:
        result = execute(a) if a.execute else prepare_only(a)
        print(json.dumps(result))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, LedgerBlocked) else 'UNVERIFIED_FAILURE'
        print(json.dumps({'gate': 'DENY', 'orders_sent': 0, 'reason': reason}))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
