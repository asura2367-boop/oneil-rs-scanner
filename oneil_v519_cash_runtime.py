"""Same-run KIS cash observation. Read-only, never authorizes or sends an order."""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from oneil_v519_cash_bridge import assess_readonly

KST=ZoneInfo('Asia/Seoul')

def inspect(candidates, provider=None, *, enabled=False, now=None, env=None):
    now=now or dt.datetime.now(KST)
    env=os.environ if env is None else env
    if now.tzinfo is None or any(env.get(k,'false').lower()!='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise ValueError('HARD_DENY_REQUIRED')
    if not isinstance(candidates,dict) or candidates.get('mode')!='LIVE_INTENTS_PREVIEW_ONLY' or candidates.get('gate')!='DENY' or candidates.get('orders_sent')!=0:
        raise ValueError('INVALID_CANDIDATES')
    intents=candidates.get('intents')
    if not isinstance(intents,list) or len(intents)>25:raise ValueError('INVALID_INTENTS')
    codes=[]
    for row in intents:
        if not isinstance(row,dict) or row.get('status')!='REVIEW_ONLY_UNPRICED' or row.get('quantity') is not None or row.get('price_krw') is not None:
            raise ValueError('INVALID_REVIEW_ROW')
        code=row.get('ticker')
        if not isinstance(code,str) or len(code)!=6 or not code.isascii() or not code.isdigit() or code in codes:
            raise ValueError('INVALID_TICKER')
        codes.append(code)
    checks=[]
    if not enabled:
        checks=[{'ticker':c,'status':'NOT_REQUESTED'} for c in codes]
        status='NOT_REQUESTED'
    else:
        try:
            provider=provider or KISReadOnlyProvider()
            held,unfilled=provider.positions()
            if not isinstance(held,set) or not isinstance(unfilled,set):raise ValueError('POSITION_INVALID')
            balance=provider.balance()
            status='OBSERVATION_ATTEMPTED'
            for ticker in codes:
                if ticker in held or ticker in unfilled:
                    state='HELD_OR_UNFILLED_BLOCKED'
                else:
                    try:
                        price=provider.reference_price(ticker,now)
                        power=provider.power(ticker,price)
                        r=assess_readonly(balance,power,ticker,price,held=held,unfilled=unfilled,now=now,env=env)
                        state='INDICATIVE_OBSERVED_NOT_AUTHORIZED' if r['cash_and_buying_power_observed'] else 'OBSERVATION_INVALID'
                    except Exception:state='OBSERVATION_INVALID'
                checks.append({'ticker':ticker,'status':state})
        except Exception:
            status='ACCOUNT_READ_BLOCKED';checks=[{'ticker':c,'status':'ACCOUNT_READ_BLOCKED'} for c in codes]
    return {'version':'5.19-cash-runtime','as_of_kst':now.isoformat(timespec='seconds'),
            'status':status,'checks':checks,'gate':'DENY','reserve_pct':40,'trading_pct':60,
            'stock_pct_of_trading_budget':30,'orders_sent':0,'broker_order_api_calls':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'blocking_reasons':['QUOTE_NOT_EXECUTABLE_ATTESTED','VENUE_ORDERABLE_CASH_UNVERIFIED',
              'DAILY_LOSS_UNVERIFIED','PERSISTENT_ORDER_RECOVERY_UNVERIFIED','LIVE_ORDER_TRANSMISSION_DISABLED']}

class KISReadOnlyProvider:
    def __init__(self):
        from kis_live_v4_5_2 import _read,_key,UTC
        from kis_buying_power_readonly import credentials
        token=_read(_key(),dt.datetime.now(UTC))
        if not token:raise RuntimeError('TOKEN_UNAVAILABLE')
        self.token=token[0]
        self.key,self.secret,self.cano,self.product=credentials()
    def positions(self):
        from kis_safety_v3 import verified_balance_snapshot,unfilled_codes
        held,_=verified_balance_snapshot(self.key,self.secret,self.cano,self.product,self.token)
        return held,unfilled_codes(self.key,self.secret,self.cano,self.product,self.token)
    def balance(self):
        from kis_buying_power_readonly import request,require_ok
        from urllib.parse import urlencode
        args={'CANO':self.cano,'ACNT_PRDT_CD':self.product,'AFHR_FLPR_YN':'N','OFL_YN':'',
              'INQR_DVSN':'00','UNPR_DVSN':'01','FUND_STTL_ICLD_YN':'N',
              'FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
              'CTX_AREA_FK100':'','CTX_AREA_NK100':''}
        headers={'authorization':'Bearer '+self.token,'appkey':self.key,'appsecret':self.secret,
                 'tr_id':'TTTC8434R','custtype':'P'}
        # This is a read-only snapshot; it cannot establish whole-account independent attestation.
        return require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-balance?'+urlencode(args),headers=headers),'Balance')
    def reference_price(self,ticker,now):
        from kis_safety_v3 import current_quote
        return current_quote(self.key,self.secret,self.token,ticker,now)
    def power(self,ticker,price):
        from kis_buying_power_readonly import request,require_ok
        from urllib.parse import urlencode
        p={'CANO':self.cano,'ACNT_PRDT_CD':self.product,'PDNO':ticker,'ORD_UNPR':str(price),
           'ORD_DVSN':'00','CMA_EVLU_AMT_ICLD_YN':'N','OVRS_ICLD_YN':'N'}
        h={'authorization':'Bearer '+self.token,'appkey':self.key,'appsecret':self.secret,
           'tr_id':'TTTC8908R','custtype':'P'}
        return require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-psbl-order?'+urlencode(p),headers=h),'Power')

def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--intents',default='v5_11_review.json')
    p.add_argument('--output',default='v5_19_cash_runtime.json')
    p.add_argument('--broker-read',action='store_true')
    a=p.parse_args()
    doc=json.loads(Path(a.intents).read_text(encoding='utf8'))
    out=inspect(doc,enabled=a.broker_read)
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V519_CASH_RUNTIME='+out['status']+'; gate=DENY; orders_sent=0')
if __name__=='__main__':main()
