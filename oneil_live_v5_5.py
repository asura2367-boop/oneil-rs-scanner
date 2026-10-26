#!/usr/bin/env python3
"""Read-only evidence merger. ALWAYS DENIES actual broker order transmission."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from oneil_live_v5_4 import gate, Denied

KST=ZoneInfo('Asia/Seoul')
REQUIRED=('broker_cash_verified','positions_verified','unfilled_verified','daily_loss_verified',
          'quotes_fresh_verified','exchange_tradability_verified','broker_history_reconciled')

def safe_read(path):
    if not path.is_file(): return None
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data,dict) else None
    except (OSError, ValueError, UnicodeError): return None

def validated_timestamp(value,now,max_age_seconds=90):
    if not isinstance(value,str): return False
    try: t=dt.datetime.fromisoformat(value)
    except ValueError: return False
    if t.tzinfo is None: return False
    delta=(now-t.astimezone(now.tzinfo)).total_seconds()
    return 0<=delta<=max_age_seconds

def inspect(preflight,reconcile,risk,now):
    """Evidence audit; source flags never elevated by inference."""
    problems=[]
    for label,doc in [('preflight',preflight),('reconciliation',reconcile),('risk',risk)]:
        if not doc or not validated_timestamp(doc.get('collected_at'),now):
            problems.append(label+'_MISSING_OR_STALE')
    if isinstance(preflight,dict):
        if preflight.get('orders_sent')!=0 or preflight.get('live_enabled') is not False:
            problems.append('PREFLIGHT_UNSAFE')
    if isinstance(reconcile,dict):
        if reconcile.get('orders_sent')!=0 or reconcile.get('requires_manual_review') is not False:
            problems.append('RECONCILIATION_UNSAFE')
    if isinstance(risk,dict):
        if risk.get('daily_loss_verified') is not True:
            problems.append('DAILY_LOSS_NOT_VERIFIED')
    # Even genuine reports do not demonstrate exchange-specific executable quote freshness.
    problems.append('EXCHANGE_AND_QUOTE_NOT_CERTIFIED')
    return sorted(set(problems))

def evaluate(preflight,reconcile,risk,now,reserve,stock,evening):
    problems=inspect(preflight,reconcile,risk,now)
    # Construct no 'true' evidence flag, unless its precise source can prove it;
    # currently it cannot. gate() is intentionally always DENY.
    model={'schema_version':1,'as_of':now.strftime('%Y-%m-%d'),
           **{field:False for field in REQUIRED},'emergency_halt':True,
           'daily_loss_krw':0,'daily_loss_limit_krw':20000,'buying_power_krw':0,
           'today_reserved_and_filled_buy_krw':0,'evening_spent_and_reserved_krw':0,
           'candidates':[]}
    try:
        result=gate(model,now,reserve,stock,evening)
        # No result can authorize orders, defensively fail if invariant changes.
        if result.get('actual_broker_trade_authorization') is True: raise RuntimeError('UNSAFE_GATE')
    except Denied:
        pass
    return {'version':'5.5','status':'DENY','inspection_issues':problems,
            'reserve_pct':str(reserve),'stock_pct_of_trading_budget':str(stock),
            'evening_pct_of_trading_budget':str(evening),
            'source_status':'READ_ONLY_EVIDENCE_UNVERIFIED',
            'broker_order_api_calls':0,'orders_sent':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'actual_broker_trade_authorization':False}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--preflight',default='data/live/v4_5_preflight.json')
    ap.add_argument('--reconciliation',default='data/live/v4_5_reconcile.json')
    ap.add_argument('--risk',default='data/live/v4_7_risk_report.json')
    ap.add_argument('--report',default='v5_5_report.json')
    ap.add_argument('--reserve-pct',default='40')
    ap.add_argument('--stock-pct',default='30')
    ap.add_argument('--evening-pct',default='30')
    args=ap.parse_args()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise SystemExit('HARD_BLOCK: LIVE flags must remain false')
    now=dt.datetime.now(KST)
    report=evaluate(safe_read(Path(args.preflight)),safe_read(Path(args.reconciliation)),
                    safe_read(Path(args.risk)),now,args.reserve_pct,args.stock_pct,args.evening_pct)
    Path(args.report).write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('V5.5 GATE=DENY; evidence_issues='+','.join(report['inspection_issues'])+
          '; broker_order_api_calls=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()
