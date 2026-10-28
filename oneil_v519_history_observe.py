#!/usr/bin/env python3
"""KIS daily order/fill observation. Strict READ-ONLY, permanently DENY.
Never prints or saves broker order identifiers, amounts, account ids or raw responses.
This observation is not a complete position reconciliation or trading authorization.
"""
import argparse
import datetime as dt
import json
import os
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
URL='https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/inquire-daily-ccld'
CODE=re.compile(r'^\d{6}$')

def flags_off(env):
    return all(env.get(k,'false').strip().lower()=='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER'))

def fetch_page(key,secret,cano,product,token,date,fk,nk,continuing):
    params={'CANO':cano,'ACNT_PRDT_CD':product,'INQR_STRT_DT':date,'INQR_END_DT':date,
            'SLL_BUY_DVSN_CD':'00','INQR_DVSN':'00','PDNO':'','CCLD_DVSN':'00',
            'ORD_GNO_BRNO':'','ODNO':'','INQR_DVSN_3':'00','INQR_DVSN_1':'',
            'INQR_DVSN_2':'','CTX_AREA_FK100':fk,'CTX_AREA_NK100':nk,
            'EXCG_ID_DVSN_CD':'ALL'}
    headers={'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,
             'tr_id':'TTTC8001R','custtype':'P'}
    if continuing:headers['tr_cont']='N'
    req=urllib.request.Request(URL+'?'+urlencode(params),headers=headers,method='GET')
    with urllib.request.urlopen(req,timeout=15) as resp:
        cont=resp.headers.get('tr_cont')
        body=json.load(resp)
    return cont,body

def observe(now, *, enabled=False, provider=None, env=None):
    env=os.environ if env is None else env
    if not flags_off(env):raise ValueError('LIVE_SWITCH_NOT_DISABLED')
    if now.tzinfo is None:raise ValueError('NAIVE_CLOCK')
    state='NOT_REQUESTED'; pages=0; rows=0
    if enabled:
        state='BLOCKED'
        try:
            if provider is None:
                from kis_live_v4_5_2 import _read,_key,UTC
                from kis_buying_power_readonly import credentials
                saved=_read(_key(),dt.datetime.now(UTC))
                if not saved:raise RuntimeError('CACHE_MISSING')
                key,secret,cano,product=credentials()
                token=saved[0]
                provider=lambda date,fk,nk,again:fetch_page(key,secret,cano,product,token,date,fk,nk,again)
            day=now.astimezone(KST).strftime('%Y%m%d')
            seen=set();fk=nk=''
            for i in range(10):
                cont,body=provider(day,fk,nk,i>0)
                if not isinstance(body,dict) or str(body.get('rt_cd'))!='0':raise ValueError('RESPONSE_INVALID')
                if not isinstance(cont,str) or cont.strip().upper() not in ('','D','E','F','M'):
                    raise ValueError('CONTINUATION_INVALID')
                data=body.get('output1')
                if not isinstance(data,list) or any(not isinstance(x,dict) for x in data):
                    raise ValueError('ROWS_INVALID')
                pages+=1; rows+=len(data)
                if cont.strip().upper() not in ('F','M'):
                    state='OBSERVED_PAGES_COMPLETE_NOT_RECONCILED';break
                nf=str(body.get('ctx_area_fk100') or '').strip()
                nn=str(body.get('ctx_area_nk100') or '').strip()
                if not nf or not nn or (nf,nn) in seen:raise ValueError('CURSOR_INVALID')
                seen.add((nf,nn));fk,nk=nf,nn
            else:raise ValueError('PAGE_LIMIT')
        except Exception:
            state='BLOCKED';pages=rows=0
    return {'version':'5.19-history-observation','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','status':state,'pages_observed':pages,'rows_observed':rows,
            'independent_reconciliation_verified':False,'full_order_history_verified':False,
            'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--broker-read',action='store_true')
    p.add_argument('--output',default='v5_19_history_observe.json');a=p.parse_args()
    r=observe(dt.datetime.now(KST),enabled=a.broker_read)
    Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V519_HISTORY='+r['status']+'; pages='+str(r['pages_observed'])+'; gate=DENY; orders_sent=0')
if __name__=='__main__':main()
