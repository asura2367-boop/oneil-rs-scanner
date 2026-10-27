#!/usr/bin/env python3
"""V5.11: LIVE candidate intent bridge, non-authorizing. No broker/order API imports."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')
TICKER=re.compile(r'^[0-9]{6}$')

def assemble(candidate, broker, now):
    if not isinstance(candidate,dict) or candidate.get('status')!='DENY' or candidate.get('source')!='V4B_CSV_UNVERIFIED' or candidate.get('orders_sent')!=0:
        raise ValueError('CANDIDATE_SOURCE_INVALID')
    rows=candidate.get('review_candidates')
    if not isinstance(rows,list) or len(rows)>100:
        raise ValueError('CANDIDATE_LIST_INVALID')
    report={'version':'5.11','mode':'LIVE_INTENTS_PREVIEW_ONLY','generated_at_kst':now.isoformat(),
            'gate':'DENY','orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
            'intents':[], 'blocking_reasons':['ORDER_TRANSMISSION_NOT_IMPLEMENTED','BROKER_CASH_NOT_VERIFIED','LIVE_QUOTES_NOT_VERIFIED','DAILY_PNL_NOT_VERIFIED','UNFILLED_NOT_FRESHLY_VERIFIED'],
            'paper_state_used':False,'actual_orderable_qty':None}
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('flag')!='REVIEW_ONLY_NOT_LIVE_QUOTE':
            raise ValueError('CANDIDATE_ROW_INVALID')
        code=row.get('ticker'); ds=row.get('date')
        if not isinstance(code,str) or not TICKER.fullmatch(code) or code in seen:
            raise ValueError('TICKER_INVALID_OR_DUPLICATE')
        try: day=dt.date.fromisoformat(ds)
        except (ValueError,TypeError): raise ValueError('SIGNAL_DATE_INVALID') from None
        if day > now.date() or (now.date()-day).days > 4:
            raise ValueError('SIGNAL_STALE_OR_FUTURE')
        seen.add(code)
        digest=hashlib.sha256(f'V5.11|{day.isoformat()}|{code}|BUY'.encode()).hexdigest()[:20]
        report['intents'].append({'intent_id':digest,'ticker':code,'signal_date':day.isoformat(),
            'flow_category':row.get('flow_category'),'rs':row.get('rs'),
            'status':'REVIEW_ONLY_UNPRICED','requested_side':'BUY',
            'quantity':None,'price_krw':None,'orderable_cash_krw':None,'broker_verified':False})
    check=(broker.get('broker_report_checks') if isinstance(broker,dict) else None) or {}
    report['broker_read_status']=broker.get('broker_read_status','NOT_REQUESTED') if isinstance(broker,dict) else 'NOT_REQUESTED'
    report['broker_report_checks']={k:check.get(k,'NOT_VERIFIED') for k in ('preflight','reconciliation','gate')}
    if report['broker_report_checks']['preflight']=='NO_INTENTS_DENY':
        report['blocking_reasons'].append('PREFLIGHT_HAS_NO_LIVE_INTENTS')
    if not report['intents']:
        report['blocking_reasons'].append('NO_CANDIDATES')
    return report

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--candidates',default='v5_9_candidates_review.json')
    ap.add_argument('--broker',default='v5_8_safe_report.json')
    ap.add_argument('--output',default='v5_11_review.json')
    args=ap.parse_args()
    if any(os.getenv(x,'false').lower()!='false' for x in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise SystemExit('LIVE_FLAGS_NOT_DISABLED')
    candidate=json.loads(Path(args.candidates).read_text(encoding='utf-8'))
    path=Path(args.broker)
    broker=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    out=assemble(candidate,broker,dt.datetime.now(KST))
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'V5.11 GATE=DENY; live_review_intents={len(out["intents"])}; priced=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()
