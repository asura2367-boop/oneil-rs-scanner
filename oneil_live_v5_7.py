#!/usr/bin/env python3
"""V5.7 offline, synthetic only. NO network imports, NO broker order endpoints."""
import argparse
import datetime as dt
import hashlib
import json
import os
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

def d(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('INVALID_NUMBER')
    x = Decimal(str(value))
    if not x.is_finite(): raise ValueError('INVALID_NUMBER')
    return x

def validate(data, reserve=40, stock=30, evening=30):
    if os.getenv('LIVE_ENABLED','false').lower() != 'false' or os.getenv('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise ValueError('LIVE_GUARD_NOT_DISABLED')
    rates=[d(x) for x in (reserve,stock,evening)]
    if not (0<=rates[0]<=90 and 0<rates[1]<=100 and 0<=rates[2]<=100):
        raise ValueError('INVALID_PERCENT')
    now=dt.datetime.fromisoformat(data['observed_at_kst'])
    if now.tzinfo is None or now.utcoffset()!=dt.timedelta(hours=9): raise ValueError('INVALID_KST_TIMESTAMP')
    minute=now.hour*60+now.minute
    session=('REGULAR' if now.weekday()<5 and 540<=minute<930 else
             'EVENING_REVIEW' if now.weekday()<5 and 940<=minute<1200 else 'CLOSED')
    reasons=['ORDER_TRANSMISSION_NOT_IMPLEMENTED','SYNTHETIC_INPUT_NOT_BROKER_VERIFIED']
    if session=='CLOSED': reasons.append('MARKET_CLOSED')
    if session=='EVENING_REVIEW': reasons.append('EXCHANGE_TRADEABILITY_UNVERIFIED')
    for flag in ('account_verified','unfilled_verified','daily_pnl_verified','quote_verified','exchange_verified','broker_reconciliation_verified'):
        if data.get(flag) is not True: reasons.append(flag.upper()+'_MISSING')
    if data.get('emergency_halt') is not False: reasons.append('EMERGENCY_HALT')
    cash=d(data.get('orderable_cash_krw',0)); used=d(data.get('committed_today_krw',0))
    if cash<0 or used<0: raise ValueError('INVALID_BUDGET')
    budget=(cash*(100-rates[0])/100).quantize(Decimal('1'),rounding=ROUND_DOWN)
    remaining=max(Decimal(0),budget-used)
    per_stock=(budget*rates[1]/100).quantize(Decimal('1'),rounding=ROUND_DOWN)
    evening_cap=(budget*rates[2]/100).quantize(Decimal('1'),rounding=ROUND_DOWN)
    pnl=d(data.get('verified_daily_pnl_krw',0))
    if pnl<=-20000: reasons.append('DAILY_LOSS_LIMIT_HIT_EXAMPLE')
    candidates=data.get('candidates',[])
    if not isinstance(candidates,list) or len(candidates)>10: raise ValueError('INVALID_CANDIDATES')
    seen=set(); plans=[]
    for item in candidates:
        code=item.get('ticker',''); price=d(item.get('limit_price_krw',0))
        if not isinstance(code,str) or len(code)!=6 or not code.isascii() or not code.isdigit() or code in seen:
            reasons.append('DUPLICATE_OR_BAD_TICKER');continue
        seen.add(code)
        if price<=0: reasons.append('INVALID_PRICE');continue
        if item.get('already_held') is not False or item.get('has_unfilled') is not False:
            reasons.append('HELD_OR_UNFILLED');continue
        cap=min(per_stock,remaining,evening_cap if session=='EVENING_REVIEW' else budget)
        # 0.2% cost + slippage buffer
        qty=int((cap/(price*Decimal('1.002'))).to_integral_value(rounding=ROUND_DOWN))
        if qty<1: reasons.append('INSUFFICIENT_BUDGET'); continue
        oid=hashlib.sha256(f"V5.7|{now.date()}|{session}|{code}|{qty}|{price}".encode()).hexdigest()[:20]
        plans.append({'ticker':code,'preview_qty':qty,'preview_cost_krw':int(qty*price),'intent_id':oid,'status':'PREVIEW_ONLY'})
        remaining-=qty*price*Decimal('1.002')
    return {'version':'5.7','mode':'SYNTHETIC_NO_ORDER','session':session,
            'reserve_pct':float(rates[0]),'stock_pct_of_trading_budget':float(rates[1]),
            'evening_pct_of_trading_budget':float(rates[2]),'synthetic_budget_krw':int(budget),
            'per_stock_cap_krw':int(per_stock),'plans':plans,'block_codes':sorted(set(reasons)),
            'gate':'DENY','broker_api_calls':0,'orders_sent':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',default='v5_7_synthetic.json')
    parser.add_argument('--output',default='v5_7_report.json')
    parser.add_argument('--reserve-pct',default='40')
    parser.add_argument('--stock-pct',default='30')
    parser.add_argument('--evening-pct',default='30')
    args=parser.parse_args()
    data=json.loads(Path(args.input).read_text(encoding='utf-8'))
    result=validate(data,args.reserve_pct,args.stock_pct,args.evening_pct)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"V5.7 GATE=DENY; session={result['session']}; synthetic_plans={len(result['plans'])}; orders_sent=0; broker_api_calls=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false")
if __name__=='__main__': main()
