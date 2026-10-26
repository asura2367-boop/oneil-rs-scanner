#!/usr/bin/env python3
"""Persistent switch for PAPER/LIVE-preview automation. NEVER authorizes an order."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PATH=Path('data/live/auto_control.json')

def switch(mode, previous=None):
    if mode not in ('on','off'): raise ValueError('invalid mode')
    if os.environ.get('LIVE_ENABLED','false').lower() != 'false' or os.environ.get('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise RuntimeError('LIVE_ORDER_SWITCH_MUST_REMAIN_FALSE')
    return {'schema_version':1, 'analysis_enabled':mode=='on', 'real_order_enabled':False,
            'orders_authorized':False, 'changed_utc':datetime.now(timezone.utc).isoformat(),
            'description':'ON schedules read-only analysis only; never submits an order'}

def read(path=PATH):
    if not path.exists(): return {'analysis_enabled':False,'real_order_enabled':False}
    s=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(s,dict) or type(s.get('analysis_enabled')) is not bool or s.get('real_order_enabled') is not False:
        raise RuntimeError('UNSAFE_OR_INVALID_STATE')
    return s

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['on','off','status'],required=True)
    p.add_argument('--path',type=Path,default=PATH);a=p.parse_args()
    if a.mode=='status':
        s=read(a.path);print('AUTO_ANALYSIS='+('ON' if s['analysis_enabled'] else 'OFF')+'; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false; orders_sent=0');return
    s=switch(a.mode);a.path.parent.mkdir(parents=True,exist_ok=True)
    tmp=a.path.with_suffix('.json.tmp');tmp.write_text(json.dumps(s,indent=2)+'\n',encoding='utf-8');tmp.replace(a.path)
    print('AUTO_ANALYSIS='+a.mode.upper()+'; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false; orders_sent=0')

if __name__=='__main__':main()
