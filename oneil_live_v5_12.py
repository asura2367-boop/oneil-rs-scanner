#!/usr/bin/env python3
"""V5.12 account evidence inspector. Read-only, NEVER sends or authorizes orders.

Accepts explicit sanitized account snapshot input; an input file alone is NOT
independent broker verification. No broker credentials or network access.
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^[0-9]{6}$')
MAX_SNAPSHOT_AGE=120

def inspect(intents, account, now):
    if not isinstance(intents,dict) or intents.get('mode')!='LIVE_INTENTS_PREVIEW_ONLY' or intents.get('gate')!='DENY' or intents.get('orders_sent')!=0:
        raise ValueError('UNSAFE_INTENT_INPUT')
    rows=intents.get('intents')
    if not isinstance(rows,list) or len(rows)>30:
        raise ValueError('INVALID_INTENTS')
    report={'version':'5.12','mode':'ACCOUNT_EVIDENCE_DIAGNOSTIC_ONLY', 'generated_at_kst':now.isoformat(),
            'gate':'DENY','orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'candidate_count':len(rows),'snapshot_status':'NOT_PROVIDED',
            'account_values_used_to_authorize':False,'real_order_quantities':None,'blocking_reasons':[], 'candidate_checks':[]}
    reasons=report['blocking_reasons']
    if not isinstance(account,dict) or not account:
        reasons.append('BROKER_SNAPSHOT_MISSING')
        account={}
    else:
        mandatory=('generated_at_kst','holdings','unfilled_orders','orderable_cash_krw')
        if not all(k in account for k in mandatory):
            reasons.append('BROKER_SNAPSHOT_INCOMPLETE')
        try:
            ts=dt.datetime.fromisoformat(account['generated_at_kst'])
            if ts.tzinfo is None or ts>now or (now-ts).total_seconds()>MAX_SNAPSHOT_AGE:
                reasons.append('BROKER_SNAPSHOT_STALE_OR_INVALID')
            else:
                report['snapshot_status']='RECENT_UNATTESTED_INPUT'
        except (ValueError,TypeError,KeyError):
            reasons.append('BROKER_SNAPSHOT_STALE_OR_INVALID')
        # Structured checks do not prove source is broker or market session is valid.
        if type(account.get('orderable_cash_krw')) is not int or account.get('orderable_cash_krw',-1)<0:
            reasons.append('CASH_NOT_VALIDATED')
        for key in ('holdings','unfilled_orders'):
            val=account.get(key)
            if not isinstance(val,list) or len(val)>1000 or any(not isinstance(c,str) or CODE.fullmatch(c) is None for c in val) or len(val)!=len(set(val)):
                reasons.append(key.upper()+'_NOT_VALIDATED')
    held=account.get('holdings') if isinstance(account.get('holdings'),list) else []
    open_orders=account.get('unfilled_orders') if isinstance(account.get('unfilled_orders'),list) else []
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('status')!='REVIEW_ONLY_UNPRICED' or row.get('requested_side')!='BUY' or row.get('quantity') is not None or row.get('price_krw') is not None:
            raise ValueError('INTENT_NOT_UNPRICED')
        code=row.get('ticker')
        if not isinstance(code,str) or not CODE.fullmatch(code) or code in seen:
            raise ValueError('DUPLICATE_OR_INVALID_TICKER')
        seen.add(code)
        check=[]
        if code in held: check.append('ALREADY_HELD')
        if code in open_orders: check.append('UNFILLED_ORDER')
        if not check: check.append('ACCOUNT_DATA_NOT_AUTHORIZING')
        report['candidate_checks'].append({'ticker':code,'intent_id':row.get('intent_id'),'checks':check,'quantity':None,'price':None})
    reasons.extend(['BROKER_ORIGIN_NOT_ATTESTED','REALTIME_EXCHANGE_QUOTE_NOT_VERIFIED','DAILY_PNL_NOT_VERIFIED','ORDER_EXECUTION_NOT_IMPLEMENTED'])
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--intents',default='v5_11_review.json')
    p.add_argument('--snapshot',default='v5_12_account_snapshot.json')
    p.add_argument('--output',default='v5_12_review.json')
    a=p.parse_args()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise SystemExit('LIVE_FLAGS_NOT_DISABLED')
    obj=json.loads(Path(a.intents).read_text(encoding='utf-8'))
    f=Path(a.snapshot)
    account=json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {}
    out=inspect(obj,account,dt.datetime.now(KST))
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'V5.12 GATE=DENY; candidates={out["candidate_count"]}; snapshot={out["snapshot_status"]}; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__': main()
