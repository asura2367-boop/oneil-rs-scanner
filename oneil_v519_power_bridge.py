#!/usr/bin/env python3
"""Generate a sanitized, hard-DENY power report from LIVE preview candidates.

No KIS requests, raw account figures, or order price inference.
"""
import argparse, datetime as dt, json, os
from pathlib import Path
from zoneinfo import ZoneInfo
from oneil_buying_power_guard import examine
KST=ZoneInfo('Asia/Seoul')
def build(path):
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('HARD_DENY_FLAGS_REQUIRED')
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('gate')!='DENY' or data.get('orders_sent')!=0 or data.get('mode')!='LIVE_INTENTS_PREVIEW_ONLY':
            raise ValueError('INVALID_SOURCE')
        intents=data.get('intents')
        if not isinstance(intents,list): raise ValueError('INVALID_INTENTS')
        rows=[]
        for item in intents:
            if not isinstance(item,dict) or item.get('status')!='REVIEW_ONLY_UNPRICED' or item.get('quantity') is not None or item.get('price_krw') is not None:
                raise ValueError('UNVERIFIED_PRICE')
            rows.append({'ticker':item.get('ticker'), 'query_price':None,
                         'broker_orderable_cash':None, 'broker_orderable_qty':None})
        return examine(rows, now=dt.datetime.now(KST),held=None,unfilled=None,
                       price_attested=False,daily_loss_verified=False,baseline_verified=False)
    except (OSError,TypeError,ValueError,KeyError,json.JSONDecodeError):
        return examine([],now=dt.datetime.now(KST)) | {'source':'CANDIDATE_EVIDENCE_INVALID','candidate_input_verified':False}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--intents',default='v5_11_review.json');ap.add_argument('--output',default='v5_19_power_report.json');args=ap.parse_args()
    r=build(args.intents)
    Path(args.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'V5.19 POWER GATE=DENY; candidates={r["candidate_count"]}; broker_buying_power_verified=false; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
