#!/usr/bin/env python3
"""KIS V4.10: broker read-only capital allocation, KRX regular hours only. NO ORDERS."""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from kis_live_v4_9 import allocate, pct
from kis_buying_power_readonly import credentials, request, get_buying_power
from kis_safety_v3 import verified_balance_snapshot, unfilled_codes, current_quote
from live_trade import make_plan

KST = ZoneInfo('Asia/Seoul')

class Stop(Exception):
    pass

def regular_session(now):
    return now.weekday() < 5 and dt.time(9, 0) <= now.time() < dt.time(15, 30)

def build_verified_budget(plans, held, unfilled, invested, quote, power, reserve=40, stock=30):
    """Fail closed if ANY required broker inquiry fails. Safe read-only estimates."""
    pct(reserve); pct(stock)
    if not plans or len(plans) > 20:
        raise Stop('NO_VALID_CANDIDATES')
    rows=[]; amounts=[]; seen=set()
    for plan in plans:
        ticker=plan.get('ticker')
        if not isinstance(ticker,str) or len(ticker)!=6 or not ticker.isdigit() or not ticker.isascii() or ticker in seen:
            raise Stop('BAD_OR_DUPLICATE_TICKER')
        seen.add(ticker)
        if ticker in held or ticker in unfilled:
            raise Stop('HELD_OR_UNFILLED_CANDIDATE')
        if plan.get('status') != 'PREVIEW_ONLY':
            raise Stop('CANDIDATE_NOT_READY')
        price=quote(ticker)
        if type(price) is not int or price <= 0:
            raise Stop('INVALID_QUOTE')
        reference=plan.get('reference_close')
        try: reference=int(reference)
        except (ValueError, TypeError): raise Stop('INVALID_REFERENCE') from None
        if reference<=0 or price*100 > reference*103:
            raise Stop('PRICE_GAP_OR_REFERENCE')
        cash,qty=power(ticker,price)
        if type(cash) is not int or type(qty) is not int or cash<0 or qty<0:
            raise Stop('UNVERIFIED_BUYING_POWER')
        amounts.append(cash)
        rows.append({'ticker':ticker, 'price':price, 'broker_max_qty':qty})
    # Per-symbol buying power is NOT identical to unrestricted cash.
    # Use the minimum verified amount and conservatively deduct existing holdings.
    if type(invested) is not int or invested<0:
        raise Stop('UNVERIFIED_PORTFOLIO')
    base=max(0,min(amounts)-invested)
    result=allocate(base,rows,reserve,stock,verified=True)
    result['method']='CONSERVATIVE_MIN_SYMBOL_BUYING_POWER_MINUS_HELD_VALUATION'
    result['all_broker_reads_verified']=True
    result['session']='KRX_REGULAR_ONLY'
    # No actionable authorization, even when estimates are positive.
    result['gate']='DENY'
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--reserve-pct',default='40')
    p.add_argument('--stock-pct',default='30')
    args=p.parse_args()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise Stop('ORDER_SWITCH_ENABLED')
    now=dt.datetime.now(KST)
    if not regular_session(now):
        print('V4.10 BLOCKED_MARKET_CLOSED; session=KRX_REGULAR_ONLY; orders_sent=0; LIVE_ENABLED=false')
        return
    key,secret,cano,product=credentials()
    plans=make_plan(Path('data/paper/state.json'), Path('data/paper/paper_summary.json'))['plans']
    if not plans: raise Stop('NO_CANDIDATES')
    auth=request('POST','/oauth2/tokenP',{'grant_type':'client_credentials','appkey':key,'appsecret':secret})
    token=auth.get('access_token')
    if not isinstance(token,str) or not token: raise Stop('AUTH_NOT_VERIFIED')
    held,invested=verified_balance_snapshot(key,secret,cano,product,token)
    unfilled=unfilled_codes(key,secret,cano,product,token)
    result=build_verified_budget(plans,held,unfilled,invested,
        lambda t: current_quote(key,secret,token,t,now),
        lambda t,price:get_buying_power(key,secret,cano,product,token,t,price),
        args.reserve_pct,args.stock_pct)
    print('V4.10 READ_ONLY VERIFIED; reserve_pct='+str(result['reserve_pct'])+
          '; trading_pct='+str(100-pct(args.reserve_pct))+
          '; stock_pct='+str(result['per_stock_pct_of_trading_budget']))
    print('broker_verified=true; gate=DENY; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    for row in result['rows']:
        print(row['ticker'],'PREVIEW_ONLY_NO_ORDER', 'preview_qty=',row['preview_qty'],
              'preview_amount_krw=',row['preview_amount_krw'])
    print('NOT A TRADE AUTHORIZATION: broker snapshots may be delayed; regular session only.')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        # Never log raw API bodies, secrets, query strings, or account amounts.
        print('V4.10 BLOCKED; error_type='+type(exc).__name__+
              '; gate=DENY; orders_sent=0; LIVE_ENABLED=false', file=sys.stderr)
        sys.exit(1)
