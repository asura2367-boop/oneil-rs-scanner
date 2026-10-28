#!/usr/bin/env python3
"""V5.19 real candidate -> broker-read -> database-read integration.
Never sends orders or mutates the order ledger. Requires same-run fresh report.
"""
import argparse, datetime as dt, json, os, re
from pathlib import Path
from zoneinfo import ZoneInfo
from v519_candidate_adapter import select_candidates
from v519_pg_ledger import connect, status
KST=ZoneInfo('Asia/Seoul')

def pipeline(report, *, broker_reader=None, ledger_reader=None, now=None):
    now=now or dt.datetime.now(KST)
    selected=select_candidates(report,now=now)
    if not selected: return {'gate':'DENY','orders_sent':0,'status':'NO_ELIGIBLE_CANDIDATES','candidates':[]}
    if broker_reader is None or ledger_reader is None:
        raise RuntimeError('READERS_REQUIRED')
    ledger=ledger_reader()
    if ledger.get('halted') is not True:
        raise RuntimeError('EMERGENCY_HALT_MUST_REMAIN_ON')
    if ledger.get('unresolved') != 0:
        raise RuntimeError('UNRESOLVED_ORDERS')
    results=[]
    for candidate in selected:
        ticker=candidate['ticker']
        evidence=broker_reader(ticker)
        if evidence.get('ticker')!=ticker or evidence.get('quote') is None:
            raise RuntimeError('BROKER_EVIDENCE_MISSING')
        price=evidence['quote']
        if type(price) is not int or price<=0:raise RuntimeError('BAD_QUOTE')
        # Not an executable quote; quantity is a theoretical cap, not an order.
        qty=min(180000//price,600000//price)
        results.append({'ticker':ticker,'rs':candidate['rs'],'reference_price':price,
                        'theoretical_max_qty':qty,'order_authorized':False})
    return {'gate':'DENY','orders_sent':0,'status':'BROKER_OBSERVED_NOT_ORDERABLE',
            'risk_limit_krw':{'total':1000000,'reserve':400000,'trading':600000,'per_stock':180000},
            'candidates':results}

def live_broker_reader():
    from oneil_v519_live_runner import token,price_reference
    from kis_buying_power_readonly import credentials
    key,secret,_,_=credentials()
    tok=token(key,secret)
    return lambda ticker:{'ticker':ticker,'quote':price_reference(tok,key,secret,ticker)}

def live_ledger_reader():
    with connect() as db:return status(db)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--report',required=True)
    ap.add_argument('--output',default='v519_pipeline_result.json')
    args=ap.parse_args()
    try:
        report=json.loads(Path(args.report).read_text(encoding='utf-8'))
        result=pipeline(report,broker_reader=live_broker_reader(),ledger_reader=live_ledger_reader)
        code=0
    except Exception:
        result={'gate':'DENY','orders_sent':0,'status':'BLOCKED','candidates':[]}
        code=2
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V519_PIPELINE='+result['status']+'; orders_sent=0')
    raise SystemExit(code)
if __name__=='__main__':main()
