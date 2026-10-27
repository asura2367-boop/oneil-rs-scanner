#!/usr/bin/env python3
"""V4.1 read-only KIS preflight. No order endpoints, no real trade capability."""
import argparse
import datetime as dt
import json
import os
import sys
from urllib.error import HTTPError, URLError
from pathlib import Path
from zoneinfo import ZoneInfo

from kis_live_v4 import protect, prepare, read_json, SafetyBlock, CAP, PER_STOCK_CAP

KST = ZoneInfo('Asia/Seoul')
STAGE = 'initialization'  # Explicit, non-sensitive stage labels only


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



def write_blocked_preflight(path, now, reason, *, source_date=None):
    """Persist explicit DENY evidence; never impersonate verified KIS preflight."""
    if reason not in ('NO_INTENTS', 'MARKET_CLOSED'):
        raise ValueError('UNSUPPORTED_BLOCK_REASON')
    doc = {'mode': 'READ_ONLY_PREFLIGHT_BLOCKED',
           'generated_at_kst': now.isoformat(),
           'status': 'DENY', 'reason': reason,
           'paper_source_date': source_date,
           'broker_verified': False, 'live_enabled': False,
           'orders_sent': 0, 'decisions': [],
           'note': 'No KIS preflight performed; not a live trading authorization.'}
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + '.tmp')
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(dest)

def main():
    global STAGE
    STAGE = 'initialization'
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='data/paper/state.json')
    parser.add_argument('--summary', default='data/paper/paper_summary.json')
    parser.add_argument('--output', default='data/live/v4_1_preflight.json')
    args = parser.parse_args()
    STAGE = 'safety_guard'
    protect()
    now = dt.datetime.now(KST)
    if now.weekday() >= 5 or not(dt.time(9, 0) <= now.time() < dt.time(15, 30)):
        write_blocked_preflight(args.output, now, 'MARKET_CLOSED')
        print('V4.1 BLOCKED_MARKET_CLOSED; orders_sent=0; LIVE_ENABLED=false')
        return
    STAGE = 'candidate_plan'
    plan = prepare(read_json(args.state), read_json(args.summary), now)
    if not plan['intents']:
        write_blocked_preflight(args.output, now, 'NO_INTENTS', source_date=plan.get('source_date'))
        print('V4.1 NO_INTENTS; report=BLOCKED_NO_CANDIDATES; orders_sent=0; LIVE_ENABLED=false')
        return
    # Import ONLY after market and offline input gates. All imported helpers are read-only.
    from kis_buying_power_readonly import credentials, request, get_buying_power
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes, current_quote
    STAGE = 'credentials_format'
    key, secret, cano, product = credentials()
    STAGE = 'authentication'
    # V4.5.1 can supply a short-lived token through this process environment.
    # If it is absent, keep standalone V4.1 behavior unchanged.
    token = os.environ.get('KIS_SESSION_ACCESS_TOKEN', '').strip()
    if not token:
        auth = request('POST', '/oauth2/tokenP',
                       {'grant_type':'client_credentials', 'appkey':key, 'appsecret':secret})
        token = auth.get('access_token') if isinstance(auth, dict) else None
    if not token:
        raise SafetyBlock('AUTH_UNAVAILABLE')
    STAGE = 'balance_valuation'
    held, invested = verified_balance_snapshot(key,secret,cano,product,token)
    STAGE = 'unfilled_orders'
    unfilled = unfilled_codes(key,secret,cano,product,token)
    STAGE = 'quote_and_buying_power'
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


def safe_diagnostic(exc, stage):
    """Only whitelist stage and HTTP status; do not render URL, response, token or keys."""
    known = {'initialization', 'safety_guard', 'candidate_plan',
             'credentials_format', 'authentication', 'balance_valuation',
             'unfilled_orders', 'quote_and_buying_power'}
    stage = stage if stage in known else 'unknown'
    if isinstance(exc, HTTPError):
        code = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else 'unknown'
        return f'stage={stage}; error_type=HTTPError; http_status={code}'
    if isinstance(exc, URLError):
        return f'stage={stage}; error_type=URLError; http_status=none'
    if isinstance(exc, SafetyBlock):
        # Do not print exception message: it may not be a trusted constant.
        return f'stage={stage}; error_type=SafetyBlock; http_status=none'
    return f'stage={stage}; error_type={type(exc).__name__ if type(exc) in (ValueError, TypeError, RuntimeError, KeyError, OSError) else "Other"}; http_status=none'


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('V4.1 BLOCKED: ' + safe_diagnostic(exc, STAGE) +
              '; orders_sent=0; LIVE_ENABLED=false', file=sys.stderr)
        sys.exit(1)
