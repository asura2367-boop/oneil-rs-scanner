#!/usr/bin/env python3
"""V4.3 NO-ORDER journal/state machine. Never calls any brokerage API.

Operational mode only imports intent IDs from V4.2 as PREVIEW_ONLY.
Transitions to submitted/fill/cancel are *simulations only* in tests and
are not accepted from live workflow. No execution claims are made.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ID = re.compile(r'^[0-9a-f]{20}$')
TICKER = re.compile(r'^\d{6}$')
STATES = {'PREVIEW_ONLY','SEND_BLOCKED','SUBMISSION_UNKNOWN','ACKNOWLEDGED',
          'PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED','HALTED'}
ALLOWED = {
    'PREVIEW_ONLY': {'SEND_BLOCKED','HALTED'},
    'SEND_BLOCKED': {'HALTED'},
    'SUBMISSION_UNKNOWN': {'ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED','HALTED'},
    'ACKNOWLEDGED': {'PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED','HALTED'},
    'PARTIALLY_FILLED': {'PARTIALLY_FILLED','FILLED','CANCELLED','HALTED'},
    'FILLED': set(), 'CANCELLED': set(), 'REJECTED': set(), 'HALTED': set(),
}

class Blocked(Exception):
    pass


def guard():
    if os.getenv('LIVE_ENABLED','false').lower() != 'false':
        raise Blocked('LIVE_ENABLED_MUST_BE_FALSE')
    if os.getenv('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise Blocked('KIS_ALLOW_ORDER_MUST_BE_FALSE')


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def validate_ledger(ledger):
    if not isinstance(ledger,dict) or ledger.get('schema_version')!=1 or not isinstance(ledger.get('recorded'),dict):
        raise Blocked('INVALID_V4_2_LEDGER')
    records=ledger['recorded']
    if len(records)>10000:
        raise Blocked('LEDGER_TOO_LARGE')
    for ident, obj in records.items():
        if not isinstance(ident,str) or not ID.fullmatch(ident):
            raise Blocked('INVALID_INTENT_ID')
        if not isinstance(obj,dict) or not TICKER.fullmatch(str(obj.get('ticker',''))):
            raise Blocked('INVALID_TICKER')
        if obj.get('state') != 'PREVIEW_RECORDED_NO_ORDER':
            raise Blocked('UNSUPPORTED_LEDGER_STATE')
    return records


def initialize(ledger, previous=None, halted=False):
    records=validate_ledger(ledger)
    if previous is None:
        previous={'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER', 'halted':False, 'intents':{}}
    if (not isinstance(previous,dict) or previous.get('schema_version') != 1
        or previous.get('mode') != 'NO_ORDER_STATE_TRACKER'
        or not isinstance(previous.get('intents'),dict)
        or not isinstance(previous.get('halted'),bool)):
        raise Blocked('INVALID_STATE_FILE')
    intents={k:dict(v) for k,v in previous['intents'].items()}
    for ident, row in intents.items():
        if not ID.fullmatch(ident) or not isinstance(row,dict) or row.get('state') not in STATES:
            raise Blocked('INVALID_PREVIOUS_INTENT')
        if not TICKER.fullmatch(str(row.get('ticker',''))):
            raise Blocked('INVALID_PREVIOUS_TICKER')
        if ident in records and row['ticker'] != records[ident]['ticker']:
            raise Blocked('INTENT_ID_CONFLICT')
    is_halted = previous['halted'] or halted
    for ident, row in records.items():
        if ident not in intents:
            intents[ident]={'ticker':row['ticker'], 'state':'HALTED' if is_halted else 'PREVIEW_ONLY',
                            'source':'V4_2_PREVIEW', 'broker_order_id':None, 'filled_qty':0}
    return {'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER','halted':is_halted,'intents':intents}


def simulated_transition(record, new_state, *, filled_qty=0, simulation=False):
    """For isolated offline tests. Not invoked by the workflow or main()."""
    if not simulation:
        raise Blocked('BROKER_TRANSITIONS_DISABLED')
    if not isinstance(record,dict) or record.get('state') not in ALLOWED:
        raise Blocked('INVALID_STATE')
    if new_state not in ALLOWED[record['state']]:
        raise Blocked('ILLEGAL_TRANSITION')
    if not isinstance(filled_qty,int) or isinstance(filled_qty,bool) or filled_qty<0:
        raise Blocked('INVALID_FILLED_QUANTITY')
    old_qty=record.get('filled_qty',0)
    if filled_qty<old_qty:
        raise Blocked('FILL_QUANTITY_REGRESSION')
    if new_state in ('PARTIALLY_FILLED','FILLED') and filled_qty<=0:
        raise Blocked('MISSING_FILL_QUANTITY')
    return {**record,'state':new_state,'filled_qty':filled_qty, 'simulation_only':True}


def write_atomic(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--ledger',default='data/live/v4_2_ledger.json')
    p.add_argument('--state',default='data/live/v4_3_state.json')
    p.add_argument('--report',default='data/live/v4_3_report.json')
    p.add_argument('--halt',action='store_true',help='Permanently halt all new intent tracking; no resume in this version')
    args=p.parse_args()
    guard()
    ledger=load_json(args.ledger)
    path=Path(args.state)
    prev=load_json(path) if path.exists() else None
    result=initialize(ledger,prev,args.halt)
    report={'mode':'V4_3_NO_ORDER_JOURNAL','live_enabled':False,'orders_sent':0,
            'broker_api_calls':0,'halted':result['halted'],
            'intent_count':len(result['intents']),
            'state_counts':{s:sum(x['state']==s for x in result['intents'].values()) for s in sorted(STATES)}}
    # Report has no ticker, account, credential or simulated fill information.
    write_atomic(path,result)
    write_atomic(Path(args.report),report)
    print('V4.3 STATE JOURNAL COMPLETE; orders_sent=0; broker_api_calls=0; LIVE_ENABLED=false')
    print('intents=',report['intent_count'],'halted=',report['halted'])

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        code=str(e) if isinstance(e,Blocked) else type(e).__name__
        print('V4.3 BLOCKED: '+code+'; orders_sent=0',file=sys.stderr)
        sys.exit(1)
