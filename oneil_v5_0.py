#!/usr/bin/env python3
"""Oneil V5.0 safe preview + post-close reconciliation (NEVER PLACES ORDERS)."""
import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

def fail(code):
    raise ValueError(code)

def percent(value):
    try: v = Decimal(str(value))
    except Exception: fail('INVALID_PERCENT')
    if not v.is_finite() or v < 0 or v > 100: fail('INVALID_PERCENT')
    return v

def session(now, venue, eligible=False, verified=False):
    """Calendar and broker quote validation remain external fail-closed requirements."""
    if now.weekday() >= 5: return 'BLOCKED_NON_TRADING_DAY'
    t = now.time()
    if venue == 'KRX' and dt.time(9) <= t < dt.time(15,30): return 'REGULAR_PREVIEW'
    if venue == 'KRX' and dt.time(16) <= t < dt.time(20):
        return 'EVENING_PREVIEW' if eligible and verified else 'BLOCKED_UNVERIFIED_VENUE'
    if venue == 'NXT' and dt.time(15,40) <= t < dt.time(20):
        return 'EVENING_PREVIEW' if eligible and verified else 'BLOCKED_UNVERIFIED_VENUE'
    return 'BLOCKED_MARKET_CLOSED'

def budget(cash, reserve=40, stock=30, evening=30):
    if type(cash) is not int or cash < 0: fail('UNVERIFIED_CASH')
    r,s,e = (percent(x) for x in (reserve,stock,evening))
    retained=int((Decimal(cash)*r/100).to_integral_value(rounding=ROUND_FLOOR))
    trading=cash-retained
    stock_cap=int((Decimal(trading)*s/100).to_integral_value(rounding=ROUND_FLOOR))
    evening_cap=int((Decimal(trading)*e/100).to_integral_value(rounding=ROUND_FLOOR))
    return {'reserve_pct':str(r),'trading_pct':str(100-r),'stock_pct':str(s),
            'evening_pct':str(e),'reserve_krw':retained,'trading_budget_krw':trading,
            'stock_cap_krw':stock_cap,'evening_buy_cap_krw':evening_cap}

def next_workday(day):
    d=day+dt.timedelta(days=1)
    while d.weekday()>=5: d+=dt.timedelta(days=1)
    return d.isoformat()  # Exchange holidays NOT automatically verified

def reconcile(data):
    """Only verified evidence may produce a numeric daily P/L. No guesses."""
    required=('opening_equity','closing_equity','net_external_cashflow','fees_and_taxes')
    evidence=data.get('evidence') or {}
    ok=(data.get('source') == 'BROKER_VERIFIED' and data.get('baseline_captured_before_open') is True
        and data.get('all_trades_reconciled') is True and data.get('cashflows_reconciled') is True
        and data.get('all_positions_valued') is True and all(type(evidence.get(k)) is int for k in required))
    pnl=None
    if ok:
        if any(evidence[k]<0 for k in ('opening_equity','closing_equity','fees_and_taxes')):
            ok=False
        else:
            # Closing equity already NET of costs, so do not subtract fees twice.
            pnl=evidence['closing_equity']-evidence['opening_equity']-evidence['net_external_cashflow']
    return {'daily_pnl_verified':bool(ok),'daily_pnl_krw':pnl if ok else None,
            'loss_limit_krw':-20000,'daily_loss_limit_breached':(pnl<=-20000) if ok else None,
            'reason':'VERIFIED' if ok else 'INSUFFICIENT_VERIFIED_EVIDENCE'}

def report(data, now, reserve=40,stock=30,evening=30,gap=1):
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        fail('LIVE_SWITCH_MUST_BE_FALSE')
    p=budget(data.get('sample_cash_krw',0),reserve,stock,evening)
    gap=percent(gap)
    outcome=reconcile(data)
    result={'mode':'NO_ORDER_PREVIEW','asof_kst':now.isoformat(),
            'date':now.date().isoformat(),'next_business_day_tentative':next_workday(now.date()),
            'next_date_exchange_holiday_verified':False,'capital_example_only':p,
            'daily_settlement':outcome,'evening_gap_limit_pct':str(gap),
            'orders_sent':0,'live_enabled':False,'kis_allow_order':False,'gate':'DENY',
            'night_report_status':'REVIEW_ONLY_NOT_TRADE_SIGNAL','next_day_strategy':[]}
    for x in data.get('candidates',[]):
        ticker=x.get('ticker')
        if not isinstance(ticker,str) or len(ticker)!=6 or not ticker.isascii() or not ticker.isdigit():fail('BAD_TICKER')
        venue=x.get('venue','KRX')
        status=session(now,venue,x.get('venue_eligible') is True,x.get('venue_data_verified') is True)
        if status=='EVENING_PREVIEW':
            close=x.get('regular_close'); price=x.get('quote_price')
            if type(close) is not int or type(price) is not int or close<=0 or price<=0:
                status='BLOCKED_UNVERIFIED_PRICES'
            elif Decimal(price) > Decimal(close)*(100+gap)/100:
                status='BLOCKED_EVENING_PRICE_GAP'
            elif not (x.get('spread_verified') is True and x.get('unfilled_verified') is True
                      and x.get('buying_power_verified') is True):
                status='BLOCKED_EVENING_LIQUIDITY_OR_ORDERS'
        result['next_day_strategy'].append({'ticker':ticker,'venue':venue,'current_session_check':status,
           'next_day_action':'RECHECK_RS_FLOW_CHANNEL_QUOTE_HOLDINGS_AND_CASH_BEFORE_ANY_ORDER',
           'risk_gate':'DENY'})
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output',default='v5_0_report.json',type=Path)
    for name,default in [('reserve-pct',40),('stock-pct',30),('evening-pct',30),('gap-pct',1)]:
        p.add_argument('--'+name,default=str(default))
    a=p.parse_args()
    data=json.loads(a.input.read_text())
    if data.get('source') not in ('SYNTHETIC_EXAMPLE','BROKER_VERIFIED'):fail('UNKNOWN_SOURCE')
    # LIVE account evidence cannot be provided via manually edited JSON.
    if data.get('source')=='BROKER_VERIFIED':fail('BROKER_DATA_NEEDS_SIGNED_CONNECTOR')
    out=report(data,dt.datetime.now(KST),a.reserve_pct,a.stock_pct,a.evening_pct,a.gap_pct)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print('V5.0 SAFE REPORT COMPLETE; DAILY_PNL_VERIFIED=false; NEXT_DAY_STRATEGY=REVIEW_ONLY; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':
    try: main()
    except Exception:
        print('V5.0 BLOCKED: INVALID_OR_UNVERIFIED_EVIDENCE; orders_sent=0',file=sys.stderr)
        sys.exit(1)
