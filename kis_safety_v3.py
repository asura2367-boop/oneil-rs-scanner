#!/usr/bin/env python3
"""KIS V3: fail-closed, READ-ONLY trade eligibility preview. NO order endpoint."""
import datetime as dt
import json
import urllib.request
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

def current_quote(key, secret, token, ticker, now):
    """Read a REST price and independently verify today's trading date.

    A daily bar confirms only the trading date, not that the REST price is
    a real-time executable quote. This module never submits an order.
    """
    if now.weekday() >= 5 or not (dt.time(9) <= now.time() < dt.time(15, 30)):
        raise RuntimeError('QUOTE_07: Outside regular market session')

    p = {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': ticker}
    try:
        result = request('GET', '/uapi/domestic-stock/v1/quotations/inquire-price?'
                         + urlencode(p),
                         headers=headers(key, secret, token, 'FHKST01010100'))
    except Exception:
        raise RuntimeError('QUOTE_01: Price HTTP or transport failure') from None
    try:
        result = require_ok(result, 'Quote')
    except Exception:
        raise RuntimeError('QUOTE_02: Price API rejected inquiry') from None
    out = result.get('output') if isinstance(result, dict) else None
    if not isinstance(out, dict):
        raise RuntimeError('QUOTE_03: Price output missing')
    price = unsigned_int(out.get('stck_prpr'))
    if not price:
        raise RuntimeError('QUOTE_04: Invalid or missing price')

    # KIS's inquire-price output does not reliably provide stck_bsop_date.
    # Use the dedicated daily-price endpoint instead; never infer the date.
    daily_params = {
        'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': ticker,
        'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '0'
    }
    try:
        daily = request(
            'GET', '/uapi/domestic-stock/v1/quotations/inquire-daily-price?'
            + urlencode(daily_params),
            headers=headers(key, secret, token, 'FHKST01010400')
        )
    except Exception:
        raise RuntimeError('QUOTE_08: Daily-price HTTP or transport failure') from None
    try:
        daily = require_ok(daily, 'Daily price')
    except Exception:
        raise RuntimeError('QUOTE_09: Daily-price API rejected inquiry') from None
    rows = daily.get('output') if isinstance(daily, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise RuntimeError('QUOTE_05: Daily trading date unavailable')
    day = str(rows[0].get('stck_bsop_date') or '').strip()
    if len(day) != 8 or not day.isascii() or not day.isdigit():
        raise RuntimeError('QUOTE_05: Daily trading date missing or malformed')
    if day != now.strftime('%Y%m%d'):
        raise RuntimeError('QUOTE_06: Trading date mismatch')

    # Cross-check the daily record has a plausible price; it does not prove
    # intra-day price freshness, which remains an explicit limitation.
    daily_close = unsigned_int(rows[0].get('stck_clpr'))
    if not daily_close:
        raise RuntimeError('QUOTE_10: Daily-price value missing')
    return price

def verified_balance_snapshot(key, secret, cano, product, token):
    """Read all balance pages using the KIS response *header* tr_cont.

    Fail closed on missing/unknown headers, pagination loops, or inconsistent
    summaries. No account details are logged or written to disk.
    """
    fk = nk = ''
    seen = set()
    held = set()
    valuations = []
    base = {'CANO': cano, 'ACNT_PRDT_CD': product, 'AFHR_FLPR_YN': 'N',
            'OFL_YN': '', 'INQR_DVSN': '00', 'UNPR_DVSN': '01',
            'FUND_STTL_ICLD_YN': 'N', 'FNCG_AMT_AUTO_RDPT_YN': 'N',
            'PRCS_DVSN': '00'}
    for page in range(10):
        params = {**base, 'CTX_AREA_FK100': fk, 'CTX_AREA_NK100': nk}
        path = '/uapi/domestic-stock/v1/trading/inquire-balance?' + urlencode(params)
        req_headers = headers(key, secret, token, 'TTTC8434R')
        if page:
            req_headers['tr_cont'] = 'N'
        req = urllib.request.Request(
            'https://openapi.koreainvestment.com:9443' + path,
            headers={'Content-Type': 'application/json; charset=utf-8', **req_headers},
            method='GET')
        with urllib.request.urlopen(req, timeout=20) as response:
            # Header is essential and not part of the JSON body.
            continuation = response.headers.get('tr_cont')
            body = json.load(response)
        require_ok(body, 'Balance')
        if continuation is None:
            raise RuntimeError('VALUATION_03_HEADER: Continuation header unavailable')
        continuation = continuation.strip().upper()
        if continuation not in ('', 'D', 'E', 'F', 'M'):
            raise RuntimeError('VALUATION_03_HEADER: Unknown continuation status')
        rows = body.get('output1')
        summaries = body.get('output2')
        if not isinstance(rows, list):
            raise RuntimeError('VALUATION_01: Holdings missing')
        if not isinstance(summaries, list) or not summaries or not isinstance(summaries[0], dict):
            raise RuntimeError('VALUATION_01: Account evaluation missing')
        invested = unsigned_int(summaries[0].get('scts_evlu_amt'))
        if invested is None:
            raise RuntimeError('VALUATION_02: Portfolio valuation missing')
        valuations.append(invested)
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError('VALUATION_01: Invalid holding record')
            qty = unsigned_int(row.get('hldg_qty'))
            ticker = str(row.get('pdno', '')).strip()
            if qty is None:
                raise RuntimeError('VALUATION_01: Invalid held quantity')
            if qty > 0:
                if len(ticker) != 6 or not ticker.isascii() or not ticker.isdigit():
                    raise RuntimeError('VALUATION_01: Invalid holding code')
                held.add(ticker)
        if continuation not in ('F', 'M'):
            if len(set(valuations)) != 1:
                raise RuntimeError('VALUATION_02: Inconsistent page valuations')
            return held, valuations[0]
        fk2 = str(body.get('ctx_area_fk100') or '').strip()
        nk2 = str(body.get('ctx_area_nk100') or '').strip()
        if not fk2 or not nk2 or (fk2, nk2) in seen:
            raise RuntimeError('VALUATION_03_CURSOR: Missing/repeated pagination cursor')
        seen.add((fk2, nk2))
        fk, nk = fk2, nk2
    raise RuntimeError('VALUATION_03_LIMIT: Too many balance pages')


def unfilled_codes(key, secret, cano, product, token):
    """Fail-closed read-only inquiry of ALL amend/cancel eligible orders.

    The response HEADER tr_cont, not the presence of body cursors, controls
    pagination. Do not interpret a missing/invalid response as no orders.
    """
    codes = set()
    fk = nk = ''
    seen = set()
    base = {'CANO': cano, 'ACNT_PRDT_CD': product,
            'INQR_DVSN_1': '0', 'INQR_DVSN_2': '0'}  # all orders / all sides
    for page in range(10):
        params = {**base, 'CTX_AREA_FK100': fk, 'CTX_AREA_NK100': nk}
        req_headers = headers(key, secret, token, 'TTTC0084R')
        if page:
            req_headers['tr_cont'] = 'N'
        req = urllib.request.Request(
            'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl?'
            + urlencode(params),
            headers={'Content-Type': 'application/json; charset=utf-8', **req_headers},
            method='GET')
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                continuation = response.headers.get('tr_cont')
                body = json.load(response)
        except Exception:
            raise RuntimeError('UNFILLED_01: Query transport or response failure') from None
        if not isinstance(body, dict):
            raise RuntimeError('UNFILLED_02: Invalid API response')
        try:
            require_ok(body, 'Unfilled orders')
        except Exception:
            raise RuntimeError('UNFILLED_02: API rejected inquiry') from None
        if continuation is None:
            raise RuntimeError('UNFILLED_03: Continuation header unavailable')
        continuation = continuation.strip().upper()
        if continuation not in ('', 'D', 'E', 'F', 'M'):
            raise RuntimeError('UNFILLED_03: Unexpected continuation header')
        rows = body.get('output')
        if not isinstance(rows, list):
            raise RuntimeError('UNFILLED_04: Orders array missing')
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError('UNFILLED_04: Invalid order record')
            code = str(row.get('pdno') or '').strip()
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                raise RuntimeError('UNFILLED_04: Invalid order instrument')
            # Include all returned cancellable orders; never silently omit an order.
            codes.add(code)
        if continuation not in ('F', 'M'):
            return codes
        new_fk = str(body.get('ctx_area_fk100') or '').strip()
        new_nk = str(body.get('ctx_area_nk100') or '').strip()
        if not new_fk or not new_nk or (new_fk, new_nk) in seen:
            raise RuntimeError('UNFILLED_05: Missing or repeated pagination cursor')
        seen.add((new_fk, new_nk))
        fk, nk = new_fk, new_nk
    raise RuntimeError('UNFILLED_05: Page limit reached')

def decide(plan, held, unfilled, invested, quote_lookup, power_lookup):
    """Return safe, non-orderable previews with stage-specific failure statuses."""
    remaining = max(0, CAP - invested)
    answers = []
    for row in plan:
        t = row['ticker']
        qty = amount = 0
        if t in held:
            status = 'BLOCKED_ALREADY_HELD'
        elif t in unfilled:
            status = 'BLOCKED_UNFILLED_ORDER'
        elif row.get('status') != 'PREVIEW_ONLY':
            status = 'BLOCKED_SIGNAL_NOT_READY'
        elif remaining <= 0:
            status = 'BLOCKED_TOTAL_CAP'
        else:
            # Never include exception text/API responses in logs: they can be sensitive.
            try:
                price = quote_lookup(t)
                if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
                    raise ValueError('invalid quotation')
            except Exception as ex:
                status = 'BLOCKED_QUOTE_ERROR'
                allowed = {f'QUOTE_{i:02d}' for i in range(1, 11)}
                code = str(ex).split(':', 1)[0]
                safe_code = code if code in allowed else 'QUOTE_UNKNOWN'
                print('QUOTE_DIAGNOSTIC ticker=' + t + ' error_code=' + safe_code,
                      file=sys.stderr)
            else:
                reference = unsigned_int(row.get('reference_close'))
                if not reference:
                    status = 'BLOCKED_REFERENCE_PRICE_MISSING'
                elif price * 100 > reference * 103:
                    status = 'BLOCKED_PRICE_GAP_GT_3PCT'
                else:
                    try:
                        cash, broker_qty = power_lookup(t, price)
                        if (not isinstance(cash, int) or isinstance(cash, bool) or cash < 0
                                or not isinstance(broker_qty, int) or isinstance(broker_qty, bool)
                                or broker_qty < 0):
                            raise ValueError('invalid buying power')
                        planned_qty = int(row.get('preview_qty') or 0)
                        if planned_qty < 0:
                            raise ValueError('invalid preview quantity')
                        qty = min(STOCK_CAP // price, remaining // price,
                                  cash // price, broker_qty, planned_qty)
                        if qty > 0:
                            amount = qty * price
                            remaining -= amount
                            status = 'PREVIEW_ELIGIBLE_NO_ORDER'
                        else:
                            status = 'BLOCKED_NO_BUYING_POWER'
                    except Exception:
                        qty = amount = 0
                        status = 'BLOCKED_BUYING_POWER_ERROR'
        answers.append((t, status, qty, amount))
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
    STAGE = 'valuation'
    held, invested = verified_balance_snapshot(key,secret,cano,product,token)
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
    # A green Actions check must not conceal a complete data/API outage.
    error_statuses = {'BLOCKED_QUOTE_ERROR', 'BLOCKED_BUYING_POWER_ERROR'}
    if answers and all(status in error_statuses for _, status, _, _ in answers):
        print('V3 STATUS: ALL_CANDIDATES_DATA_ERROR; orders_sent=0; LIVE_ENABLED=false',
              file=sys.stderr)
        sys.exit(2)


if __name__=='__main__':
    try: main()
    except Exception as ex:
        # Never print exception details or API payloads: they may contain private data.
        allowed_codes = ('VALUATION_01', 'VALUATION_02', 'VALUATION_03_HEADER',
                         'VALUATION_03_CURSOR', 'VALUATION_03_LIMIT',
                         'UNFILLED_01', 'UNFILLED_02', 'UNFILLED_03',
                         'UNFILLED_04', 'UNFILLED_05')
        message = str(ex)
        error_code = next((code for code in allowed_codes
                           if STAGE in ('valuation', 'unfilled_orders')
                           and message.startswith(code + ':')),
                          'UNKNOWN')
        print('V3 STATUS: BLOCKED_API_OR_DATA_ERROR; stage=' + STAGE +
              '; error_code=' + error_code +
              '; error_type=' + type(ex).__name__ +
              '; orders_sent=0; LIVE_ENABLED=false', file=sys.stderr)
        sys.exit(1)
