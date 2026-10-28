#!/usr/bin/env python3
"""Read-only account cash bridge: never logs money, never places orders.

KIS inquiry responses are observations, NOT independently certified account equity.
All output always DENY. Raw monetary values are used only in memory.
"""
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
TICKER = re.compile(r'^\d{6}$')

class EvidenceBlocked(ValueError): pass

def exact_krw(value):
    if isinstance(value, bool) or not isinstance(value, str) or not re.fullmatch(r'\d+', value):
        raise EvidenceBlocked('AMOUNT_SCHEMA_INVALID')
    v=int(value)
    if v>10**15:raise EvidenceBlocked('AMOUNT_TOO_LARGE')
    return v

def read_balance_cash(body):
    """Return observed cash only. Missing keys MUST NOT mean 0.

    These fields are KIS account observations, not confirmed buying power.
    """
    if not isinstance(body, dict) or str(body.get('rt_cd'))!='0':
        raise EvidenceBlocked('BALANCE_RESPONSE_INVALID')
    out=body.get('output2')
    if not isinstance(out,list) or len(out)!=1 or not isinstance(out[0],dict):
        raise EvidenceBlocked('BALANCE_SUMMARY_MISSING')
    return exact_krw(out[0].get('dnca_tot_amt'))

def read_orderable(body):
    if not isinstance(body,dict) or str(body.get('rt_cd'))!='0':
        raise EvidenceBlocked('POWER_RESPONSE_INVALID')
    out=body.get('output')
    if not isinstance(out,dict):raise EvidenceBlocked('POWER_OUTPUT_MISSING')
    return min(exact_krw(out.get('nrcvb_buy_amt')),exact_krw(out.get('ord_psbl_cash')))

def assess_readonly(balance_body, power_body, ticker, price_krw, *,
                    held=None, unfilled=None, now=None, env=None):
    env=os.environ if env is None else env
    if env.get('LIVE_ENABLED','false').lower()!='false' or env.get('KIS_ALLOW_ORDER','false').lower()!='false':
        raise EvidenceBlocked('ORDER_FLAGS_MUST_BE_OFF')
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise EvidenceBlocked('CLOCK_INVALID')
    status='BLOCKED'; reasons=[]
    if not isinstance(ticker,str) or not TICKER.fullmatch(ticker):raise EvidenceBlocked('TICKER_INVALID')
    if type(price_krw) is not int or price_krw<=0:raise EvidenceBlocked('REFERENCE_PRICE_INVALID')
    observed=False
    qty=None
    if held is None or unfilled is None or not isinstance(held,set) or not isinstance(unfilled,set):
        reasons.append('ACCOUNT_POSITIONS_UNVERIFIED')
    elif ticker in held or ticker in unfilled:
        reasons.append('ALREADY_HELD_OR_UNFILLED')
    try:
        cash=read_balance_cash(balance_body)
        orderable=read_orderable(power_body)
        observed=True
        # This is only an indicative calculation based on reference price.
        # Never implies availability of an executable exchange quote.
        budget=cash*60//100
        per_stock=budget*30//100
        qty=min(orderable,budget,per_stock)//price_krw
        status='READ_ONLY_CASH_OBSERVED_UNATTESTED'
    except EvidenceBlocked:
        reasons.append('CASH_OR_BUYING_POWER_NOT_VERIFIED')
    reasons+=['EXCHANGE_EXECUTABLE_PRICE_UNATTESTED',
              'DAILY_LOSS_NOT_INDEPENDENTLY_VERIFIED',
              'BROKER_ORDER_HISTORY_NOT_RECONCILED',
              'DURABLE_LEDGER_NOT_VERIFIED',
              'USER_APPROVAL_REQUIRED','ORDER_TRANSMISSION_DISABLED']
    # Qty, cash and reference price intentionally not exported.
    return {'version':'5.19-cash-bridge','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','ticker':ticker,'observation_status':status,
            'cash_and_buying_power_observed':observed,
            'cash_basis':'CURRENT_BROKER_OBSERVED_CASH_NOT_FIXED_CAPITAL',
            'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
            'quantity_authorized':False,'issues':reasons,
            'orders_sent':0,'broker_order_api_calls':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def main():
    # No live API requests here: use only provider-injected in-process data.
    out=assess_readonly(None,None,'000000',1)
    Path('v5_19_cash_bridge_diagnostic.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('CASH_BRIDGE=SAFE_DENY; no sensitive amounts; orders_sent=0')
if __name__=='__main__':main()
