#!/usr/bin/env python3
"""KIS buying-power and existing-position checks. READ ONLY, NO ORDER ENDPOINTS."""
from __future__ import annotations
import json, os, sys, urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from live_trade import make_plan

HOST = 'https://openapi.koreainvestment.com:9443'
TOTAL_CAP = 1_000_000
PER_STOCK_CAP = 200_000

def request(method, path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data,
        headers={'Content-Type': 'application/json; charset=utf-8', **(headers or {})}, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

def unsigned_int(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            return None
        return int(number)
    except (ValueError, InvalidOperation, TypeError):
        return None

def require_ok(body, what):
    if str(body.get('rt_cd')) != '0':
        raise RuntimeError(f'{what} rejected (code: {str(body.get("msg_cd", "unknown"))[:24]})')
    return body

def credentials():
    if os.getenv('LIVE_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('HARD STOP: LIVE_ENABLED must be false')
    key = os.environ['KIS_APP_KEY'].strip()
    secret = os.environ['KIS_APP_SECRET'].strip()
    cano = os.environ['KIS_ACCOUNT_NO'].strip()
    product = os.environ['KIS_ACCOUNT_PRODUCT'].strip()
    if not key or not secret or len(cano) != 8 or not cano.isascii() or not cano.isdigit() or len(product) != 2 or not product.isascii() or not product.isdigit():
        raise ValueError('Invalid credentials or account number format')
    return key, secret, cano, product

def get_balance(key, secret, cano, product, token):
    args = {'CANO':cano,'ACNT_PRDT_CD':product,'AFHR_FLPR_YN':'N','OFL_YN':'',
            'INQR_DVSN':'00','UNPR_DVSN':'01','FUND_STTL_ICLD_YN':'N',
            'FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
            'CTX_AREA_FK100':'','CTX_AREA_NK100':''}
    h = {'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'tr_id':'TTTC8434R','custtype':'P'}
    result = require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-balance?'+urlencode(args),headers=h),'Balance')
    if not isinstance(result.get('output1'), list):
        raise RuntimeError('Balance holdings missing')
    held = set()
    for r in result['output1']:
        qty = unsigned_int(r.get('hldg_qty'))
        code = str(r.get('pdno','')).strip()
        if qty is None:
            raise RuntimeError('Invalid holdings quantity')
        if qty > 0 and len(code) == 6 and code.isascii() and code.isdigit():
            held.add(code)
        elif qty > 0:
            raise RuntimeError('Unknown held stock code: stop safely')
    return held

def get_buying_power(key, secret, cano, product, token, ticker, price):
    params = {'CANO':cano,'ACNT_PRDT_CD':product,'PDNO':ticker,'ORD_UNPR':str(price),
              'ORD_DVSN':'00','CMA_EVLU_AMT_ICLD_YN':'N','OVRS_ICLD_YN':'N'}
    h = {'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'tr_id':'TTTC8908R','custtype':'P'}
    body = require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-psbl-order?'+urlencode(params),headers=h), 'Buying power')
    output = body.get('output')
    if not isinstance(output,dict):
        raise RuntimeError('Missing buying power output')
    amt = unsigned_int(output.get('nrcvb_buy_amt'))  # no margin, conservative
    qty = unsigned_int(output.get('nrcvb_buy_qty'))
    cash = unsigned_int(output.get('ord_psbl_cash'))
    if amt is None or qty is None or cash is None:
        raise RuntimeError('Incomplete buying power response')
    return min(amt,cash),qty

def evaluate(plans, held, lookup):
    """Pure function. Any missing/failed lookup blocks buys; output contains no account details."""
    remaining = TOTAL_CAP
    decisions=[]
    for row in plans:
        ticker=row['ticker']; price=unsigned_int(row.get('reference_close'))
        status=row.get('status','BLOCKED_UNKNOWN'); qty=0; amount=0
        if ticker in held:
            status='BLOCKED_ALREADY_HELD'
        elif status != 'PREVIEW_ONLY' or price is None or price == 0:
            status='BLOCKED_BAD_OR_STALE_PREVIEW'
        else:
            try:
                buying_amt,buying_qty = lookup(ticker,price)
                if buying_amt < price or buying_qty <= 0:
                    status='BLOCKED_INSUFFICIENT_BUYING_POWER'
                else:
                    qty = min(int(row.get('preview_qty',0)), buying_qty,
                              buying_amt//price, PER_STOCK_CAP//price, remaining//price)
                    if qty > 0:
                        amount=qty*price
                        remaining-=amount
                        status='ELIGIBLE_PREVIEW_NO_ORDER'
                    else:
                        status='BLOCKED_LIMIT_OR_FUNDS'
            except (RuntimeError, ValueError, TypeError):
                status='BLOCKED_BUYING_POWER_CHECK_FAILED'
        decisions.append({'ticker':ticker,'status':status,'reference_price_krw':price or 0,
                          'preview_qty':qty,'preview_amount_krw':amount})
    return decisions

def main():
    key,secret,cano,product=credentials()
    # Read-only plan relies on local paper files; does not mutate them.
    plan=make_plan(Path('data/paper/state.json'),Path('data/paper/paper_summary.json'))
    token_data=request('POST','/oauth2/tokenP',{'grant_type':'client_credentials','appkey':key,'appsecret':secret})
    token=token_data.get('access_token')
    if not token: raise RuntimeError('Auth failed: missing access token')
    held=get_balance(key,secret,cano,product,token)
    decisions=evaluate(plan['plans'],held,lambda ticker,price: get_buying_power(key,secret,cano,product,token,ticker,price))
    # Report is deliberately not persisted or uploaded to GitHub. The logs have no account identifiers.
    print('KIS BUYING POWER READ-ONLY: SUCCESS')
    print('LIVE_ENABLED=false; order endpoints absent; orders sent: 0')
    print('Strategy total cap: 1000000 KRW; per stock cap: 200000 KRW')
    print('Candidate decisions (NO orders):')
    for d in decisions:
        print(d['ticker'],d['status'],'preview_qty=',d['preview_qty'],'preview_amount_krw=',d['preview_amount_krw'])
    print('NOTE: Reference close is NOT a live quote; eligibility is not a trade authorization.')

if __name__=='__main__':
    try:
        main()
    except Exception as ex:
        # Never log raw exception messages from HTTP responses or credentials.
        print('KIS READ-ONLY FAILED:',type(ex).__name__,file=sys.stderr)
        sys.exit(1)
