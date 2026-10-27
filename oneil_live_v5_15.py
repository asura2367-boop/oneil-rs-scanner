#!/usr/bin/env python3
"""V5.15 conservative validator for externally normalized KIS WebSocket quotes. NO ORDERS.

No network connection, token access, broker order, pricing or buying-power call.
An adapter must independently validate wire schema before supplying these records.
"""
import argparse
import datetime as dt
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
VENUE_CHANNEL = {'KRX': 'H0STASP0', 'NXT': 'H0NXASP0'}
CODE = re.compile(r'^[0-9]{6}$')

def _time(value):
    t=dt.datetime.fromisoformat(value)
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError('timestamp missing offset')
    return t.astimezone(dt.timezone.utc)

def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise ValueError('invalid quote')
    d=Decimal(str(value))
    if not d.is_finite() or d<=0: raise ValueError('invalid quote')
    return d

def verify(observation, now, *, max_age_s=5, max_spread_bps=80):
    """Trusted evidence is required but cannot be established from an arbitrary JSON file."""
    codes=[]
    def fail(s):
        if s not in codes: codes.append(s)
    try:
        if not isinstance(observation,dict): raise ValueError('bad record')
        code=observation.get('ticker')
        venue=observation.get('venue')
        channel=observation.get('channel')
        if not isinstance(code,str) or not CODE.fullmatch(code): fail('INVALID_TICKER')
        if venue not in VENUE_CHANNEL or channel != VENUE_CHANNEL.get(venue): fail('VENUE_CHANNEL_MISMATCH')
        # Reporter-origin attestations cannot certify source legitimacy. Need verified streaming adapter.
        if observation.get('source_verified') is not True: fail('SOURCE_NOT_ATTESTED')
        if observation.get('schema_verified') is not True: fail('WIRE_SCHEMA_NOT_VERIFIED')
        ts=_time(observation['exchange_timestamp'])
        recv=_time(observation['received_at'])
        now=_time(now.isoformat()) if isinstance(now,dt.datetime) else _time(now)
        if recv < ts or (recv-ts).total_seconds()>max_age_s: fail('RECEIVE_EXCHANGE_SKEW')
        age=(now-recv).total_seconds()
        if age < -1 or age > max_age_s: fail('QUOTE_STALE_OR_FUTURE')
        bid,ask=_money(observation['best_bid']),_money(observation['best_ask'])
        if ask<bid: fail('CROSSED_BOOK')
        elif (ask-bid)/((ask+bid)/2)*10000 > max_spread_bps: fail('SPREAD_TOO_WIDE')
        if int(observation.get('bid_qty',0))<=0 or int(observation.get('ask_qty',0))<=0:
            fail('EMPTY_BOOK')
        if observation.get('market_phase') not in ('REGULAR','NXT_AFTER'):
            fail('UNVERIFIED_MARKET_PHASE')
        if venue=='KRX' and observation.get('market_phase')!='REGULAR':
            fail('INVALID_KRX_PHASE')
        # Policy: never auto-certify venue/clock from a JSON or read-only REST reply.
        fail('LIVE_FEED_ATTESTATION_NOT_IMPLEMENTED')
    except (KeyError,ValueError,TypeError,InvalidOperation,OverflowError):
        fail('INCOMPLETE_OR_INVALID_OBSERVATION')
    return {'ticker':observation.get('ticker') if isinstance(observation,dict) else None,
            'venue':observation.get('venue') if isinstance(observation,dict) else None,
            'review_status':'DENY','reason_codes':codes,
            'quote_fresh_verified':False,'price_approved':False,'orders_sent':0}

def run(input_file, output_file, now):
    if (os.environ.get('LIVE_ENABLED','false').lower()!='false' or
        os.environ.get('KIS_ALLOW_ORDER','false').lower()!='false'):
        raise RuntimeError('LIVE_ORDER_FLAGS_NOT_DISABLED')
    if input_file is None:
        result=[];reason='NO_TRUSTED_STREAM_INPUT'
    else:
        data=json.loads(Path(input_file).read_text(encoding='utf-8'))
        if not isinstance(data,list) or len(data)>25: raise ValueError('INVALID_INPUT')
        result=[verify(x,now) for x in data];reason='UNATTESTED_SAMPLE_ONLY'
    report={'version':'5.15','gate':'DENY','mode':'NO_ORDER_QUOTE_DIAGNOSTIC',
            'source_status':reason,'observations':result,'priced_verified':0,
            'buying_power_verified':0,'daily_loss_verified':False,
            'orders_sent':0,'broker_api_calls':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
    Path(output_file).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'V5.15 GATE=DENY; source_status={reason}; records={len(result)}; priced_verified=0; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output',default='v5_15_report.json');a=p.parse_args()
    run(a.input,a.output,dt.datetime.now(KST))
