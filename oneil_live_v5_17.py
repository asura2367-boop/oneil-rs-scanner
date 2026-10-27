#!/usr/bin/env python3
"""V5.17 KIS real-time quote decoding diagnostic. NO ORDERS, NO PRICE APPROVAL.
Source mapping: KIS official domestic_stock_functions_ws.py asking_price_krx/nxt.
Do not save raw frames, keys, account data or prices. """
import argparse
import asyncio
import datetime as dt
import json
import os
import re
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

from oneil_live_v5_16 import (approval_key, candidates, subscribe_message,
                              VENUES, WS_URL, safe_flags)
KST = ZoneInfo('Asia/Seoul')
CODE = re.compile(r'^[0-9]{6}$')
# The official KIS WS KRX/NXT asking-price examples share this field prefix.
PREFIX = ['MKSC_SHRN_ISCD','BSOP_HOUR','HOUR_CLS_CODE']
PREFIX += [f'ASKP{i}' for i in range(1,11)]
PREFIX += [f'BIDP{i}' for i in range(1,11)]
PREFIX += [f'ASKP_RSQN{i}' for i in range(1,11)]
PREFIX += [f'BIDP_RSQN{i}' for i in range(1,11)]
# Schema contains additional fields; reject truncated frames even if prefix is present.
MIN_FIELD_COUNT = 59

def parse_frame(frame, expected_codes, received_at, *, max_delay_s=5, max_spread_bps=80):
    """Sanitized categorization only, never authorizes order or returns prices."""
    def out(code,venue='UNKNOWN',ticker='UNKNOWN'):
        return {'status':code,'venue':venue,'ticker':ticker, 'price_approved':False}
    if not isinstance(frame,str) or len(frame)>100000: return out('INVALID_FRAME')
    parts=frame.split('|',3)
    if len(parts)!=4 or parts[0]!='0' or parts[1] not in VENUES.values():
        return out('CONTROL_OR_UNKNOWN')
    venue=next(v for v,tr in VENUES.items() if tr==parts[1])
    try:
        count=int(parts[2]);fields=parts[3].split('^')
        if count!=1 or len(fields)<MIN_FIELD_COUNT: return out('SCHEMA_COUNT_UNVERIFIED',venue)
        m=dict(zip(PREFIX,fields))
        ticker=m['MKSC_SHRN_ISCD']
        if not CODE.fullmatch(ticker) or ticker not in expected_codes: return out('UNKNOWN_SYMBOL',venue)
        hhmmss=m['BSOP_HOUR']
        if not re.fullmatch(r'[0-2][0-9][0-5][0-9][0-5][0-9]',hhmmss): return out('INVALID_EXCHANGE_TIME',venue,ticker)
        h,mi,s=int(hhmmss[:2]),int(hhmmss[2:4]),int(hhmmss[4:])
        if h>23: return out('INVALID_EXCHANGE_TIME',venue,ticker)
        local=received_at.astimezone(KST)
        exchange_time=local.replace(hour=h,minute=mi,second=s,microsecond=0)
        # No trading-date field; midnight/overnight ambiguity cannot be certified.
        delay=(local-exchange_time).total_seconds()
        if delay < -1 or delay>max_delay_s: return out('STALE_OR_FUTURE_EXCHANGE_TIME',venue,ticker)
        ask,bid=int(m['ASKP1']),int(m['BIDP1'])
        ask_qty,bid_qty=int(m['ASKP_RSQN1']),int(m['BIDP_RSQN1'])
        if min(ask,bid,ask_qty,bid_qty)<=0: return out('EMPTY_OR_INVALID_BOOK',venue,ticker)
        if ask<bid: return out('CROSSED_BOOK',venue,ticker)
        spread=10000*(ask-bid)/((ask+bid)/2)
        if spread>max_spread_bps: return out('SPREAD_TOO_WIDE',venue,ticker)
        # Source phase/session and KIS full schema need separate certification.
        return out('PLAUSIBLE_BOOK_UNATTESTED',venue,ticker)
    except (ValueError,TypeError,OverflowError,KeyError):
        return out('INVALID_QUOTE_FIELD',venue)

async def collect(tickers, seconds):
    import websockets
    approval=approval_key()
    counts=Counter()
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
            status=parse_frame(frame,set(tickers),dt.datetime.now(KST))
            counts[status['status']]+=1
            # never persist raw payload, ticker, price, or key
    return dict(counts)

def run(source, output, *, network=False, seconds=12):
    safe_flags()
    codes=candidates(source)
    result={'version':'5.17','status':'OFFLINE_ONLY','gate':'DENY','candidates':len(codes),
            'counts':{},'quote_schema_fully_attested':False,
            'quote_market_phase_verified':False,'price_approved_count':0,
            'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
    if network and codes:
        try:
            result['counts']=asyncio.run(collect(codes,seconds))
            result['status']='DECODED_UNATTESTED'
        except Exception as exc:
            result['status']='NETWORK_READ_BLOCKED'
            result['error_type']=type(exc).__name__ if type(exc).__name__ in {'TimeoutError','InvalidStatus','ConnectionClosedError','ConnectionRefusedError'} else 'OTHER'
    elif network: result['status']='NO_CANDIDATES'
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"V5.17 GATE=DENY; status={result['status']}; candidates={len(codes)}; counts={result['counts']}; "
          'price_approved_count=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--candidates',default='data/v4b/candidates.csv')
    p.add_argument('--output',default='v5_17_report.json')
    p.add_argument('--network',action='store_true')
    p.add_argument('--seconds',type=int,default=12)
    a=p.parse_args()
    if not 3<=a.seconds<=25: p.error('seconds must be 3..25')
    run(a.candidates,a.output,network=a.network,seconds=a.seconds)
