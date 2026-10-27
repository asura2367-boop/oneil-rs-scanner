#!/usr/bin/env python3
"""V5.14: KIS quote and buying-power observational checks. NO ORDERS EVER.

Broker observations stay in process memory; only statuses go to public artifacts.
KIS legacy quote adapters lack a verified exchange+quote timestamp, therefore
broker-observed prices cannot be certified as live and no tradeable qty is set.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from oneil_live_v5_13 import check_intents, flags_off
KST = ZoneInfo('Asia/Seoul')


def broker_observer(codes, now):
    """Existing read-only adapters; never return price/cash values to report."""
    from kis_live_v4_5_2 import _key, _read, UTC, CacheBlocked
    from kis_buying_power_readonly import credentials, get_buying_power
    from kis_safety_v3 import current_quote
    token_record = _read(_key(), dt.datetime.now(UTC))
    if not token_record:
        raise CacheBlocked('TOKEN_CACHE_EXPIRED_OR_MISSING')
    token = token_record[0]
    app_key, app_secret, account, product = credentials()
    observed = {}
    for code in codes:
        try:
            price = current_quote(app_key, app_secret, token, code, now)
            if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
                observed[code] = 'PRICE_INVALID'
                continue
            cash, qty = get_buying_power(app_key, app_secret, account, product, token, code, price)
            if not all(type(x) is int and x >= 0 for x in (cash, qty)):
                observed[code] = 'BUYING_POWER_INVALID'
            else:
                observed[code] = 'OBSERVED_TIMESTAMP_AND_VENUE_UNVERIFIED'
        except Exception:
            observed[code] = 'BROKER_QUERY_BLOCKED'
    return observed


def review(intents, now, broker_read=False, observer=broker_observer, env=None):
    env = dict(os.environ if env is None else env)
    if not flags_off(env):
        raise RuntimeError('LIVE_FLAGS_MUST_REMAIN_FALSE')
    codes = check_intents(intents)
    # Strict: no prior reports confer fresh price verification or authorization.
    statuses = {c:'NOT_REQUESTED' for c in codes}
    if broker_read:
        try:
            raw=observer(codes,now)
            if not isinstance(raw,dict) or set(raw)!=set(codes):
                raise ValueError('INCOMPLETE_OBSERVATION')
            allowed={'PRICE_INVALID','BUYING_POWER_INVALID',
                     'OBSERVED_TIMESTAMP_AND_VENUE_UNVERIFIED','BROKER_QUERY_BLOCKED'}
            if any(type(v) is not str or v not in allowed for v in raw.values()):
                raise ValueError('INVALID_OBSERVATION')
            statuses=raw
        except Exception:
            statuses={c:'BROKER_READ_BLOCKED' for c in codes}
    rows=[{'ticker':c,'read_status':statuses[c],
           'quote_fresh_verified':False,'exchange_verified':False,
           'orderable_cash_verified_for_execution':False,
           'approved_price_krw':None,'approved_quantity':None,
           'held_or_unfilled_clearance_verified':False} for c in codes]
    return {'version':'5.14','mode':'BROKER_OBSERVATION_ONLY',
            'as_of_kst':now.isoformat(timespec='seconds'),
            'candidate_count':len(codes),'read_requested':bool(broker_read),
            'rows':rows,'reserve_pct':40,'trading_pct':60,
            'stock_pct_of_trading_budget':30,
            'gate':'DENY','blocking_reasons':['QUOTE_TIME_NOT_CERTIFIED',
                'EXCHANGE_NOT_CERTIFIED','DAILY_PNL_NOT_VERIFIED',
                'ORDER_EXECUTION_NOT_IMPLEMENTED'],
            'actual_orders_sent':0,'broker_order_api_calls':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--intents',default='v5_11_review.json')
    ap.add_argument('--output',default='v5_14_review.json')
    ap.add_argument('--broker-read',action='store_true')
    args=ap.parse_args()
    obj=json.loads(Path(args.intents).read_text(encoding='utf-8'))
    result=review(obj,dt.datetime.now(KST),broker_read=args.broker_read)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    statuses={x['read_status'] for x in result['rows']}
    print('V5.14 GATE=DENY; candidates='+str(result['candidate_count'])+
          '; observation_statuses='+','.join(sorted(statuses))+
          '; priced_verified=0; orderable_qty_verified=0; orders_sent=0; '+
          'LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__': main()
