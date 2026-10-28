#!/usr/bin/env python3
"""No-order evidence audit for existing V5.19 outputs. Never authorizes trades."""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^[0-9]{6}$')

def timestamp(doc):
    if not isinstance(doc,dict): return None
    raw=doc.get('generated_at_kst') or doc.get('as_of_kst') or doc.get('timestamp_kst')
    try:
        t=dt.datetime.fromisoformat(raw)
        return t.astimezone(KST) if t.tzinfo and t.utcoffset() is not None else None
    except (TypeError,ValueError,OverflowError): return None

def recent(doc,now,max_seconds=300):
    t=timestamp(doc)
    return t is not None and 0 <= (now-t).total_seconds() <= max_seconds

def audit(candidate,account,buying,quote,risk,history,now=None,env=None):
    env=os.environ if env is None else env
    if env.get('LIVE_ENABLED','false').lower()!='false' or env.get('KIS_ALLOW_ORDER','false').lower()!='false':
        raise ValueError('LIVE_SWITCH_NOT_DISABLED')
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise ValueError('CLOCK_NAIVE')
    codes=[]; blocks=[]
    rows=candidate.get('intents') if isinstance(candidate,dict) else None
    if (recent(candidate,now) and candidate.get('mode')=='LIVE_INTENTS_PREVIEW_ONLY' and candidate.get('gate')=='DENY'
        and type(candidate.get('orders_sent')) is int and candidate['orders_sent']==0
        and isinstance(rows,list) and len(rows)<=25 and all(isinstance(x,dict) for x in rows)):
        codes=[r.get('ticker') for r in rows]
        if not all(isinstance(c,str) and CODE.fullmatch(c) for c in codes) or len(set(codes))!=len(codes) or any(r.get('status')!='REVIEW_ONLY_UNPRICED' for r in rows):codes=[]
    if not codes:blocks.append('CANDIDATE_NOT_VERIFIED')
    aok=recent(account,now) and account.get('broker_status')=='READ_ONLY_OBSERVED' and account.get('gate')=='DENY' and account.get('orders_sent')==0
    bok=recent(buying,now) and buying.get('status')=='OBSERVATION_ATTEMPTED' and buying.get('gate')=='DENY' and buying.get('orders_sent')==0
    if not aok:blocks.append('ACCOUNT_NOT_VERIFIED')
    if not bok:blocks.append('INDICATIVE_BUYING_POWER_NOT_OBSERVED')
    # V5.18 reports are diagnostic, even if plausible exchange frames arrived.
    quote_observed=bool(recent(quote,now) and quote.get('version')=='5.18' and quote.get('orders_sent')==0)
    if not quote_observed:blocks.append('QUOTE_DIAGNOSTIC_MISSING_OR_STALE')
    blocks.append('EXECUTABLE_QUOTE_NOT_ATTESTED')
    # V4.7 or V4.8 audits do not establish same-run authenticated whole-account P&L here.
    pnl_evidence_present=bool(recent(risk,now) and risk.get('orders_sent')==0)
    if not pnl_evidence_present:blocks.append('DAILY_PNL_EVIDENCE_MISSING_OR_STALE')
    blocks.append('DAILY_LOSS_INDEPENDENT_RECONCILIATION_REQUIRED')
    # No broker order ID + complete execution/partial fill/cancel history => no recovery approval.
    history_present=bool(recent(history,now) and history.get('source')=='BROKER_ORDER_HISTORY' and history.get('verified') is True)
    if not history_present:blocks.append('BROKER_ORDER_HISTORY_MISSING_OR_UNVERIFIED')
    blocks.append('AUTOMATIC_RETRY_AND_ORDER_TRANSMISSION_DISABLED')
    return {'version':'5.19-pretrade-audit','generated_at_kst':now.isoformat(timespec='seconds'),
            'gate':'DENY','candidate_count':len(codes),'account_observed':bool(aok),
            'indicative_buying_power_observed':bool(bok),'quote_diagnostic_observed':quote_observed,
            'daily_pnl_input_observed':pnl_evidence_present,'order_history_observed':history_present,
            'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
            'approved_order_quantity_count':0,'block_codes':blocks,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,'orders_sent':0,'broker_order_api_calls':0}

def load(path):
    try:
        x=json.loads(Path(path).read_text(encoding='utf8'))
        return x if isinstance(x,dict) else None
    except (OSError,ValueError,UnicodeError):return None

def main():
    p=argparse.ArgumentParser()
    for name,default in [('candidate','v5_11_review.json'),('account','v5_13_review.json'),('buying','v5_19_indicative_power.json'),('quote','v5_18_report.json'),('risk','data/live/v4_8_audit.json'),('history','v5_19_order_history.json'),('output','v5_19_pretrade_audit.json')]:p.add_argument('--'+name,default=default)
    a=p.parse_args()
    out=audit(*(load(getattr(a,n)) for n in ('candidate','account','buying','quote','risk','history')))
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print('V5.19 PRETRADE GATE=DENY; candidates='+str(out['candidate_count'])+'; blocks='+','.join(out['block_codes'])+'; orders_sent=0')
if __name__=='__main__':main()
