#!/usr/bin/env python3
"""KIS quote WebSocket connectivity diagnostic, strictly NO ORDERS.

Receives limited KRX/NXT market-data frames and stores only non-sensitive counts.
Raw payloads/approval keys are never printed, logged, or stored. Receiving frames
is NOT equivalent to verified quotes or tradability.
"""
import argparse
import asyncio
import csv
import datetime as dt
import json
import os
import re
import ssl
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
VENUES = {'KRX':'H0STASP0', 'NXT':'H0NXASP0'}
CODE = re.compile(r'^[0-9]{6}$')
WS_URL = 'ws://ops.koreainvestment.com:21000'  # official production endpoint
APPROVAL_URL = 'https://openapi.koreainvestment.com:9443/oauth2/Approval'

def safe_flags():
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('ORDER_FLAGS_MUST_BE_FALSE')

def candidates(csv_path, maximum=5):
    with Path(csv_path).open(encoding='utf-8-sig',newline='') as fp:
        reader=csv.DictReader(fp)
        if not {'date','ticker'}.issubset(reader.fieldnames or []):
            raise ValueError('INVALID_CANDIDATE_HEADERS')
        rows=list(reader)
    if not rows: return []
    today=dt.datetime.now(KST).date()
    result=[]
    for row in rows:
        ticker=row['ticker']
        if not CODE.fullmatch(ticker or ''): raise ValueError('INVALID_TICKER')
        day=dt.date.fromisoformat(row['date'])
        if day>today or (today-day).days>4: raise ValueError('STALE_CANDIDATE_DATE')
        if ticker not in result: result.append(ticker)
    if len(result)>maximum: raise ValueError('TOO_MANY_CANDIDATES')
    return result

def subscribe_message(approval, channel, ticker):
    if channel not in VENUES.values() or not CODE.fullmatch(ticker):
        raise ValueError('INVALID_SUBSCRIPTION')
    return {'header': {'approval_key':approval,'custtype':'P','tr_type':'1','content-type':'utf-8'},
            'body':{'input':{'tr_id':channel,'tr_key':ticker}}}

def frame_type(frame):
    if not isinstance(frame,str) or len(frame)>100000: return 'INVALID'
    if frame.startswith('0|') or frame.startswith('1|'):
        parts=frame.split('|',3)
        if len(parts)!=4 or parts[1] not in VENUES.values(): return 'UNRECOGNIZED'
        return 'MARKET_DATA_'+('KRX' if parts[1]==VENUES['KRX'] else 'NXT')
    if frame.startswith('{'): return 'CONTROL'
    return 'UNRECOGNIZED'

def approval_key():
    import requests
    key=os.environ.get('KIS_APP_KEY','').strip()
    secret=os.environ.get('KIS_APP_SECRET','').strip()
    if not key or not secret: raise RuntimeError('MISSING_KIS_SECRETS')
    response=requests.post(APPROVAL_URL,headers={'content-type':'application/json'},
        json={'grant_type':'client_credentials','appkey':key,'secretkey':secret},timeout=12)
    if response.status_code!=200: raise RuntimeError('APPROVAL_HTTP_'+str(response.status_code))
    approval=response.json().get('approval_key')
    if not isinstance(approval,str) or not approval: raise RuntimeError('APPROVAL_KEY_MISSING')
    return approval

async def collect(tickers, seconds):
    import websockets
    approval=approval_key()
    counts=Counter();seen_channels=set()
    # TLS is not claimed: the KIS portal documents ws:// endpoint.
    async with websockets.connect(WS_URL,open_timeout=10,close_timeout=3,max_size=100000,ping_interval=20) as ws:
        for ticker in tickers:
            for channel in VENUES.values():
                await ws.send(json.dumps(subscribe_message(approval,channel,ticker),separators=(',',':')))
                counts['subscriptions_sent']+=1
        end=asyncio.get_running_loop().time()+seconds
        while asyncio.get_running_loop().time()<end:
            try:
                frame=await asyncio.wait_for(ws.recv(),timeout=min(2,max(.1,end-asyncio.get_running_loop().time())))
            except asyncio.TimeoutError: continue
            kind=frame_type(frame)
            counts[kind]+=1
            if kind.startswith('MARKET_DATA_'): seen_channels.add(kind.rsplit('_',1)[1])
    return {'counts':dict(counts),'venues_with_frames':sorted(seen_channels)}

def run(path, output, *, network=False, seconds=12):
    safe_flags()
    ticker_list=candidates(path)
    result={'version':'5.16','mode':'KIS_WS_DIAGNOSTIC_NO_ORDERS','gate':'DENY',
        'ticker_count':len(ticker_list),'network_requested':network,'status':'OFFLINE_ONLY',
        'counts':{},'venues_with_frames':[], 'raw_prices_saved':False,
        'venue_quote_schema_verified':False,'quote_fresh_verified':False,'priced_verified':0,
        'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
    if network and ticker_list:
        try:
            observed=asyncio.run(collect(ticker_list,seconds))
            result.update(observed,status='FRAMES_OBSERVED_UNVERIFIED' if observed['venues_with_frames'] else 'NO_MARKET_DATA_FRAMES')
        except Exception as exc:
            # Strictly bounded diagnostics; no exception text or tracebacks (potential secrets).
            allowed={'TimeoutError','ConnectionClosedError','InvalidStatus','InvalidHandshake','ConnectionRefusedError'}
            kind=type(exc).__name__
            result['status']='BROKER_CONNECTION_BLOCKED'
            result['error_type']=kind if kind in allowed else 'OTHER'
    elif network:
        result['status']='NO_CANDIDATES'
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"V5.16 GATE=DENY; status={result['status']}; candidates={len(ticker_list)}; "
          f"counts={result['counts']}; priced_verified=0; orders_sent=0; "
          'LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--candidates',default='data/v4b/candidates.csv')
    parser.add_argument('--output',default='v5_16_report.json')
    parser.add_argument('--network',action='store_true')
    parser.add_argument('--seconds',type=int,default=12)
    args=parser.parse_args()
    if not 3<=args.seconds<=25: parser.error('seconds must be 3..25')
    run(args.candidates,args.output,network=args.network,seconds=args.seconds)
