#!/usr/bin/env python3
"""V5.19 fail-closed risk audit; pure Python, no broker endpoints or orders.

This diagnostic consumes externally verified, same-run evidence. It never
confuses presence of broker observations with authorization for trading.
"""
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^\d{6}$')

def _nonneg_int(v):
    return type(v) is int and v >= 0

def inspect(candidate, account, quote, risk, now=None):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: raise ValueError('AWARE_CLOCK_REQUIRED')
    findings=[]
    def block(label, ok):
        if not ok: findings.append(label)
    intents=candidate.get('intents') if isinstance(candidate,dict) else None
    codes=[r.get('ticker') for r in intents] if isinstance(intents,list) and all(isinstance(r,dict) for r in intents) else []
    valid=bool(isinstance(candidate,dict) and candidate.get('mode')=='LIVE_INTENTS_PREVIEW_ONLY'
          and candidate.get('gate')=='DENY' and candidate.get('orders_sent')==0
          and isinstance(intents,list) and len(intents)<=25
          and len(codes)==len(intents) and len(set(codes))==len(codes)
          and all(isinstance(c,str) and CODE.fullmatch(c) for c in codes))
    block('CANDIDATES_NOT_VALID',valid)
    account_ok=(isinstance(account,dict) and account.get('gate')=='DENY'
                and account.get('orders_sent')==0 and account.get('broker_status')=='READ_ONLY_OBSERVED')
    block('ACCOUNT_READ_NOT_VERIFIED',account_ok)
    quote_ok=(isinstance(quote,dict) and quote.get('version')=='5.18'
              and quote.get('quote_price_approved_count',0)>0
              and quote.get('quote_schema_fully_attested') is True
              and quote.get('exchange_business_day_attested') is True
              and quote.get('exchange_session_phase_attested') is True)
    block('EXECUTABLE_QUOTE_NOT_ATTESTED',quote_ok)
    loss_ok=(isinstance(risk,dict) and risk.get('daily_loss_verified') is True
             and risk.get('external_cashflows_reconciled') is True
             and risk.get('baseline_verified') is True
             and risk.get('orders_sent')==0)
    block('DAILY_LOSS_NOT_VERIFIED',loss_ok)
    # No monetary amounts from account observations are exposed.
    # Without synchronized certified prices, orderable quantities cannot be computed.
    findings.append('ORDER_SUBMISSION_DISABLED')
    return {'version':'5.19-risk-audit','generated_at_kst':now.isoformat(timespec='seconds'),
            'gate':'DENY','candidate_count':len(codes) if valid else 0,
            'account_observed':account_ok,'quote_attested':quote_ok,'daily_loss_verified':loss_ok,
            'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
            'approved_order_qty_count':0,'block_codes':findings,
            'broker_order_api_calls':0,'orders_sent':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError,UnicodeError): return None

def main():
    import argparse,os
    p=argparse.ArgumentParser()
    for n,default in [('candidate','v5_11_review.json'),('account','v5_13_review.json'),('quote','v5_18_report.json'),('risk','data/live/v4_8_audit.json'),('output','v5_19_risk_audit.json')]:
        p.add_argument('--'+n,default=default)
    args=p.parse_args()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise SystemExit('HARD_DENY_FLAGS_REQUIRED')
    out=inspect(load(args.candidate),load(args.account),load(args.quote),load(args.risk))
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V5.19 RISK GATE=DENY; candidates='+str(out['candidate_count'])+'; blocks='+','.join(out['block_codes'])+'; orders_sent=0')
if __name__=='__main__':main()
