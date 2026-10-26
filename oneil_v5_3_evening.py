#!/usr/bin/env python3
"""KRX/NXT after-hours READ-ONLY orderbook diagnostics. NEVER sends orders.

A REST snapshot does not prove real-time market timestamp or participation.
Thus this tool never emits an order-eligible signal.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import requests

KST=ZoneInfo('Asia/Seoul')
HOST='https://openapi.koreainvestment.com:9443'
QUOTES='/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn'
TR_ID='FHKST01010200'
MAX_CODES=5

def guard():
    if os.environ.get('LIVE_ENABLED','false').lower()!='false' or os.environ.get('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('LIVE_GUARD_BLOCKED')

def exchange_session(now):
    minute=now.hour*60+now.minute
    if now.weekday()>4: return []
    markets=[]
    if 15*60+40 <= minute < 20*60: markets.append('NXT')
    if 16*60 <= minute < 20*60: markets.append('KRX')
    return markets

def read_codes(path, today):
    import csv
    if not path.exists(): return [], 'CANDIDATES_MISSING'
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not {'date','ticker'}.issubset(reader.fieldnames or []):return [],'INVALID_CANDIDATES'
        codes={r['ticker'] for r in reader if r['date']==today and re.fullmatch(r'[0-9]{6}',r['ticker'] or '')}
    return sorted(codes)[:MAX_CODES], 'SIGNAL_DATE_MATCHED' if codes else 'NO_TODAY_CANDIDATES'

def get_token(http,key,secret):
    r=http.post(HOST+'/oauth2/tokenP',json={'grant_type':'client_credentials','appkey':key,'appsecret':secret},timeout=12)
    r.raise_for_status()
    data=r.json()
    token=data.get('access_token') if isinstance(data,dict) else None
    if not isinstance(token,str) or not token:raise RuntimeError('TOKEN_MISSING')
    return token

def fetch_orderbook(http,key,secret,token,ticker,market):
    # KIS quote market code is API-specific; verify independently against broker docs.
    market_code={'KRX':'J','NXT':'NX'}[market]
    params={'FID_COND_MRKT_DIV_CODE':market_code,'FID_INPUT_ISCD':ticker}
    hdr={'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'tr_id':TR_ID,'custtype':'P'}
    r=http.get(HOST+QUOTES,params=params,headers=hdr,timeout=12)
    r.raise_for_status()
    data=r.json()
    if not isinstance(data,dict) or str(data.get('rt_cd'))!='0':raise RuntimeError('QUOTE_REJECTED')
    book=data.get('output1')
    if not isinstance(book,dict):raise RuntimeError('BOOK_MISSING')
    def natural(v):
        s=str(v if v is not None else '').strip()
        return int(s) if re.fullmatch('[0-9]+',s) else 0
    ask=natural(book.get('askp1'))
    bid=natural(book.get('bidp1'))
    ask_q=natural(book.get('askp_rsqn1'))
    bid_q=natural(book.get('bidp_rsqn1'))
    if not (ask>0 and bid>0 and ask>=bid and ask_q>0 and bid_q>0):raise RuntimeError('BOOK_INVALID')
    return {'bid_krw':bid,'ask_krw':ask,'spread_pct':round((ask-bid)/bid*100,3),'bid_size':bid_q,'ask_size':ask_q}

def evaluate(now, control_enabled, codes, quote_fn):
    markets=exchange_session(now)
    report={'as_of_kst':now.isoformat(timespec='seconds'), 'analysis_switch_on':bool(control_enabled),
            'markets_in_session':markets, 'mode':'READ_ONLY_MARKET_DIAGNOSTICS',
            'live_price_timestamp_verified':False, 'exchange_eligibility_verified':False,
            'orders_sent':0, 'live_enabled':False, 'kis_allow_order':False,
            'candidates':[], 'status':'REVIEW_ONLY'}
    if not control_enabled:report['status']='SKIPPED_OFF';return report
    if not markets:report['status']='MARKET_CLOSED';return report
    for code in codes:
        for market in markets:
            item={'ticker':code,'market':market,'trade_authorized':False,'reason':'REST_TIMESTAMP_UNVERIFIED'}
            try: item.update(quote_fn(code,market))
            except Exception: item['reason']='QUOTE_UNAVAILABLE_OR_INVALID'
            report['candidates'].append(item)
    return report

def main():
    guard()
    from oneil_v5_2_schedule import control,read_candidates
    parser=argparse.ArgumentParser()
    parser.add_argument('--switch',default='data/live/auto_control.json')
    parser.add_argument('--candidates',default='data/v4b/candidates.csv')
    parser.add_argument('--output',default='v5_3_evening_report.json')
    parser.add_argument('--network',action='store_true',help='Read-only API; disabled by default')
    a=parser.parse_args();now=dt.datetime.now(KST)
    enabled,switch_status=control(Path(a.switch))
    codes,signal_status=read_candidates(Path(a.candidates),now.strftime('%Y-%m-%d'))
    source='OFFLINE_DIAGNOSTIC'
    def offline(_,__):raise RuntimeError('NETWORK_DISABLED')
    fetch=offline
    if a.network and enabled and codes and exchange_session(now):
        key=os.getenv('KIS_APP_KEY','').strip();secret=os.getenv('KIS_APP_SECRET','').strip()
        if key and secret:
            try:
                http=requests.Session();token=get_token(http,key,secret)
                fetch=lambda code,market:fetch_orderbook(http,key,secret,token,code,market)
                source='KIS_READ_ONLY_REST'
            except Exception:source='AUTH_UNAVAILABLE'
        else:source='SECRETS_UNAVAILABLE'
    result=evaluate(now,enabled,codes,fetch)
    result.update(source=source,switch_status=switch_status,signal_status=signal_status)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V5.3 EVENING_DIAGNOSTIC status='+result['status']+' switch='+switch_status+
          ' source='+source+' candidates='+str(len(result['candidates']))+
          ' orders_sent=0 LIVE_ENABLED=false KIS_ALLOW_ORDER=false')
    # Do not write tokens or API response bodies to a report.
if __name__=='__main__':main()
