#!/usr/bin/env python3
"""V5.10: review candidate signals against read-only KIS evidence. NEVER place orders."""
import argparse, datetime as dt, json, os, subprocess, sys
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')

def compile_report(candidate, broker, *, broker_requested=False):
    valid_candidate=(isinstance(candidate,dict) and candidate.get('status')=='DENY'
                     and candidate.get('source')=='V4B_CSV_UNVERIFIED'
                     and candidate.get('orders_sent')==0
                     and isinstance(candidate.get('review_candidates'),list))
    candidates=[]
    if valid_candidate:
        for item in candidate['review_candidates'][:10]:
            if isinstance(item,dict) and isinstance(item.get('ticker'),str) and len(item['ticker'])==6 and item['ticker'].isdigit() and item.get('flag')=='REVIEW_ONLY_NOT_LIVE_QUOTE':
                candidates.append({'ticker':item['ticker'],'rs':item.get('rs'),'flow_category':item.get('flow_category'),'price_verified':False,'orderable_qty':None})
    reports=broker.get('broker_report_checks',{}) if isinstance(broker,dict) else {}
    if not isinstance(reports,dict): reports={}
    child=broker.get('broker_read_status','UNKNOWN') if isinstance(broker,dict) else 'UNKNOWN'
    checks={k:reports.get(k,'MISSING') for k in ('preflight','reconciliation','gate')}
    return {'version':'5.10','generated_at_kst':dt.datetime.now(KST).isoformat(timespec='seconds'),
            'mode':'READ_ONLY_CANDIDATE_AND_ACCOUNT_EVIDENCE','gate':'DENY','orders_sent':0,
            'broker_order_api_calls':0,'live_enabled':False,'kis_allow_order':False,
            'candidate_source':'V4B_CSV_UNVERIFIED','candidate_input_valid':bool(valid_candidate),
            'candidate_count':len(candidates),'candidates':candidates,
            'broker_read_requested':bool(broker_requested),'broker_child_status':child,
            'broker_report_checks':checks,'broker_preflight_snapshot_present':checks['preflight']=='SNAPSHOT_PRESENT',
            'actual_orderable_cash_verified':False,'executable_quote_verified':False,
            'daily_loss_verified':False,'unfilled_order_live_verified':False,
            'reserve_pct':40,'trading_pct':60,'per_stock_pct_of_trading':30,
            'evening_buy_pct_of_trading':30,'actual_order_quantities':None,
            'blocking_reasons':['ORDER_SUBMISSION_NOT_IMPLEMENTED','BROKER_CASH_NOT_VERIFIED',
                'EXECUTABLE_EXCHANGE_QUOTE_NOT_VERIFIED','DAILY_LOSS_NOT_VERIFIED',
                'UNFILLED_ORDER_FRESHNESS_NOT_VERIFIED']}

def safe_read(path):
    try:
        obj=json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj,dict) else {}
    except (OSError,ValueError,TypeError): return {}

def main():
    p=argparse.ArgumentParser();p.add_argument('--broker-read',action='store_true');p.add_argument('--output',default='v5_10_report.json');a=p.parse_args()
    if any(os.getenv(x,'false').lower()!='false' for x in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise SystemExit('LIVE_FLAGS_NOT_DISABLED')
    env=dict(os.environ);env['LIVE_ENABLED']='false';env['KIS_ALLOW_ORDER']='false'
    for file in ('v5_9_candidates_review.json','v5_8_safe_report.json'):
        Path(file).unlink(missing_ok=True)
    cr=subprocess.run([sys.executable,'oneil_live_v5_9.py','--output','v5_9_candidates_review.json'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40)
    br_args=[sys.executable,'oneil_live_v5_8.py','--output','v5_8_safe_report.json']
    if a.broker_read:br_args.append('--broker-read')
    br=subprocess.run(br_args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
    c=safe_read(Path('v5_9_candidates_review.json')) if cr.returncode==0 else {}
    b=safe_read(Path('v5_8_safe_report.json')) if br.returncode==0 else {}
    result=compile_report(c,b,broker_requested=a.broker_read)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V5.10 GATE=DENY; candidates='+str(result['candidate_count'])+'; candidate_input_valid='+str(result['candidate_input_valid']).lower()+'; broker_child='+str(result['broker_child_status'])+'; preflight='+str(result['broker_report_checks']['preflight'])+'; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()
