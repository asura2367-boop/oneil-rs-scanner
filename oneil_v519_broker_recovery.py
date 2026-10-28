"""KIS daily order/fill reconciliation for pre-existing persisted order intents.
Read-only: never sends a new, cancel, or amend order.
"""
import datetime as dt
import os
import re
from zoneinfo import ZoneInfo
from oneil_v519_order_recovery import ReconcileBlocked
from oneil_v519_history_observe import fetch_page

KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^[0-9]{6}$')

def exact_int(v):
    if not isinstance(v,str) or re.fullmatch(r'\d+',v) is None:
        raise ReconcileBlocked('INVALID_BROKER_QUANTITY')
    return int(v)

def read_full_day(fetch, day):
    """Return complete raw rows only if broker response pagination terminates safely."""
    if not isinstance(day,str) or not re.fullmatch(r'\d{8}',day):
        raise ReconcileBlocked('INVALID_DAY')
    fk=nk='';seen=set();all_rows=[]
    for page in range(10):
        continuation, body=fetch(day,fk,nk,page>0)
        if not isinstance(body,dict) or str(body.get('rt_cd'))!='0':
            raise ReconcileBlocked('BROKER_HISTORY_API_INVALID')
        if not isinstance(continuation,str) or continuation.strip().upper() not in ('','D','E','F','M'):
            raise ReconcileBlocked('BROKER_PAGINATION_HEADER_INVALID')
        rows=body.get('output1')
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
            raise ReconcileBlocked('BROKER_HISTORY_ROWS_INVALID')
        all_rows.extend(rows)
        if len(all_rows)>10000:raise ReconcileBlocked('BROKER_HISTORY_ROW_LIMIT')
        if continuation.strip().upper() not in ('F','M'):
            return all_rows
        next_fk=str(body.get('ctx_area_fk100') or '').strip()
        next_nk=str(body.get('ctx_area_nk100') or '').strip()
        if not next_fk or not next_nk or (next_fk,next_nk) in seen:
            raise ReconcileBlocked('BROKER_PAGINATION_LOOP')
        seen.add((next_fk,next_nk));fk,nk=next_fk,next_nk
    raise ReconcileBlocked('BROKER_HISTORY_PAGE_LIMIT')

def reconcile_existing(journal, rows, *, trading_day, full_history_verified=False):
    """Never infer absence of orders when a submission is unknown.
    Only reconcile an order when an existing journal broker ID matches exactly.
    Missing/ambiguous rows leave the order locked; never automatic retry.
    """
    if not full_history_verified:raise ReconcileBlocked('INCOMPLETE_HISTORY')
    if not isinstance(rows,list):raise ReconcileBlocked('ROWS_INVALID')
    result={'matched':0,'unresolved':0,'orders_sent':0,'gate':'DENY'}
    pending=journal.db.execute("SELECT intent,ticker,qty,status,broker_id FROM journal WHERE status NOT IN ('FILLED','CANCELLED','REJECTED')").fetchall()
    for ident,ticker,qty,old_status,broker_id in pending:
        if not broker_id:
            result['unresolved']+=1
            continue
        matches=[r for r in rows if str(r.get('odno','')).strip()==broker_id and r.get('pdno')==ticker]
        if len(matches)!=1:
            result['unresolved']+=1
            continue
        row=matches[0]
        ordered=exact_int(row.get('ord_qty'))
        filled=exact_int(row.get('tot_ccld_qty'))
        remaining=exact_int(row.get('rmn_qty'))
        if ordered!=qty or filled>ordered or remaining>ordered or filled+remaining>ordered:
            raise ReconcileBlocked('ORDER_QUANTITY_CONFLICT')
        # Cancellations/rejections require another independently verified source;
        # a zero remaining quantity is not enough to infer a fill or cancellation.
        if filled==qty and remaining==0 and str(row.get('cncl_yn','')).upper() not in ('Y','1'):
            state='FILLED'
        elif 0<filled<qty:
            state='PART_FILLED'
        else:
            state='ACKNOWLEDGED'
        journal.reconcile(ident,{'broker_id':broker_id,'status':state,'filled_qty':filled},full_history_verified=True)
        result['matched']+=1
    return result

def observe_and_reconcile(journal, *, now=None, provider=None):
    if any(os.getenv(k,'false').lower()!='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise ReconcileBlocked('ORDER_SWITCH_MUST_BE_OFF')
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise ReconcileBlocked('CLOCK_INVALID')
    if provider is None:
        from kis_live_v4_5_2 import _read,_key,UTC
        from kis_buying_power_readonly import credentials
        cached=_read(_key(),dt.datetime.now(UTC))
        if not cached:raise ReconcileBlocked('TOKEN_CACHE_MISSING')
        key,secret,cano,product=credentials()
        token=cached[0]
        provider=lambda day,fk,nk,again:fetch_page(key,secret,cano,product,token,day,fk,nk,again)
    day=now.astimezone(KST).strftime('%Y%m%d')
    rows=read_full_day(provider,day)
    return reconcile_existing(journal,rows,trading_day=day,full_history_verified=True)
