#!/usr/bin/env python3
"""KIS domestic stock balance READ ONLY. Never sends trade orders or writes account data."""
import json, os, sys, urllib.request, urllib.error
from decimal import Decimal, InvalidOperation

HOST='https://openapi.koreainvestment.com:9443'
LIMIT=1_000_000

def req(method, path, payload=None, headers=None):
    data=json.dumps(payload).encode() if payload is not None else None
    h={'Content-Type':'application/json; charset=utf-8', **(headers or {})}
    request=urllib.request.Request(HOST+path, data=data, headers=h, method=method)
    with urllib.request.urlopen(request,timeout=15) as response:
        return json.load(response)

def num(value):
    try: return Decimal(str(value or '0'))
    except (InvalidOperation, ValueError): return Decimal(0)

def main():
    # Refuse all attempts to turn this script into an order-capable mode.
    if os.getenv('LIVE_ENABLED','false').lower() != 'false':
        raise RuntimeError('HARD STOP: LIVE_ENABLED must be false')
    key=os.environ['KIS_APP_KEY'].strip()
    secret=os.environ['KIS_APP_SECRET'].strip()
    cano=os.environ['KIS_ACCOUNT_NO'].strip()
    product=os.environ['KIS_ACCOUNT_PRODUCT'].strip()
    if not (len(cano)==8 and cano.isdigit() and len(product)==2 and product.isdigit()):
        raise ValueError('Invalid account format (expected 8 and 2 digits)')
    token_resp=req('POST','/oauth2/tokenP',{'grant_type':'client_credentials','appkey':key,'appsecret':secret})
    token=token_resp.get('access_token')
    if not token: raise RuntimeError('Token response did not contain access_token')
    from urllib.parse import urlencode
    params={
       'CANO':cano,'ACNT_PRDT_CD':product,'AFHR_FLPR_YN':'N','OFL_YN':'',
       'INQR_DVSN':'00','UNPR_DVSN':'01','FUND_STTL_ICLD_YN':'N',
       'FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
       'CTX_AREA_FK100':'','CTX_AREA_NK100':''
    }
    result=req('GET','/uapi/domestic-stock/v1/trading/inquire-balance?'+urlencode(params),headers={
       'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'tr_id':'TTTC8434R',
       'custtype':'P'
    })
    if str(result.get('rt_cd'))!='0':
        raise RuntimeError('Balance query rejected (message code: '+str(result.get('msg_cd','unknown'))+')')
    holdings=result.get('output1') or []
    totals=result.get('output2') or []
    summary=totals[0] if totals else {}
    cash=num(summary.get('dnca_tot_amt'))
    available=num(summary.get('prvs_rcdl_excc_amt')) if summary.get('prvs_rcdl_excc_amt') else None
    print('KIS real-account READ-ONLY API: SUCCESS')
    print('Holdings count:',sum(1 for p in holdings if num(p.get('hldg_qty'))>0))
    print('Account cash figure (KRW):',int(cash))
    if available is not None: print('Reference withdrawal-available (KRW):',int(available))
    print('Strategy maximum capital (KRW):',LIMIT)
    print('WARNING: cash figure is not orderable cash; separate buying-power API needed.')
    print('No account identifiers, tokens, position details, or response bodies stored.')
    print('LIVE_ENABLED=false; BUY/SELL API absent; orders sent: 0')

if __name__=='__main__':
    try: main()
    except Exception as ex:
        # No exception from URL/HTTP response bodies; avoid printing credentials
        print('READ-ONLY CHECK FAILED:',type(ex).__name__,file=sys.stderr)
        sys.exit(1)
