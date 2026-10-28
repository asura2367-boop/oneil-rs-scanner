#!/usr/bin/env python3
"""V5.19 independent evidence-integrity diagnostics. Strictly no API or orders."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

def stamp(s):
    try:
        v = dt.datetime.fromisoformat(s)
        return v.astimezone(KST) if v.tzinfo and v.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None

def review(baseline, current, flow, history, *, now=None, env=None):
    now = now or dt.datetime.now(KST)
    env = os.environ if env is None else env
    if now.tzinfo is None or any(env.get(k,'false').lower()!='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise ValueError('SAFETY_FLAGS_OR_CLOCK_INVALID')
    codes=[]
    def require(cond, code):
        if not cond: codes.append(code)
    def valid_equity(x, max_age):
        if not isinstance(x,dict): return False
        t=stamp(x.get('as_of_kst'))
        return (x.get('source')=='BROKER_EQUITY_OBSERVATION' and x.get('account_scope')=='WHOLE_ACCOUNT'
            and x.get('complete') is True and type(x.get('equity_krw')) is int
            and x['equity_krw']>=0 and t is not None and t.date()==now.date()
            and 0 <= (now-t).total_seconds()<=max_age)
    base_ok=valid_equity(baseline,24*3600) and stamp(baseline['as_of_kst']).time()<=dt.time(9,0)
    current_ok=valid_equity(current,300) and (not base_ok or stamp(current['as_of_kst'])>stamp(baseline['as_of_kst']))
    require(base_ok,'PREOPEN_EQUITY_BASELINE_MISSING_OR_INVALID')
    require(current_ok,'CURRENT_EQUITY_MISSING_OR_STALE')
    flow_ok=(isinstance(flow,dict) and flow.get('source')=='RECONCILED_EXTERNAL_CASHFLOWS'
        and flow.get('complete') is True and flow.get('independently_verified') is True
        and type(flow.get('net_external_cashflow_krw')) is int and stamp(flow.get('as_of_kst')) is not None
        and 0 <= (now-stamp(flow['as_of_kst'])).total_seconds()<=300)
    require(flow_ok,'EXTERNAL_CASHFLOWS_NOT_RECONCILED')
    history_ok=False
    if isinstance(history,dict) and history.get('source')=='KIS_COMPLETE_ORDER_EXECUTION_HISTORY':
        t=stamp(history.get('as_of_kst'))
        rows=history.get('orders')
        # A record without broker-side pagination/total reconciliation is unverified.
        complete=(history.get('all_pages_read') is True and history.get('cursor_terminated') is True
            and history.get('broker_totals_matched') is True and history.get('query_covers_whole_day') is True)
        if complete and t and 0 <= (now-t).total_seconds()<=300 and isinstance(rows,list) and len(rows)<=10000:
            ids=[];good=True
            for item in rows:
                if not isinstance(item,dict): good=False;break
                ident=item.get('broker_order_id'); qty=item.get('ordered_qty'); done=item.get('filled_qty')
                if (not isinstance(ident,str) or not ident or type(qty) is not int or type(done) is not int
                    or qty<=0 or not 0<=done<=qty or item.get('state') not in
                    ('ACCEPTED','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED')):
                    good=False;break
                ids.append(ident)
            history_ok=good and len(set(ids))==len(ids)
    require(history_ok,'BROKER_ORDER_HISTORY_INCOMPLETE_OR_INVALID')
    # Even valid-shaped inputs are not independently authenticated by this module.
    codes.extend(['BROKER_DATA_PROVENANCE_NOT_CRYPTOGRAPHICALLY_ATTESTED',
                  'EXECUTABLE_QUOTE_UNATTESTED','LIVE_ORDER_TRANSMISSION_DISABLED'])
    loss_reached=None
    if base_ok and current_ok and flow_ok:
        pnl=current['equity_krw']-baseline['equity_krw']-flow['net_external_cashflow_krw']
        loss_reached=pnl<=-20000
        if loss_reached: codes.append('DAILY_LOSS_LIMIT_REACHED')
    return {'version':'5.19-evidence-integrity','generated_at_kst':now.isoformat(timespec='seconds'),
        'gate':'DENY','baseline_schema_valid':bool(base_ok),'current_schema_valid':bool(current_ok),
        'cashflow_schema_valid':bool(flow_ok),'history_schema_valid':bool(history_ok),
        'daily_loss_limit_reached':loss_reached,'blocking_reasons':codes,
        'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def load(path):
    try:
        v=json.loads(Path(path).read_text(encoding='utf8'))
        return v if isinstance(v,dict) else None
    except (OSError,UnicodeError,ValueError):return None

def main():
    p=argparse.ArgumentParser()
    for key in ('baseline','current','flow','history'):p.add_argument('--'+key)
    p.add_argument('--output',default='v5_19_evidence_integrity.json')
    a=p.parse_args()
    result=review(*(load(getattr(a,k)) if getattr(a,k) else None for k in ('baseline','current','flow','history')))
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V519_EVIDENCE_INTEGRITY=DENY; loss_evidence='+str(result['baseline_schema_valid'] and result['current_schema_valid'] and result['cashflow_schema_valid']).lower()+'; history_schema='+str(result['history_schema_valid']).lower()+'; orders_sent=0')
if __name__=='__main__':main()
