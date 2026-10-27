#!/usr/bin/env python3
"""Pure read-only buying-power evidence verifier; never sends orders.

The caller may pass a broker-observed buying-power result in process memory only.
This module NEVER treats such a result as an approved executable order price.
"""
from __future__ import annotations
import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CODE = re.compile(r'^\d{6}$')

def integer(value):
    if isinstance(value, bool): return None
    try:
        v = Decimal(str(value))
        if not v.is_finite() or v < 0 or v != v.to_integral_value(): return None
        return int(v)
    except (InvalidOperation, TypeError, ValueError): return None

def examine(rows, *, now=None, reserve_pct=40, stock_pct=30, held=None, unfilled=None, price_attested=False,
            daily_loss_verified=False, baseline_verified=False):
    """No actual broker values are returned; sensitive amount stays memory-local."""
    now = now or dt.datetime.now(KST)
    if now.tzinfo is None: raise ValueError('AWARE_CLOCK_REQUIRED')
    if not isinstance(reserve_pct,int) or not 0 <= reserve_pct <= 100: raise ValueError('RESERVE_INVALID')
    if not isinstance(stock_pct,int) or not 0 <= stock_pct <= 100: raise ValueError('STOCK_PCT_INVALID')
    if not isinstance(rows,list) or len(rows)>25: raise ValueError('INPUT_INVALID')
    held = held if isinstance(held,set) else None
    unfilled = unfilled if isinstance(unfilled,set) else None
    seen=set(); decisions=[]
    for item in rows:
        if not isinstance(item,dict): raise ValueError('INPUT_INVALID')
        ticker=item.get('ticker')
        if not isinstance(ticker,str) or not CODE.fullmatch(ticker) or ticker in seen:
            raise ValueError('INVALID_OR_DUPLICATE_TICKER')
        seen.add(ticker)
        reasons=[]
        cash=integer(item.get('broker_orderable_cash'))
        qty=integer(item.get('broker_orderable_qty'))
        proposed_price=integer(item.get('query_price'))
        if held is None or unfilled is None: reasons.append('ACCOUNT_STATE_NOT_VERIFIED')
        elif ticker in held or ticker in unfilled: reasons.append('HELD_OR_UNFILLED')
        if cash is None or qty is None or proposed_price is None or proposed_price==0:
            reasons.append('BUYING_POWER_RESPONSE_INVALID')
        if not price_attested: reasons.append('QUERY_PRICE_NOT_CERTIFIED')
        if not daily_loss_verified or not baseline_verified: reasons.append('DAILY_LOSS_OR_BASELINE_NOT_VERIFIED')
        # Do not export price, buying power, or an orderable quantity.
        decisions.append({'ticker':ticker,'observation_valid':cash is not None and qty is not None,
                          'orderable_qty_approved':False,'block_codes':reasons + ['ORDER_TRANSMISSION_DISABLED']})
    return {'version':'5.19-buying-power-guard','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','source':'BROKER_OBSERVATION_REQUIRES_PRICE_ATTESTATION',
            'candidate_count':len(decisions),'checks':decisions,
            'reserve_pct':reserve_pct,'trading_pct':100-reserve_pct,
            'stock_pct_of_trading_budget':stock_pct,
            'broker_buying_power_verified':False,'actual_order_qty_verified':False,
            'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
