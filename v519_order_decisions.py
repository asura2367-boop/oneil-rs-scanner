"""Convert same-run KIS candidate review into a strictly non-executable order plan.
Existing KIS reference prices are not executable quotes. No broker writes.
"""
import argparse,json
from pathlib import Path
CAPITAL=1_000_000
RESERVE=400_000
TRADING=600_000
STOCK_LIMIT=180_000
def decide(report):
    if report.get('status')!='READ_ONLY_BROKER_REVIEW' or report.get('gate')!='DENY' or report.get('orders_sent')!=0:
        raise ValueError('UNVERIFIED_REVIEW')
    candidates=report.get('candidates')
    if not isinstance(candidates,list):raise ValueError('CANDIDATES_MISSING')
    remaining=TRADING
    orders=[]
    seen=set()
    for c in sorted(candidates,key=lambda x:-x['rs']):
        ticker=c.get('ticker');price=c.get('reference_price');qty=c.get('theoretical_max_qty')
        if not isinstance(ticker,str) or len(ticker)!=6 or not ticker.isascii() or not ticker.isdigit() or ticker in seen:
            raise ValueError('INVALID_TICKER')
        seen.add(ticker)
        if type(price)!=int or price<=0 or type(qty)!=int or qty<0 or c.get('order_authorized') is not False:
            raise ValueError('INVALID_BROKER_REVIEW')
        quantity=min(qty,STOCK_LIMIT//price,remaining//price)
        remaining-=quantity*price
        orders.append({'ticker':ticker,'rs':c['rs'],'side':'BUY','reference_price':price,
                       'preview_quantity':quantity,'preview_notional':quantity*price,
                       'approved':False,'executable_price_verified':False})
    return {'status':'ORDER_PLAN_READ_ONLY','gate':'DENY','orders_sent':0,
            'limits':{'capital':CAPITAL,'reserve':RESERVE,'trading':TRADING,'per_stock':STOCK_LIMIT},
            'preview_notional_total':TRADING-remaining,'orders':orders}
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--review',required=True);p.add_argument('--output',default='v519_order_plan.json')
    a=p.parse_args()
    try:
        result=decide(json.loads(Path(a.review).read_text(encoding='utf-8')))
        exit_code=0
    except Exception:
        result={'status':'BLOCKED','gate':'DENY','orders_sent':0,'orders':[]}
        exit_code=2
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\\n',encoding='utf-8')
    print('V519_PLAN='+result['status']+'; orders_sent=0')
    raise SystemExit(exit_code)
if __name__=='__main__':main()
