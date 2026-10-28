#!/usr/bin/env python3
"""V5.19 same-run scan -> KIS read-only evidence -> Supabase read-only status.
NEVER sends, reserves or amends orders. Fail closed on incomplete evidence.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from v519_same_run_candidates import build
from v519_pg_ledger import connect, status

KST = ZoneInfo('Asia/Seoul')

def review(csv_path, day, *, now=None, broker=None, ledger=None):
    now = now or dt.datetime.now(KST)
    if os.getenv('LIVE_ENABLED', 'false').lower() != 'false' or os.getenv('KIS_ALLOW_ORDER', 'false').lower() != 'false':
        raise ValueError('LIVE_SWITCH_MUST_BE_OFF')
    if now.astimezone(KST).date().isoformat() != day:
        raise ValueError('DAY_MISMATCH')
    candidates = build(csv_path, day, now)['candidates']
    if not candidates:
        return {'gate':'DENY','orders_sent':0,'status':'NO_ELIGIBLE_CANDIDATES','candidates':[]}
    if ledger is None or broker is None:
        raise ValueError('BROKER_AND_LEDGER_REQUIRED')
    control = ledger()
    if control['halted'] is not True or control['unresolved'] != 0:
        raise ValueError('LEDGER_NOT_HALTED_OR_UNRESOLVED')
    rows=[]
    for c in candidates:
        evidence = broker(c['ticker'])
        if evidence.get('ticker') != c['ticker']:
            raise ValueError('BROKER_TICKER_MISMATCH')
        price=evidence.get('price')
        if type(price) is not int or price<=0:
            raise ValueError('INVALID_REFERENCE_PRICE')
        if evidence.get('held') or evidence.get('pending'):
            # Refuse even theoretical quantity for existing holdings or pending orders.
            qty=0
        else:
            qty=min(180000//price, evidence.get('broker_qty',0), evidence.get('broker_cash',0)//price)
        rows.append({'ticker':c['ticker'],'rs':c['rs'],'reference_price':price,
                     'theoretical_max_qty':max(0,qty),'quote_executable_verified':False,
                     'order_authorized':False})
    return {'gate':'DENY','orders_sent':0,'status':'READ_ONLY_BROKER_REVIEW',
            'risk_limits_krw':{'capital':1000000,'reserve':400000,'trading':600000,'per_stock':180000},
            'candidates':rows}

def broker_adapter():
    from oneil_v519_live_runner import token, price_reference, buying_power
    from kis_buying_power_readonly import credentials
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes
    key,secret,cano,prod=credentials()
    tok=token(key,secret)
    held,_=verified_balance_snapshot(key,secret,cano,prod,tok)
    pending=unfilled_codes(key,secret,cano,prod,tok)
    def get(ticker):
        price=price_reference(tok,key,secret,ticker)
        amount,qty=buying_power(tok,key,secret,cano,prod,ticker,price)
        return {'ticker':ticker,'price':price,'broker_qty':qty,'broker_cash':amount,
                'held':ticker in held,'pending':bool(pending)}
    return get

def ledger_adapter():
    with connect() as db:
        return status(db)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--csv',required=True)
    parser.add_argument('--date',required=True)
    parser.add_argument('--output',default='v519_broker_review.json')
    args=parser.parse_args()
    try:
        result=review(args.csv,args.date,broker=broker_adapter(),ledger=ledger_adapter())
        code=0
    except Exception:
        result={'gate':'DENY','orders_sent':0,'status':'BLOCKED','candidates':[]}
        code=2
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V519_REVIEW='+result['status']+'; orders_sent=0')
    raise SystemExit(code)
if __name__=='__main__':main()
