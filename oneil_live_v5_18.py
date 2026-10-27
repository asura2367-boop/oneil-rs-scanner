#!/usr/bin/env python3
"""V5.18 conservative WebSocket quote diagnostics. No orders or approved prices."""
import argparse
import asyncio
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from oneil_live_v5_16 import approval_key, candidates, subscribe_message, VENUES, WS_URL, safe_flags
from oneil_live_v5_17 import parse_frame

KST=ZoneInfo('Asia/Seoul')

def phase(venue, now):
    """Conservative indicative venue window, NOT exchange calendar certification."""
    t=now.astimezone(KST)
    if t.weekday()>=5: return 'CLOSED_OR_UNVERIFIED'
    mins=t.hour*60+t.minute
    if venue=='KRX' and 540 <= mins < 930: return 'REGULAR_WINDOW_ONLY'
    if venue=='NXT' and 540 <= mins < 930: return 'REGULAR_WINDOW_ONLY'
    if venue=='NXT' and 940 <= mins < 1200: return 'EVENING_WINDOW_ONLY'
    return 'CLOSED_OR_UNVERIFIED'

def check(frame, expected, received_at, seen):
    """Sanitized classification. seen holds metadata IDs only, never prices."""
    v=parse_frame(frame,expected,received_at,max_delay_s=5,max_spread_bps=80)
    if v['status']!='PLAUSIBLE_BOOK_UNATTESTED': return v['status'],v['venue'],v['ticker']
    venue,ticker=v['venue'],v['ticker']
    if phase(venue,received_at)=='CLOSED_OR_UNVERIFIED': return 'VENUE_WINDOW_UNVERIFIED',venue,ticker
    # Exchange business time is a clock without date. It cannot prove session phase.
    try:
        fields=frame.split('|',3)[3].split('^')
        hhmmss=fields[1]
        ident=(venue,ticker)
        prev=seen.get(ident)
        if prev is not None and hhmmss==prev: return 'DUPLICATE_EXCHANGE_SECOND',venue,ticker
        if prev is not None and hhmmss<prev: return 'OUT_OF_ORDER_EXCHANGE_SECOND',venue,ticker
        seen[ident]=hhmmss
    except (IndexError,ValueError,TypeError):
        return 'UNVERIFIED_FRAME_ID',venue,ticker
    return 'PLAUSIBLE_UNATTESTED_SESSION',venue,ticker

async def observe(tickers,seconds):
    import websockets
    counts=Counter(); by_venue=Counter(); distinct=set();seen={}
    approval=approval_key()
    async with websockets.connect(WS_URL,open_timeout=10,close_timeout=3,max_size=100000,ping_interval=20) as ws:
        for ticker in tickers:
            for channel in VENUES.values():
                await ws.send(json.dumps(subscribe_message(approval,channel,ticker),separators=(',',':')))
                counts['SUBSCRIPTIONS_SENT']+=1
        end=asyncio.get_running_loop().time()+seconds
        while asyncio.get_running_loop().time()<end:
            try: frame=await asyncio.wait_for(ws.recv(),timeout=min(2,max(.1,end-asyncio.get_running_loop().time())))
            except asyncio.TimeoutError: continue
            status,venue,ticker=check(frame,set(tickers),dt.datetime.now(KST),seen)
            counts[status]+=1
            if status=='PLAUSIBLE_UNATTESTED_SESSION':
                by_venue[venue]+=1
                distinct.add((venue,ticker))
    return dict(counts),dict(by_venue),len(distinct)

def run(source,output,*,network=False,seconds=12):
    safe_flags()
    # Enforce hard deny on any accidentally enabled environment flag.
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('LIVE_FLAGS_NOT_DISABLED')
    tickers=candidates(source)
    doc={'version':'5.18','generated_at_kst':dt.datetime.now(KST).isoformat(timespec='seconds'),'gate':'DENY','mode':'QUOTE_DIAGNOSTIC_ONLY',
         'status':'OFFLINE_ONLY','candidates':len(tickers),'counts':{},'venue_counts':{},
         'distinct_symbol_venue_pairs':0,'exchange_business_day_attested':False,
         'exchange_session_phase_attested':False,'quote_schema_fully_attested':False,
         'quote_price_approved_count':0,'daily_loss_verified':False,
         'broker_buying_power_verified':False,'orders_sent':0,'broker_order_api_calls':0,
         'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
    if network and tickers:
        try:
            c,v,n=asyncio.run(observe(tickers,seconds))
            doc.update(status='OBSERVED_UNATTESTED',counts=c,venue_counts=v,distinct_symbol_venue_pairs=n)
        except Exception as exc:
            doc['status']='NETWORK_READ_BLOCKED'
            doc['error_type']=type(exc).__name__ if type(exc).__name__ in {'TimeoutError','InvalidStatus','ConnectionClosedError','ConnectionRefusedError'} else 'OTHER'
    elif network:doc['status']='NO_CANDIDATES'
    dest=Path(output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V5.18 GATE=DENY; status='+doc['status']+'; candidates='+str(len(tickers))+
          '; counts='+json.dumps(doc['counts'],sort_keys=True)+'; venue_counts='+json.dumps(doc['venue_counts'],sort_keys=True)+
          '; quote_price_approved_count=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    return doc

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--candidates',default='data/v4b/candidates.csv')
    p.add_argument('--output',default='v5_18_report.json')
    p.add_argument('--network',action='store_true')
    p.add_argument('--seconds',type=int,default=12)
    a=p.parse_args()
    if not 3<=a.seconds<=25:p.error('seconds must be 3..25')
    run(a.candidates,a.output,network=a.network,seconds=a.seconds)
