"""Sync previously acknowledged KIS orders into Supabase. Never places orders.
Only current KST day is supported. Unknown or unmatched orders stay blocked.
"""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo
from v519_pg_ledger import connect, LedgerBlocked
from v519_pg_reconcile import reconcile_existing
from oneil_v519_broker_recovery import read_full_day, exact_int
from oneil_v519_history_observe import fetch_page
from oneil_v519_live_runner import token
from kis_buying_power_readonly import credentials

KST=ZoneInfo('Asia/Seoul')

def main():
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise LedgerBlocked('LIVE_SWITCH_MUST_BE_OFF')
    key,secret,cano,product=credentials()
    tok=token(key,secret)
    day=dt.datetime.now(KST).strftime('%Y%m%d')
    rows=read_full_day(lambda date,fk,nk,again:fetch_page(key,secret,cano,product,tok,date,fk,nk,again),day)
    with connect() as db:
        with db.cursor() as cur:
            cur.execute("SELECT intent_id,ticker,quantity,broker_order_id FROM v519_order_intents WHERE status NOT IN ('FILLED','CANCELLED','REJECTED')")
            pending=cur.fetchall()
    updated=0; unresolved=0
    for ident,ticker,qty,broker_id in pending:
        if not broker_id:
            unresolved+=1;continue
        matches=[r for r in rows if str(r.get('odno','')).strip()==broker_id and r.get('pdno')==ticker]
        if len(matches)!=1:
            unresolved+=1;continue
        r=matches[0]
        ordered=exact_int(r.get('ord_qty'))
        filled=exact_int(r.get('tot_ccld_qty'))
        remaining=exact_int(r.get('rmn_qty'))
        if ordered!=qty or filled>qty or remaining>qty or filled+remaining>qty:
            raise LedgerBlocked('BROKER_QUANTITY_CONFLICT')
        # Do not infer cancellation or rejection from zero remaining quantity.
        if filled==qty and remaining==0 and str(r.get('cncl_yn','')).upper() not in ('Y','1'):
            state='FILLED'
        elif 0<filled<qty:
            state='PART_FILLED'
        else:
            state='ACKNOWLEDGED'
        reconcile_existing(ident,broker_id,state,filled,broker_history_complete=True)
        updated+=1
    print(json.dumps({'gate':'DENY','orders_sent':0,'updated':updated,'unresolved':unresolved,'scope':'current_KST_day_only'}))

if __name__=='__main__':
    try:main()
    except Exception:
        print('SYNC_BLOCKED; orders_sent=0')
        raise SystemExit(2)
