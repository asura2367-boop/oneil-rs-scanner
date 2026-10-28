#!/usr/bin/env python3
"""V5.19 diagnostic ONLY. Verify private equity evidence and order history presence.
No network calls; never sends, retries, cancels, or authorizes orders.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
# Same evidence contract as kis_live_v4_7, independently checked here.
LOSS_LIMIT = 20000
class ValidationError(ValueError): pass

def verify_snapshot(obj, now):
    required = {'schema_version','market_date','scope','evidence_verified','baseline_time_kst',
      'current_time_kst','baseline_equity_krw','current_equity_krw','net_external_cashflow_krw',
      'cashflows_reconciled','holdings_reconciled','fees_included','source_complete'}
    if not isinstance(obj,dict) or set(obj)!=required or type(obj.get('schema_version')) is not int or obj['schema_version']!=1:
        raise ValidationError('SCHEMA')
    if obj['market_date']!=now.strftime('%Y-%m-%d') or obj['scope']!='WHOLE_ACCOUNT':
        raise ValidationError('DATE_OR_SCOPE')
    if any(obj.get(k) is not True for k in ('evidence_verified','cashflows_reconciled','holdings_reconciled','fees_included','source_complete')):
        raise ValidationError('EVIDENCE_UNVERIFIED')
    try:
        start=dt.datetime.fromisoformat(obj['baseline_time_kst'])
        end=dt.datetime.fromisoformat(obj['current_time_kst'])
    except (TypeError,ValueError): raise ValidationError('STAMP') from None
    kst_offset=dt.timedelta(hours=9)
    if (start.tzinfo is None or end.tzinfo is None or start.utcoffset()!=kst_offset
        or end.utcoffset()!=kst_offset or start.date()!=now.date() or end.date()!=now.date()
        or start.time()>dt.time(9,0) or start>=end or end>now+dt.timedelta(seconds=30)
        or now-end>dt.timedelta(minutes=5)):
        raise ValidationError('EVIDENCE_TIME')
    for k in ('baseline_equity_krw','current_equity_krw','net_external_cashflow_krw'):
        if type(obj[k]) is not int: raise ValidationError('VALUE_TYPE')
    if obj['baseline_equity_krw']<=0 or obj['current_equity_krw']<0:
        raise ValidationError('VALUE_RANGE')
    return obj['current_equity_krw']-obj['baseline_equity_krw']-obj['net_external_cashflow_krw']

KST = ZoneInfo('Asia/Seoul')

def _utc_aware(text):
    if not isinstance(text,str): return None
    try:
        v=dt.datetime.fromisoformat(text)
        return v if v.tzinfo and v.utcoffset() is not None else None
    except (ValueError,TypeError,OverflowError): return None

def analyze(equity, history, now=None, env=None):
    env=os.environ if env is None else env
    if env.get('LIVE_ENABLED','false').lower()!='false' or env.get('KIS_ALLOW_ORDER','false').lower()!='false':
        raise ValueError('LIVE_FLAGS_MUST_BE_FALSE')
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: raise ValueError('AWARE_CLOCK_REQUIRED')
    blocks=[]; pnl_ok=False; limit_reached=None
    try:
        pnl=verify_snapshot(equity,now)
        pnl_ok=True
        limit_reached=bool(pnl <= -LOSS_LIMIT)
        if limit_reached: blocks.append('DAILY_LOSS_LIMIT_REACHED')
    except (ValidationError,TypeError,AttributeError):
        blocks.append('DAILY_PNL_EVIDENCE_UNVERIFIED')
    # Even independent report presence is not proof of complete broker order history.
    history_ok=False
    if isinstance(history,dict) and history.get('source')=='BROKER_ORDER_HISTORY' and history.get('verified') is True and history.get('complete') is True and history.get('orders_sent')==0:
        stamp=_utc_aware(history.get('generated_at_kst'))
        rows=history.get('orders')
        if stamp and 0 <= (now-stamp.astimezone(KST)).total_seconds()<=300 and isinstance(rows,list) and len(rows)<=1000:
            ids=[]; valid=True
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('broker_order_id'),str) or not row['broker_order_id'] or row.get('state') not in {'ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED'}:
                    valid=False;break
                qty=row.get('requested_qty');filled=row.get('filled_qty')
                if type(qty) is not int or qty<=0 or type(filled) is not int or not 0<=filled<=qty:
                    valid=False;break
                if row['state']=='FILLED' and filled!=qty: valid=False;break
                if row['state']=='PART_FILLED' and not 0<filled<qty:valid=False;break
                ids.append(row['broker_order_id'])
            history_ok=valid and len(ids)==len(set(ids))
    if not history_ok: blocks.append('BROKER_ORDER_HISTORY_UNVERIFIED')
    blocks.append('INDEPENDENT_BROKER_RECONCILIATION_REQUIRED')
    blocks.append('LIVE_ORDER_TRANSMISSION_DISABLED')
    return {'version':'5.19-loss-history-diagnostic','generated_at_kst':now.isoformat(timespec='seconds'),
            'gate':'DENY','daily_loss_evidence_valid':pnl_ok,'daily_loss_limit_reached':limit_reached,
            'order_history_structure_valid':history_ok,'block_codes':blocks,
            'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def read_file(path):
    if path is None:return None
    try:
        obj=json.loads(Path(path).read_text(encoding='utf8'))
        return obj if isinstance(obj,dict) else None
    except (ValueError,OSError,UnicodeError):return None

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--equity',default=None,help='private verified snapshot file; never upload')
    p.add_argument('--history',default=None,help='private broker history file; never upload')
    p.add_argument('--output',default='v5_19_loss_history_diagnostic.json')
    args=p.parse_args()
    out=analyze(read_file(args.equity),read_file(args.history))
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V519_LOSS_HISTORY=DENY; daily_pnl_verified='+str(out['daily_loss_evidence_valid']).lower()+'; history_format_verified='+str(out['order_history_structure_valid']).lower()+'; orders_sent=0')
if __name__=='__main__':main()
