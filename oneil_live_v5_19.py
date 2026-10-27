#!/usr/bin/env python3
"""V5.19 read-only evidence merger. Always DENY. No broker order calls."""
import argparse, datetime as dt, json, os
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')

def load(path):
    if not path or not Path(path).is_file(): return None
    try:
        obj=json.loads(Path(path).read_text(encoding='utf8'))
        return obj if isinstance(obj,dict) else None
    except (OSError,ValueError,UnicodeError): return None

def fresh(obj, now, minutes=5):
    if not isinstance(obj,dict): return False
    raw=obj.get('generated_at_kst') or obj.get('timestamp_kst')
    if not isinstance(raw,str):return False
    try:
        moment=dt.datetime.fromisoformat(raw)
        return (moment.tzinfo is not None and moment.utcoffset() is not None and
                0<=(now-moment.astimezone(KST)).total_seconds()<=minutes*60)
    except (ValueError,TypeError,OverflowError):return False

def build(candidate, quote, account, risk, now=None):
    now=now or dt.datetime.now(KST)
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('HARD_DENY_FLAGS_REQUIRED')
    issues=[]
    def check(ok,reason):
        if not ok: issues.append(reason)
    # Files are deliberately NOT trusted merely because they exist.
    c_ok=isinstance(candidate,dict) and candidate.get('gate')=='DENY' and candidate.get('orders_sent')==0
    count=0
    if c_ok:
        intents=candidate.get('review_intents')
        if not isinstance(intents,list):intents=candidate.get('intents')
        if not isinstance(intents,list):intents=candidate.get('review_candidates')
        if isinstance(intents,list) and len(intents)<=25:
            codes=[i.get('ticker') for i in intents if isinstance(i,dict)]
            if len(codes)==len(intents) and all(isinstance(v,str) and len(v)==6 and v.isascii() and v.isdigit() for v in codes) and len(set(codes))==len(codes):count=len(codes)
            else:c_ok=False
        else:c_ok=False
    check(c_ok,'CANDIDATE_EVIDENCE_MISSING_OR_INVALID')
    q_ok=fresh(quote,now) and quote.get('version')=='5.18' and quote.get('gate')=='DENY' and quote.get('orders_sent')==0
    check(q_ok,'QUOTE_EVIDENCE_MISSING_STALE_OR_INVALID')
    # V5.18 only attests frame observation; it cannot authorize prices.
    check(q_ok and quote.get('quote_price_approved_count',0)>0 and quote.get('quote_schema_fully_attested') is True and quote.get('exchange_session_phase_attested') is True and quote.get('exchange_business_day_attested') is True,'QUOTE_NOT_CERTIFIED_FOR_TRADING')
    a_ok=fresh(account,now) and account.get('orders_sent')==0 and account.get('gate')=='DENY'
    check(a_ok,'ACCOUNT_EVIDENCE_MISSING_STALE_OR_INVALID')
    check(a_ok and account.get('broker_buying_power_verified') is True and account.get('holding_unfilled_verified') is True,'ACCOUNT_BUYING_POWER_NOT_VERIFIED')
    r_ok=fresh(risk,now) and risk.get('orders_sent')==0 and risk.get('daily_loss_verified') is True and risk.get('external_cashflows_reconciled') is True and risk.get('baseline_verified') is True
    check(r_ok,'DAILY_LOSS_OR_BASELINE_NOT_VERIFIED')
    issues.append('ORDER_TRANSMISSION_NOT_IMPLEMENTED')
    return {'version':'5.19','generated_at_kst':now.isoformat(timespec='seconds'),'gate':'DENY',
            'mode':'EVIDENCE_MERGE_NO_ORDERS','candidate_count':count,
            'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
            'live_price_approved_count':0,'live_orderable_qty_approved_count':0,
            'order_intents_authorized':0,'issues':issues,
            'broker_order_api_calls':0,'orders_sent':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--candidates',default='v5_11_review.json')
    p.add_argument('--quotes',default='v5_18_report.json')
    p.add_argument('--account',default='v5_13_report.json')
    p.add_argument('--risk',default='data/live/v4_8_audit.json')
    p.add_argument('--output',default='v5_19_report.json')
    x=p.parse_args()
    out=build(load(x.candidates),load(x.quotes),load(x.account),load(x.risk))
    Path(x.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V5.19 GATE=DENY; candidates='+str(out['candidate_count'])+'; reasons='+','.join(out['issues'])+'; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()
