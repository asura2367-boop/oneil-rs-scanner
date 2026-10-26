#!/usr/bin/env python3
"""KIS V3: fail-closed, READ-ONLY trade eligibility preview. NO order endpoint."""
import datetime as dt
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from live_trade import make_plan
from kis_buying_power_readonly import (credentials, request, require_ok, get_balance,
                                       get_buying_power, unsigned_int)

KST = ZoneInfo('Asia/Seoul')
CAP = 1_000_000
STOCK_CAP = 200_000
# Even after success this program can NEVER place orders.
STAGE = 'initialization'  # predefined safe stage names only; never log credential contents

def headers(key, secret, token, tr_id):
    return {'authorization': 'Bearer '+token, 'appkey':key,
            'appsecret':secret,'tr_id':tr_id,'custtype':'P'}

def current_quote(key,secret,token,ticker,now):
    p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':ticker}
    result=require_ok(request('GET','/uapi/domestic-stock/v1/quotations/inquire-price?'+urlencode(p),
                              headers=headers(key,secret,token,'FHKST01010100')),'Quote')
    out=result.get('output')
    if not isinstance(out,dict): raise RuntimeError('Quote missing')
    price=unsigned_int(out.get('stck_prpr'))
    quote_day=str(out.get('stck_bsop_date',''))
    # API snapshot does not prove exchange-time freshness; do NOT treat as live tick.
    if now.weekday()>=5 or not (dt.time(9,0)<=now.time()<dt.time(15,30)):
        raise RuntimeError('Market session not open')
    if quote_day!=now.strftime('%Y%m%d'):
        raise RuntimeError('Quote trading date mismatch')
    if not price: raise RuntimeError('Bad quotation price')
    return price

def balance_valuation(key,secret,cano,product,token):
    p={'CANO':cano,'ACNT_PRDT_CD':product,'AFHR_FLPR_YN':'N','OFL_YN':'',
       'INQR_DVSN':'00','UNPR_DVSN':'01','FUND_STTL_ICLD_YN':'N',
       'FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
       'CTX_AREA_FK100':'','CTX_AREA_NK100':''}
    r=require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-balance?'+urlencode(p),
                         headers=headers(key,secret,token,'TTTC8434R')),'Balance')
    summary=r.get('output2')
    if not isinstance(summary,list) or not summary or not isinstance(summary[0],dict):
        raise RuntimeError('VALUATION_01: Account evaluation missing')
    # Stock evaluation is part of the 1,000,000 KRW strategy limit (conservative: all holdings).
    invested=unsigned_int(summary[0].get('scts_evlu_amt'))
    if invested is None: raise RuntimeError('VALUATION_02: Portfolio valuation missing')
    # Distinguish explicit continuation from an opaque cursor. The official KIS
    # next-page decision uses the *response header* tr_cont; this wrapper returns
    # a body dict, so a cursor by itself cannot prove paging has ended.
    # Keep fail-closed until response-header handling / bounded pagination exists.
    continuation_flag = r.get('tr_cont') in ('F', 'M')
    cursor_present = bool(str(r.get('ctx_area_fk100') or '').strip() or
                          str(r.get('ctx_area_nk100') or '').strip())
    if continuation_flag and cursor_present:
        raise RuntimeError('VALUATION_03_BOTH: Continuation and cursor present')
    if continuation_flag:
        raise RuntimeError('VALUATION_03_FLAG: Continuation flag present')
    if cursor_present:
        raise RuntimeError('VALUATION_03_CURSOR: Cursor present; header not verified')
    return invested

def unfilled_codes(key,secret,cano,product,token):
    codes=set(); fk=''; nk=''; seen=set()
    for _ in range(10):
        params={'CANO':cano,'ACNT_PRDT_CD':product,'INQR_DVSN_1':'1',
                'INQR_DVSN_2':'0','CTX_AREA_FK100':fk,'CTX_AREA_NK100':nk}
        r=require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl?'+urlencode(params),
                             headers=headers(key,secret,token,'TTTC8036R')),'Unfilled orders')
        rows=r.get('output')
        if not isinstance(rows,list): raise RuntimeError('Unfilled orders missing')
        for x in rows:
            if not isinstance(x,dict): raise RuntimeError('Bad unfilled row')
            code=str(x.get('pdno','')).strip()
            if len(code)!=6 or not code.isdigit(): raise RuntimeError('Bad unfilled ticker')
            # Conservative: any cancellable/unfilled record blocks duplicate purchases.
            codes.add(code)
        new_fk=str(r.get('ctx_area_fk100') or '').strip()
        new_nk=str(r.get('ctx_area_nk100') or '').strip()
        if not new_fk and not new_nk: return codes
        if (new_fk,new_nk) in seen: raise RuntimeError('Pagination loop')
        seen.add((new_fk,new_nk)); fk,nk=new_fk,new_nk
    raise RuntimeError('Unfilled pagination limit')

def decide(plan,held,unfilled,invested,quote_lookup,power_lookup):
    remaining=max(0,CAP-invested)
    answers=[]
    for row in plan:
        t=row['ticker']; qty=0; amount=0; status='BLOCKED'
        if t in held: status='BLOCKED_ALREADY_HELD'
        elif t in unfilled: status='BLOCKED_UNFILLED_ORDER'
        elif row.get('status')!='PREVIEW_ONLY': status='BLOCKED_SIGNAL_NOT_READY'
        elif remaining<=0: status='BLOCKED_TOTAL_CAP'
        else:
            try:
                price=quote_lookup(t)
                reference=unsigned_int(row.get('reference_close'))
                if not reference or price*100>reference*103:
                    status='BLOCKED_PRICE_GAP_GT_3PCT'
                else:
                    cash,broker_qty=power_lookup(t,price)
                    qty=min(STOCK_CAP//price,remaining//price,cash//price,broker_qty,
                            int(row.get('preview_qty') or 0))
                    if qty>0:
                        amount=qty*price; remaining-=amount
                        status='PREVIEW_ELIGIBLE_NO_ORDER'
                    else: status='BLOCKED_NO_BUYING_POWER'
            except Exception:
                status='BLOCKED_DATA_OR_API_FAILURE'
        answers.append((t,status,qty,amount))
    return answers

def main():
    global STAGE
    STAGE = 'credentials_format'
    key,secret,cano,product=credentials() # enforces LIVE_ENABLED=false
    now=dt.datetime.now(KST)
    STAGE = 'market_session'
    # Closed weekends / outside regular market hours: NOT an API failure.
    # No brokerage request and no actionable candidates are produced.
    # Weekday exchange holidays are handled by quote-date checks (fail closed).
    if now.weekday() >= 5 or not (dt.time(9,0) <= now.time() < dt.time(15,30)):
        print('V3 STATUS: MARKET_CLOSED_OR_OUTSIDE_HOURS')
        print('All candidates: BLOCKED_MARKET_CLOSED; orders sent: 0; LIVE_ENABLED=false')
        print('No brokerage request performed. Weekday holiday calendar not verified.')
        return
    STAGE = 'candidate_plan'
    plan=make_plan(Path('data/paper/state.json'),Path('data/paper/paper_summary.json'))['plans']
    if not plan:
        print('V3 READ ONLY: no pending candidates; orders sent: 0'); return
    STAGE = 'authentication'
    auth=request('POST','/oauth2/tokenP',{'grant_type':'client_credentials','appkey':key,'appsecret':secret})
    token=auth.get('access_token')
    if not token: raise RuntimeError('Authentication failed')
    STAGE = 'balance'
    held=get_balance(key,secret,cano,product,token)
    STAGE = 'valuation'
    invested=balance_valuation(key,secret,cano,product,token)
    STAGE = 'unfilled_orders'
    unfilled=unfilled_codes(key,secret,cano,product,token)
    STAGE = 'quote_and_buying_power'
    answers=decide(plan,held,unfilled,invested,
        lambda t:current_quote(key,secret,token,t,now),
        lambda t,p:get_buying_power(key,secret,cano,product,token,t,p))
    print('V3 READ ONLY: SUCCESS; LIVE_ENABLED=false; orders sent: 0')
    print('Total strategy cap: 1000000 KRW; per stock cap: 200000 KRW')
    print('Held valuation counted against cap; sensitive balances not displayed')
    for code,status,qty,amount in answers:
        print(code,status,'preview_qty=',qty,'preview_krw=',amount)
    print('CAUTION: REST snapshot is not streaming tick or executable price. NEVER authorizes order.')

if __name__=='__main__':
    try: main()
    except Exception as ex:
        # Never print exception details or API payloads: they may contain private data.
        allowed_codes = ('VALUATION_01', 'VALUATION_02', 'VALUATION_03_BOTH',
                         'VALUATION_03_FLAG', 'VALUATION_03_CURSOR')
        message = str(ex)
        error_code = next((code for code in allowed_codes
                           if STAGE == 'valuation' and message.startswith(code + ':')),
                          'UNKNOWN')
        print('V3 STATUS: BLOCKED_API_OR_DATA_ERROR; stage=' + STAGE +
              '; error_code=' + error_code +
              '; error_type=' + type(ex).__name__ +
              '; orders_sent=0; LIVE_ENABLED=false', file=sys.stderr)
        sys.exit(1)
