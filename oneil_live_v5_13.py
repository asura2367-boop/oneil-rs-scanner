#!/usr/bin/env python3
"""V5.13: LIVE candidate vs KIS read-only held/unfilled review; NEVER ORDERS.

Raw broker observations remain process-local. Output stores only candidate codes,
block flags, and statuses; no account number, balances, tokens, or raw responses.
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CODE = re.compile(r'^[0-9]{6}$')

def flags_off(env):
    return env.get('LIVE_ENABLED', 'false').lower() == 'false' and env.get('KIS_ALLOW_ORDER', 'false').lower() == 'false'

def check_intents(obj):
    if not isinstance(obj, dict) or obj.get('mode') != 'LIVE_INTENTS_PREVIEW_ONLY' or obj.get('gate') != 'DENY' or obj.get('orders_sent') != 0:
        raise ValueError('UNSAFE_CANDIDATE_SOURCE')
    items = obj.get('intents')
    if not isinstance(items, list) or len(items) > 30:
        raise ValueError('BAD_CANDIDATE_LIST')
    codes = []
    for row in items:
        if not isinstance(row, dict) or row.get('status') != 'REVIEW_ONLY_UNPRICED' or row.get('quantity') is not None or row.get('price_krw') is not None:
            raise ValueError('CANDIDATE_NOT_UNPRICED')
        code = row.get('ticker')
        if not isinstance(code, str) or not CODE.fullmatch(code) or code in codes:
            raise ValueError('BAD_OR_DUPLICATE_CODE')
        codes.append(code)
    return codes

def observe_kis_cached():
    """Use ONLY already valid encrypted cache. No new token issuance or token persistence."""
    from kis_live_v4_5_2 import _read, _key, UTC, CacheBlocked
    from kis_buying_power_readonly import credentials
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes
    now = dt.datetime.now(UTC)
    cached = _read(_key(), now)
    if not cached:
        raise CacheBlocked('CACHE_MISSING_OR_EXPIRED')
    token, _expiry = cached
    key, secret, cano, product = credentials()
    held, _invested = verified_balance_snapshot(key, secret, cano, product, token)
    unfilled = unfilled_codes(key, secret, cano, product, token)
    # Do not return valuation, credentials, token, raw broker response.
    return held, unfilled

def review(intents, now, *, broker_read=False, observer=observe_kis_cached, env=None):
    env = dict(os.environ if env is None else env)
    if not flags_off(env):
        raise RuntimeError('LIVE_FLAGS_NOT_DISABLED')
    codes = check_intents(intents)
    status = 'NOT_REQUESTED'
    held = unfilled = None
    if broker_read:
        try:
            held, unfilled = observer()
            if not isinstance(held, set) or not isinstance(unfilled, set) or any(not isinstance(c,str) or not CODE.fullmatch(c) for c in held | unfilled):
                raise ValueError('INVALID_BROKER_OBSERVATION')
            status = 'READ_ONLY_OBSERVED'
        except Exception:
            status = 'BROKER_READ_BLOCKED'  # No exception text or raw data in logs.
            held = unfilled = None
    checks = []
    for ticker in codes:
        checks.append({'ticker':ticker,
                       'held': ('YES' if ticker in held else 'NO') if held is not None else 'UNKNOWN',
                       'unfilled': ('YES' if ticker in unfilled else 'NO') if unfilled is not None else 'UNKNOWN',
                       'price_krw': None, 'orderable_cash_krw':None, 'preview_quantity':None,
                       'gate':'DENY'})
    return {'version':'5.13','as_of_kst':now.isoformat(timespec='seconds'),
            'mode':'LIVE_READ_ONLY_REVIEW', 'broker_status':status,
            'candidate_count':len(codes),'checks':checks,
            'cash_verified':False,'live_quotes_verified':False,'daily_pnl_verified':False,
            'actual_order_qty_verified':False,'gate':'DENY','orders_sent':0,
            'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'blocking_reasons':['CASH_NOT_VERIFIED','LIVE_QUOTE_NOT_VERIFIED',
                                'DAILY_PNL_NOT_VERIFIED','ORDER_EXECUTION_NOT_IMPLEMENTED']}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--intents',default='v5_11_review.json')
    p.add_argument('--output',default='v5_13_review.json')
    p.add_argument('--broker-read',action='store_true')
    a=p.parse_args()
    obj=json.loads(Path(a.intents).read_text(encoding='utf-8'))
    r=review(obj,dt.datetime.now(KST),broker_read=a.broker_read)
    Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'V5.13 GATE=DENY; candidates={r["candidate_count"]}; broker_status={r["broker_status"]}; priced=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__': main()
