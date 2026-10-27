#!/usr/bin/env python3
"""KIS indicative buying-power observation. Strictly read only; never authorize orders.

Uses REST current quote as an INDICATIVE query input only, never an attested executable price.
Sensitive price, amounts, qty, tokens, account numbers never written to reports.
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^\d{6}$')

def observe(intents, now, *, enabled=False, provider=None, env=None):
    env=os.environ if env is None else env
    if env.get('LIVE_ENABLED','false').lower()!='false' or env.get('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('ORDER_FLAGS_NOT_DISABLED')
    if now.tzinfo is None:
        raise ValueError('NAIVE_CLOCK')
    if not isinstance(intents,dict) or intents.get('mode')!='LIVE_INTENTS_PREVIEW_ONLY' or intents.get('gate')!='DENY' or intents.get('orders_sent')!=0:
        raise ValueError('UNSAFE_INTENTS')
    rows=intents.get('intents')
    if not isinstance(rows,list) or len(rows)>25:
        raise ValueError('INVALID_CANDIDATES')
    seen=set(); codes=[]
    for item in rows:
        if not isinstance(item,dict) or item.get('status')!='REVIEW_ONLY_UNPRICED' or item.get('quantity') is not None or item.get('price_krw') is not None:
            raise ValueError('INVALID_INTENT_ROW')
        t=item.get('ticker')
        if not isinstance(t,str) or not CODE.fullmatch(t) or t in seen:
            raise ValueError('INVALID_TICKER')
        seen.add(t);codes.append(t)
    statuses=[]
    regular= now.weekday()<5 and dt.time(9,0)<=now.timetz().replace(tzinfo=None)<dt.time(15,30)
    if not enabled:
        statuses=[{'ticker':t,'status':'NOT_REQUESTED'} for t in codes]
        state='NOT_REQUESTED'
    elif not regular:
        statuses=[{'ticker':t,'status':'OUTSIDE_REGULAR_SESSION'} for t in codes]
        state='SESSION_BLOCKED'
    else:
        state='OBSERVATION_ATTEMPTED'
        try:
            if provider is None:
                provider=KisProvider()
            held,unfilled=provider.account()
            if not isinstance(held,set) or not isinstance(unfilled,set) or any(not CODE.fullmatch(t) for t in (held|unfilled)):
                raise ValueError('UNVERIFIED_BROKER_STATE')
        except Exception:
            state='ACCOUNT_READ_BLOCKED'
            statuses=[{'ticker':t,'status':'ACCOUNT_READ_BLOCKED'} for t in codes]
        else:
            for t in codes:
                if t in held:
                    status='ALREADY_HELD'
                elif t in unfilled:
                    status='UNFILLED_ORDER'
                else:
                    try:
                        # Price never leaves the process. No evidence that REST price is executable.
                        reference=provider.indicative_price(t,now)
                        if isinstance(reference,bool) or not isinstance(reference,int) or reference<=0:
                            raise ValueError('BAD_PRICE')
                        cash,qty=provider.buying_power(t,reference)
                        if isinstance(cash,bool) or isinstance(qty,bool) or not isinstance(cash,int) or not isinstance(qty,int) or cash<0 or qty<0:
                            raise ValueError('BAD_POWER')
                        status='INDICATIVE_POWER_OBSERVED_UNATTESTED' if cash>0 and qty>0 else 'INDICATIVE_NO_BUYING_POWER'
                    except Exception:
                        status='INDICATIVE_LOOKUP_BLOCKED'
                statuses.append({'ticker':t,'status':status})
    return {'version':'5.19-indicative-power','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','source':'UNATTESTED_REST_REFERENCE_PRICE','status':state,
            'candidate_count':len(codes),'checks':statuses,'reference_price_certified':False,
            'broker_buying_power_verified':False,'daily_loss_verified':False,
            'actual_order_qty_verified':False,'broker_order_api_calls':0,'orders_sent':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

class KisProvider:
    def __init__(self):
        from kis_live_v4_5_2 import _read,_key,UTC
        from kis_buying_power_readonly import credentials
        cached=_read(_key(),dt.datetime.now(UTC))
        if not cached:
            raise RuntimeError('CACHE_EXPIRED')
        self.token=cached[0]
        self.key,self.secret,self.cano,self.product=credentials()
    def account(self):
        from kis_safety_v3 import verified_balance_snapshot,unfilled_codes
        held,_invested=verified_balance_snapshot(self.key,self.secret,self.cano,self.product,self.token)
        pending=unfilled_codes(self.key,self.secret,self.cano,self.product,self.token)
        return held,pending
    def indicative_price(self,ticker,now):
        from kis_safety_v3 import current_quote
        return current_quote(self.key,self.secret,self.token,ticker,now)
    def buying_power(self,ticker,price):
        from kis_buying_power_readonly import get_buying_power
        return get_buying_power(self.key,self.secret,self.cano,self.product,self.token,ticker,price)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--intents',default='v5_11_review.json')
    p.add_argument('--output',default='v5_19_indicative_power.json')
    p.add_argument('--broker-read',action='store_true')
    a=p.parse_args()
    data=json.loads(Path(a.intents).read_text(encoding='utf-8'))
    r=observe(data,dt.datetime.now(KST),enabled=a.broker_read)
    Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from collections import Counter
    counts=dict(Counter(x['status'] for x in r['checks']))
    print('V5.19 INDICATIVE_POWER GATE=DENY; status='+r['status']+'; candidates='+str(r['candidate_count'])+'; counts='+json.dumps(counts,sort_keys=True)+'; broker_buying_power_verified=false; orders_sent=0')
if __name__=='__main__':main()
