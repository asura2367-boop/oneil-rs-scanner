#!/usr/bin/env python3
"""V4.9 non-executable capital allocation preview; never calls a broker."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

class Blocked(ValueError):
    pass

def pct(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise Blocked('INVALID_PERCENT') from None
    if not d.is_finite() or not (Decimal('0') <= d <= Decimal('100')):
        raise Blocked('INVALID_PERCENT')
    return d

def nonnegative_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Blocked('INVALID_AMOUNT')
    return value

def allocate(orderable_cash, candidates, reserve_pct=40, per_stock_pct=30, *, verified=False):
    cash = nonnegative_int(orderable_cash)
    reserve, per_stock = pct(reserve_pct), pct(per_stock_pct)
    if not isinstance(candidates, list) or len(candidates) > 100:
        raise Blocked('INVALID_CANDIDATES')
    # This function is useful for synthetic unit tests, but an executable
    # recommendation must require independently validated broker data.
    reserve_krw = int((Decimal(cash)*reserve/100).to_integral_value(rounding=ROUND_FLOOR))
    budget = cash - reserve_krw
    stock_cap = int((Decimal(budget)*per_stock/100).to_integral_value(rounding=ROUND_FLOOR))
    remaining = budget
    seen = set()
    rows = []
    for c in candidates:
        if not isinstance(c,dict): raise Blocked('INVALID_CANDIDATE')
        ticker = c.get('ticker')
        if not isinstance(ticker,str) or len(ticker)!=6 or not ticker.isascii() or not ticker.isdigit() or ticker in seen:
            raise Blocked('INVALID_OR_DUPLICATE_TICKER')
        seen.add(ticker)
        price = nonnegative_int(c.get('price'))
        broker_qty = nonnegative_int(c.get('broker_max_qty'))
        if price == 0: raise Blocked('INVALID_PRICE')
        # Conservative buffer for fees, price movement; never treat as executable order.
        limit = min(stock_cap, remaining)
        qty = min(broker_qty, limit * 1000 // (price * 1002))  # 0.2% buffer
        amount = qty * price
        reserved_cost = (amount * 1002 + 999) // 1000
        remaining -= reserved_cost
        rows.append({'ticker': ticker, 'preview_qty': qty, 'preview_amount_krw': amount,
                     'status': 'PREVIEW_ONLY_NOT_VERIFIED' if not verified else 'PREVIEW_ONLY_NO_ORDER'})
    return {'reserve_pct': str(reserve), 'per_stock_pct_of_trading_budget': str(per_stock),
            'orderable_cash_krw': cash, 'reserve_krw': reserve_krw,
            'trading_budget_krw': budget, 'per_stock_max_krw': stock_cap,
            'unallocated_budget_krw': remaining, 'broker_verified': bool(verified),
            'rows': rows, 'orders_sent': 0, 'live_enabled': False, 'gate': 'DENY'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--reserve-pct',default='40')
    p.add_argument('--stock-pct',default='30')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('data/live/v4_9_budget_preview.json'))
    args=p.parse_args()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise Blocked('ORDER_SWITCH_NOT_OFF')
    data=json.loads(args.input.read_text(encoding='utf-8'))
    if data.get('source')!='SYNTHETIC_EXAMPLE':
        raise Blocked('UNVERIFIED_BROKER_INPUT')
    result=allocate(data['orderable_cash_krw'],data['candidates'],args.reserve_pct,args.stock_pct)
    # Don't write simulated account values to GitHub by default.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V4.9 BUDGET PREVIEW COMPLETE; source=SYNTHETIC_EXAMPLE')
    print('reserve_pct='+str(result['reserve_pct'])+'; trading_pct='+str(100-pct(args.reserve_pct)))
    print('per_stock_pct_of_trading_budget='+str(result['per_stock_pct_of_trading_budget']))
    print('broker_verified=false; gate=DENY; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')

if __name__=='__main__':
    try: main()
    except (Blocked,KeyError,ValueError,TypeError,FileNotFoundError,json.JSONDecodeError):
        print('V4.9 BLOCKED: INVALID_OR_UNVERIFIED_INPUT; orders_sent=0',flush=True)
        raise SystemExit(1)
