#!/usr/bin/env python3
"""V4.1 read-only KIS preflight. No order endpoints, no real trade capability."""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from kis_live_v4 import protect, prepare, read_json, SafetyBlock, CAP, PER_STOCK_CAP

KST = ZoneInfo('Asia/Seoul')


def evaluate(intents, now, held, invested, unfilled, quote, power):
    """Pure, fail-closed testable decision function. No account values in output."""
    if not isinstance(invested, int) or invested < 0:
        raise SafetyBlock('INVALID_PORTFOLIO_VALUATION')
    if not isinstance(held, set) or not isinstance(unfilled, set):
        raise SafetyBlock('UNVERIFIED_ACCOUNT_STATE')
    available = max(0, CAP - invested)
    results = []
    seen = set()
    for item in intents:
        code = item['ticker']
        status = 'BLOCKED'
        qty = amount = price = 0
        signal_date = dt.date.fromisoformat(item['signal_date'])
        business_days = sum((signal_date + dt.timedelta(days=i)).weekday() < 5
                            for i in range(1, (now.date()-signal_date).days + 1)) if now.date() >= signal_date else 100
        if code in seen:
            status = 'BLOCKED_DUPLICATE_INTENT'
        elif business_days > 1 or now.date() <= signal_date:
            status = 'BLOCKED_STALE_OR_FUTURE_SIGNAL'
        elif code in held:
            status = 'BLOCKED_ALREADY_HELD'
        elif code in unfilled:
            status = 'BLOCKED_UNFILLED_ORDER'
        elif available <= 0:
            status = 'BLOCKED_TOTAL_CAP'
        else:
            try:
                price = int(quote(code))
                ref = float(item['estimated_price_krw'])
                if price <= 0 or ref <= 0:
                    status = 'BLOCKED_PRICE_INVALID'
                elif price > ref * 1.03:
                    status = 'BLOCKED_PRICE_GAP'
                else:
                    cash, broker_qty = power(code, price)
                    if not all(isinstance(n, int) and n >= 0 for n in (cash, broker_qty)):
                        status = 'BLOCKED_BUYING_POWER_INVALID'
                    else:
                        qty = min(PER_STOCK_CAP // price, available // price,
                                  cash // price, broker_qty)
                        if qty:
                            amount = price * qty
                            available -= amount
                            status = 'PREVIEW_ELIGIBLE_NO_ORDER'
                        else:
                            status = 'BLOCKED_BUYING_POWER_OR_CAP'
            except Exception:
                status = 'BLOCKED_DATA_OR_API_ERROR'
                qty = amount = price = 0
        seen.add(code)
        results.append({'ticker':code, 'intent_id':item['intent_id'], 'status':status,
                        'reference_only_price_krw':price, 'preview_qty':qty,
                        'preview_amount_krw':amount, 'orders_sent':0})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='data/paper/state.json')
    parser.add_argument('--summary', default='data/paper/paper_summary.json')
    parser.add_argument('--output', default='data/live/v4_1_preflight.json')
    args = parser.parse_args()
    protect()
    now = dt.datetime.now(KST)
    if now.weekday() >= 5 or not(dt.time(9, 0) <= now.time() < dt.time(15, 30)):
        print('V4.1 BLOCKED_MARKET_CLOSED; orders_sent=0; LIVE_ENABLED=false')
        return
    plan = prepare(read_json(args.state), read_json(args.summary), now)
    if not plan['intents']:
        print('V4.1 NO_INTENTS; orders_sent=0; LIVE_ENABLED=false')
        return
    # Import ONLY after market and offline input gates. All imported helpers are read-only.
    from kis_buying_power_readonly import credentials, request, get_buying_power
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes, current_quote
    key, secret, cano, product = credentials()
    auth = request('POST', '/oauth2/tokenP',
                   {'grant_type':'client_credentials', 'appkey':key, 'appsecret':secret})
    token = auth.get('access_token') if isinstance(auth, dict) else None
    if not token:
        raise SafetyBlock('AUTH_UNAVAILABLE')
    held, invested = verified_balance_snapshot(key,secret,cano,product,token)
    unfilled = unfilled_codes(key,secret,cano,product,token)
    decisions = evaluate(plan['intents'], now, held, invested, unfilled,
                         lambda t: current_quote(key,secret,token,t,now),
                         lambda t,p: get_buying_power(key,secret,cano,product,token,t,p))
    doc = {'mode':'READ_ONLY_PREFLIGHT_V4_1', 'generated_at_kst':now.isoformat(),
           'paper_source_date':plan['source_date'], 'live_enabled':False,
           'orders_sent':0, 'note':'Indicative only; not a live order or trade approval.',
           'decisions':decisions}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    print('V4.1 READ_ONLY PREFLIGHT COMPLETE; orders_sent=0; LIVE_ENABLED=false')
    for decision in decisions:
        print(decision['ticker'], decision['status'],
              'preview_qty=', decision['preview_qty'],
              'preview_krw=',decision['preview_amount_krw'])
    if decisions and all(d['status'] == 'BLOCKED_DATA_OR_API_ERROR' for d in decisions):
        raise SafetyBlock('ALL_CANDIDATE_DATA_FAILURE')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, SafetyBlock) else type(exc).__name__
        print(f'V4.1 BLOCKED: {code}; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        sys.exit(1)
