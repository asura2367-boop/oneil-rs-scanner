#!/usr/bin/env python3
"""Conservative comparison of two read-only KIS diagnostics. NEVER sends orders.

Account observation and buying-power observation are not broker attestations:
if timestamps differ too much, a row is missing, or results conflict, fail closed.
Do not write account identifiers, balances, prices, quantities, or raw responses.
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CODE = re.compile(r'^\d{6}$')

class ReconcileError(ValueError):
    pass

def _stamp(report):
    if not isinstance(report, dict): return None
    raw = report.get('as_of_kst') or report.get('generated_at_kst')
    if not isinstance(raw, str): return None
    try:
        t = dt.datetime.fromisoformat(raw)
        return t.astimezone(KST) if t.tzinfo is not None and t.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None

def _codes(rows, key):
    if not isinstance(rows, list) or len(rows) > 25: return None
    codes = []
    for row in rows:
        if not isinstance(row,dict): return None
        code = row.get(key)
        if not isinstance(code,str) or not CODE.fullmatch(code) or code in codes: return None
        codes.append(code)
    return codes

def reconcile(intents, account, buying, now=None):
    if os.getenv('LIVE_ENABLED','false').lower() != 'false' or os.getenv('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise ReconcileError('LIVE_FLAGS_NOT_DISABLED')
    now = dt.datetime.now(KST) if now is None else now
    if now.tzinfo is None: raise ReconcileError('AWARE_CLOCK_REQUIRED')
    valid_source = (isinstance(intents,dict) and intents.get('mode')=='LIVE_INTENTS_PREVIEW_ONLY'
                    and intents.get('gate')=='DENY' and intents.get('orders_sent')==0)
    rows = intents.get('intents') if valid_source else None
    source_codes = _codes(rows, 'ticker')
    if source_codes is None or any(r.get('status')!='REVIEW_ONLY_UNPRICED' or r.get('quantity') is not None or r.get('price_krw') is not None for r in rows):
        source_codes=None
    arows = account.get('checks') if isinstance(account,dict) else None
    brows = buying.get('checks') if isinstance(buying,dict) else None
    account_codes = _codes(arows,'ticker')
    buying_codes = _codes(brows,'ticker')
    times = [_stamp(x) for x in (intents,account,buying)]
    fresh = all(t is not None and 0 <= (now-t).total_seconds() <= 300 for t in times)
    coherent = (fresh and max(times)-min(times) <= dt.timedelta(seconds=120)) if fresh else False
    complete = (source_codes is not None and account_codes is not None and buying_codes is not None
                and set(source_codes)==set(account_codes)==set(buying_codes))
    account_valid = bool(isinstance(account,dict) and account.get('version')=='5.13'
                         and account.get('gate')=='DENY' and account.get('orders_sent')==0
                         and account.get('broker_status')=='READ_ONLY_OBSERVED')
    buying_valid = bool(isinstance(buying,dict) and buying.get('version')=='5.19-indicative-power'
                        and buying.get('gate')=='DENY' and buying.get('orders_sent')==0
                        and buying.get('status')=='OBSERVATION_ATTEMPTED'
                        and buying.get('broker_buying_power_verified') is False)
    ac = {x['ticker']:x for x in arows} if account_codes is not None else {}
    bc = {x['ticker']:x for x in brows} if buying_codes is not None else {}
    checks=[]
    for ticker in source_codes or []:
        ar=ac.get(ticker,{})
        br=bc.get(ticker,{})
        state='BLOCKED_INCOMPLETE_OR_STALE'
        if coherent and complete and account_valid and buying_valid:
            held,unfilled=ar.get('held'),ar.get('unfilled')
            bp=br.get('status')
            if held not in ('YES','NO') or unfilled not in ('YES','NO'):
                state='BLOCKED_ACCOUNT_STATE_UNKNOWN'
            elif held=='YES' or unfilled=='YES':
                if (held=='YES' and bp=='ALREADY_HELD') or (unfilled=='YES' and bp in ('UNFILLED_ORDER','ALREADY_HELD')):
                    state='BLOCKED_EXISTING_POSITION_OR_PENDING'
                else:
                    state='BLOCKED_BROKER_OBSERVATION_CONFLICT'
            elif bp in ('ALREADY_HELD','UNFILLED_ORDER'):
                state='BLOCKED_BROKER_OBSERVATION_CONFLICT'
            elif bp=='INDICATIVE_POWER_OBSERVED_UNATTESTED':
                state='CONSISTENT_READ_ONLY_NOT_AUTHORIZED'
            elif bp=='INDICATIVE_NO_BUYING_POWER':
                state='BLOCKED_NO_INDICATIVE_POWER'
            else:
                state='BLOCKED_INDICATIVE_READ_FAILED'
        checks.append({'ticker':ticker,'status':state,'approved':False})
    blocks=[]
    if not valid_source or source_codes is None: blocks.append('INTENTS_INVALID')
    if not coherent: blocks.append('TIMESTAMPS_STALE_OR_INCONSISTENT')
    if not complete: blocks.append('CANDIDATE_SET_MISMATCH')
    if not account_valid: blocks.append('ACCOUNT_NOT_OBSERVED')
    if not buying_valid: blocks.append('BUYING_POWER_NOT_OBSERVED')
    blocks += ['ACCOUNT_EQUITY_AND_DAILY_LOSS_UNVERIFIED','EXECUTABLE_QUOTE_UNATTESTED',
               'BROKER_ORDER_HISTORY_NOT_RECONCILED','ORDER_TRANSMISSION_DISABLED']
    return {'version':'5.19-kis-reconcile','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','mode':'READ_ONLY_DIAGNOSTIC','candidate_count':len(source_codes or []),
            'time_coherent':coherent,'candidate_sets_equal':complete,
            'checks':checks,'blocking_reasons':blocks,'approved_order_qty_count':0,
            'broker_order_api_calls':0,'orders_sent':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def _read(path):
    try:
        data=json.loads(Path(path).read_text(encoding='utf8'))
        return data if isinstance(data,dict) else None
    except (OSError,ValueError,UnicodeError):return None

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--intents',default='v5_11_review.json')
    p.add_argument('--account',default='v5_13_review.json')
    p.add_argument('--buying',default='v5_19_indicative_power.json')
    p.add_argument('--output',default='v5_19_kis_reconcile.json')
    a=p.parse_args()
    result=reconcile(_read(a.intents),_read(a.account),_read(a.buying))
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V5.19 KIS_RECONCILE GATE=DENY; candidates='+str(result['candidate_count'])+'; orders_sent=0')
if __name__=='__main__':main()
