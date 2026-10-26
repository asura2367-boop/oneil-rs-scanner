#!/usr/bin/env python3
"""V4.5 hard-denied order gate, consumes same-run V4.1 and V4.4 snapshots.
No HTTP, no broker imports, no BUY/SELL/CANCEL API. Every output denies sending.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CAP = 1_000_000
PER_STOCK = 200_000
TICKER = re.compile(r"^[0-9]{6}$")

class GateBlocked(Exception): pass

def guard():
    if os.getenv('LIVE_ENABLED','false').lower() != 'false' or os.getenv('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise GateBlocked('LIVE_FLAGS_NOT_DISABLED')
    if os.getenv('EMERGENCY_HALT','false').lower() != 'false':
        raise GateBlocked('EMERGENCY_HALT')

def read(path):
    with open(path, encoding='utf-8') as f: return json.load(f)

def evaluate(pre, recon, state, *, now, source_mtime=None, required_newer_than=None):
    """A diagnostic gate: no outcome can authorize real orders.

    Fail-closed inputs; unverified daily-loss/unresolved states block all candidates.
    """
    reasons=[]
    def block(reason):
        if reason not in reasons: reasons.append(reason)
    if required_newer_than is not None and (not source_mtime or any(v is None or v < required_newer_than for v in source_mtime)):
        block('NOT_SAME_RUN_ARTIFACT')
    if now.weekday() >= 5 or not (dt.time(9,0) <= now.time() < dt.time(15,30)):
        block('OUTSIDE_REGULAR_SESSION')
    if not isinstance(pre,dict) or pre.get('mode')!='READ_ONLY_PREFLIGHT_V4_1' or pre.get('live_enabled') is not False or pre.get('orders_sent') != 0:
        block('PREFLIGHT_INVALID')
        decisions=[]
    else:
        decisions=pre.get('decisions')
        if not isinstance(decisions,list) or len(decisions)>5: block('DECISIONS_INVALID');decisions=[]
        try:
            stamp=dt.datetime.fromisoformat(pre['generated_at_kst'])
            if stamp.tzinfo is None or abs((now-stamp.astimezone(KST)).total_seconds())>120:
                block('PREFLIGHT_STALE')
        except (ValueError,TypeError,KeyError): block('PREFLIGHT_TIMESTAMP_INVALID')
    if not isinstance(recon,dict) or recon.get('mode')!='V4_4_RECONCILE_READ_ONLY' or recon.get('orders_sent')!=0 or recon.get('live_enabled') is not False or recon.get('broker_order_api_calls')!=0 or recon.get('broker_readonly_queries_performed') is not True:
        block('RECONCILIATION_INVALID')
    else:
        if recon.get('requires_manual_review') is not False or recon.get('unmatched_broker_order_ids')!=0 or recon.get('unresolved_linked_ids')!=0:
            block('BROKER_ORDER_REVIEW_REQUIRED')
        if recon.get('journal_modified') is not False: block('JOURNAL_MUTATION')
    if not isinstance(state,dict) or state.get('mode')!='NO_ORDER_STATE_TRACKER' or state.get('halted') is not False or not isinstance(state.get('intents'),dict):
        block('JOURNAL_INVALID_OR_HALTED')
    else:
        for row in state['intents'].values():
            if (not isinstance(row,dict) or row.get('state')!='PREVIEW_ONLY' or row.get('broker_order_id') is not None or row.get('filled_qty')!=0):
                block('NONPREVIEW_OR_UNRESOLVED_STATE');break
    seen=set();total=0;eligible=0
    for row in decisions:
        if not isinstance(row,dict): block('INVALID_DECISION');continue
        t=row.get('ticker'); status=row.get('status');qty=row.get('preview_qty');amt=row.get('preview_amount_krw')
        if not isinstance(t,str) or not TICKER.fullmatch(t) or t in seen:
            block('DUPLICATE_OR_BAD_TICKER');continue
        seen.add(t)
        if status=='PREVIEW_ELIGIBLE_NO_ORDER':
            if any(type(n) is not int or n<=0 for n in (qty,amt)) or amt>PER_STOCK:
                block('PER_STOCK_CAP_OR_QUANTITY_INVALID');continue
            eligible+=1;total+=amt
        elif status is None or not str(status).startswith('BLOCKED_'):
            block('UNKNOWN_DECISION_STATUS')
    if total>CAP: block('TOTAL_CAP_EXCEEDED')
    # Cannot infer daily realized + unrealized P/L from V4.1/V4.4 aggregate reports.
    block('DAILY_LOSS_NOT_VERIFIED')
    # Deliberate permanent order transmission interlock for this version.
    block('ORDER_TRANSMISSION_NOT_IMPLEMENTED')
    return {'mode':'V4_5_FINAL_GATE_READ_ONLY','approved_for_live_order':False,
            'orders_sent':0,'broker_api_calls':0,'live_enabled':False,
            'decision':'DENY','reasons':sorted(reasons),
            'candidate_count':len(seen),'eligible_preview_count':eligible,
            'combined_preview_krw':total,'total_cap_krw':CAP,'per_stock_cap_krw':PER_STOCK}

def safe_source(path, label):
    """Read a snapshot without treating a missing or malformed file as success."""
    file = Path(path)
    try:
        stat = file.stat()
        if not file.is_file() or stat.st_size > 2_000_000:
            return None, None, label + '_INVALID'
        with file.open(encoding='utf-8') as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            return None, None, label + '_INVALID'
        return value, stat.st_mtime, None
    except FileNotFoundError:
        return None, None, label + '_MISSING'
    except (OSError, UnicodeError, ValueError, TypeError):
        return None, None, label + '_INVALID'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--preflight',required=True)
    p.add_argument('--reconcile',required=True)
    p.add_argument('--state',default='data/live/v4_3_state.json')
    p.add_argument('--output',default='data/live/v4_5_gate_report.json')
    p.add_argument('--run-marker',required=True)
    a=p.parse_args()
    guard()
    pre,pre_time,pre_error=safe_source(a.preflight,'PREFLIGHT_REPORT')
    recon,recon_time,recon_error=safe_source(a.reconcile,'RECONCILIATION_REPORT')
    state,_,state_error=safe_source(a.state,'STATE_REPORT')
    try:
        marker=Path(a.run_marker).stat().st_mtime
        marker_error=None
    except OSError:
        marker=None
        marker_error='RUN_MARKER_MISSING'
    # Missing input can never count as a current-run snapshot.
    result=evaluate(pre,recon,state,now=dt.datetime.now(KST),
        source_mtime=[pre_time,recon_time] if pre_time is not None and recon_time is not None else None,
        required_newer_than=marker if marker is not None else float('inf'))
    errors=[x for x in (pre_error,recon_error,state_error,marker_error) if x]
    result['reasons']=sorted(set(result['reasons']+errors))
    dest=Path(a.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_name(dest.name+'.tmp')
    temp.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    temp.replace(dest)
    print('V4.5 FINAL GATE: DENY; orders_sent=0; LIVE_ENABLED=false')
    print('candidate_count=',result['candidate_count'],'eligible_preview_count=',result['eligible_preview_count'])
    print('block_codes=',','.join(result['reasons']))

if __name__=='__main__':
    try:main()
    except Exception as e:
        code=str(e) if isinstance(e,GateBlocked) else type(e).__name__
        print('V4.5 BLOCKED: '+code+'; orders_sent=0',file=sys.stderr);sys.exit(1)
