#!/usr/bin/env python3
"""V4.4 KIS READ-ONLY order reconciliation. No order/cancel endpoints.

Never link PREVIEW_ONLY intents to broker orders without a previously verified
broker order ID. Writes only a sanitized aggregate report, not account data.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from kis_live_v4_3 import guard, load_json, Blocked

HOST = 'https://openapi.koreainvestment.com:9443'
KST = ZoneInfo('Asia/Seoul')
ORDER_ID = re.compile(r'^\d{1,20}$')
TICKER = re.compile(r'^\d{6}$')
STAGE = 'initialization'  # Safe stage labels only


def get_secrets():
    guard()  # Must be disabled even for inquiry.
    names = ('KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_ACCOUNT_NO', 'KIS_ACCOUNT_PRODUCT')
    values = [os.environ.get(n, '').strip() for n in names]
    if any(not v for v in values):
        raise Blocked('MISSING_SECRET')
    if not (len(values[2]) == 8 and values[2].isascii() and values[2].isdigit()
            and len(values[3]) == 2 and values[3].isascii() and values[3].isdigit()):
        raise Blocked('INVALID_ACCOUNT_FORMAT')
    return values


def api(method, path, *, headers=None, params=None, payload=None):
    if method not in ('GET', 'POST') or path not in (
            '/oauth2/tokenP', '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'):
        raise Blocked('UNAPPROVED_API_ENDPOINT')
    url = HOST + path
    if params:
        url += '?' + urlencode(params)
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, method=method, data=data,
                                 headers={'Content-Type': 'application/json; charset=utf-8', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=18) as response:
            continuation = response.headers.get('tr_cont')
            content = response.read(1024 * 1024)
    except urllib.error.HTTPError as e:
        # Only publish a numeric status, never exception text, URL, headers or body.
        status = e.code if type(e.code) is int and 100 <= e.code <= 599 else 'unknown'
        raise Blocked('BROKER_HTTP_FAILURE_' + str(status)) from None
    except Exception:
        raise Blocked('BROKER_TRANSPORT_FAILURE') from None
    try:
        obj = json.loads(content)
    except Exception:
        raise Blocked('INVALID_BROKER_JSON') from None
    if not isinstance(obj, dict):
        raise Blocked('INVALID_BROKER_RESPONSE')
    return obj, continuation


def token_for(key, secret):
    result, _ = api('POST', '/oauth2/tokenP', payload={
        'grant_type': 'client_credentials', 'appkey': key, 'appsecret': secret})
    token = result.get('access_token')
    if not isinstance(token, str) or not token:
        raise Blocked('TOKEN_NOT_GRANTED')
    return token


def fetch_orders(key, secret, cano, product, token, date):
    """Retrieve every page of today's order/fill records; fail closed on ambiguity."""
    fk = nk = ''
    seen = set()
    all_rows = []
    for page in range(12):
        params = {
            'CANO': cano, 'ACNT_PRDT_CD': product,
            'INQR_STRT_DT': date, 'INQR_END_DT': date,
            'SLL_BUY_DVSN_CD': '00', 'INQR_DVSN': '00', 'PDNO': '',
            'CCLD_DVSN': '00', 'ORD_GNO_BRNO': '', 'ODNO': '',
            'INQR_DVSN_3': '00', 'INQR_DVSN_1': '', 'INQR_DVSN_2': '',
            'CTX_AREA_FK100': fk, 'CTX_AREA_NK100': nk,
            'EXCG_ID_DVSN_CD': 'ALL'
        }
        hdr = {'authorization': 'Bearer ' + token, 'appkey': key,
               'appsecret': secret, 'tr_id': 'TTTC8001R', 'custtype': 'P'}
        if page:
            hdr['tr_cont'] = 'N'
        result, continuation = api('GET', '/uapi/domestic-stock/v1/trading/inquire-daily-ccld',
                                   params=params, headers=hdr)
        if str(result.get('rt_cd')) != '0':
            raise Blocked('ORDER_HISTORY_REJECTED')
        rows = result.get('output1')
        if not isinstance(rows, list):
            raise Blocked('ORDER_HISTORY_MISSING')
        if len(all_rows) + len(rows) > 1200:
            raise Blocked('ORDER_HISTORY_TOO_LARGE')
        all_rows.extend(rows)
        if continuation is None:
            raise Blocked('ORDER_HISTORY_HEADER_MISSING')
        flag = continuation.strip().upper()
        if flag not in ('', 'D', 'E', 'F', 'M'):
            raise Blocked('ORDER_HISTORY_HEADER_INVALID')
        if flag not in ('F', 'M'):
            return all_rows
        fk2, nk2 = (str(result.get(k) or '').strip()
                    for k in ('ctx_area_fk100', 'ctx_area_nk100'))
        if not fk2 or not nk2 or (fk2, nk2) in seen:
            raise Blocked('ORDER_HISTORY_PAGINATION_INVALID')
        seen.add((fk2, nk2))
        fk, nk = fk2, nk2
    raise Blocked('ORDER_HISTORY_PAGE_LIMIT')


def nonneg(value):
    if value is None or str(value).strip() == '':
        return None
    s = str(value).strip()
    if not s.isascii() or not s.isdigit():
        return None
    return int(s)


def reconcile(state, rows):
    """Do not mutate state. Link only explicit broker IDs (never ticker alone)."""
    if not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('mode') != 'NO_ORDER_STATE_TRACKER' or not isinstance(state.get('intents'), dict):
        raise Blocked('INVALID_JOURNAL')
    intents = state['intents']
    by_order = {}
    for ident, obj in intents.items():
        if not isinstance(obj, dict):
            raise Blocked('INVALID_INTENT')
        oid = obj.get('broker_order_id')
        if obj.get('state') == 'PREVIEW_ONLY':
            if oid is not None or nonneg(obj.get('filled_qty')) != 0:
                raise Blocked('PREVIEW_ORDER_LINK_CONFLICT')
        if oid is not None:
            oid = str(oid).strip()
            if not ORDER_ID.fullmatch(oid) or oid in by_order:
                raise Blocked('DUPLICATE_OR_INVALID_BROKER_ID')
            by_order[oid] = obj
    # Fail closed if an order is present but its quantity or identity is malformed.
    matches = set()
    unknown_orders = set()
    seen_order_tickers = {}
    for row in rows:
        if not isinstance(row, dict):
            raise Blocked('INVALID_ORDER_ROW')
        oid = str(row.get('odno') or '').strip()
        ticker = str(row.get('pdno') or '').strip()
        if not ORDER_ID.fullmatch(oid) or not TICKER.fullmatch(ticker):
            raise Blocked('INVALID_ORDER_IDENTITY')
        quantity = nonneg(row.get('ord_qty'))
        remainder = nonneg(row.get('rmn_qty'))
        if quantity is None or remainder is None or remainder > quantity:
            raise Blocked('ORDER_QUANTITY_INCONSISTENT')
        if oid in seen_order_tickers and seen_order_tickers[oid] != ticker:
            raise Blocked('ORDER_ID_TICKER_CONFLICT')
        seen_order_tickers[oid] = ticker
        if oid in by_order:
            if by_order[oid].get('ticker') != ticker:
                raise Blocked('LINKED_ORDER_TICKER_MISMATCH')
            matches.add(oid)
        else:
            unknown_orders.add(oid)
    # Even with matches, do not automatically alter intent state or fills.
    return {'mode': 'V4_4_RECONCILE_READ_ONLY', 'orders_sent': 0,
            'broker_readonly_queries_performed': True, 'broker_order_api_calls': 0, 'live_enabled': False,
            'intent_count': len(intents), 'linked_order_ids': len(by_order),
            'matched_order_ids': len(matches),
            'unmatched_broker_order_ids': len(unknown_orders),
            'unresolved_linked_ids': len(set(by_order) - matches),
            'requires_manual_review': bool(unknown_orders or (set(by_order) - matches)),
            'journal_modified': False,
            'preview_intents_not_linked': sum(v.get('state') == 'PREVIEW_ONLY' for v in intents.values())}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(path)


def main():
    global STAGE
    STAGE = 'configuration'
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='data/live/v4_3_state.json')
    parser.add_argument('--report', default='data/live/v4_4_reconcile_report.json')
    args = parser.parse_args()
    key, secret, cano, product = get_secrets()
    STAGE = 'local_journal'
    state = load_json(args.state)
    # Local structural validation BEFORE any network request.
    reconcile(state, [])
    STAGE = 'authentication'
    # Reuse only within the orchestrated V4.5.1 run; standalone usage still
    # obtains a token normally. Never print or persist this environment value.
    token = os.environ.get('KIS_SESSION_ACCESS_TOKEN', '').strip()
    if not token:
        token = token_for(key, secret)
    STAGE = 'order_history'
    rows = fetch_orders(key, secret, cano, product, token,
                        dt.datetime.now(KST).strftime('%Y%m%d'))
    STAGE = 'reconciliation'
    report = reconcile(state, rows)
    STAGE = 'report_write'
    write_report(Path(args.report), report)
    print('V4.4 READ_ONLY_RECONCILIATION_COMPLETE; orders_sent=0; LIVE_ENABLED=false')
    print('intents=', report['intent_count'], 'preview_only=', report['preview_intents_not_linked'])
    print('broker_orders_unmatched=', report['unmatched_broker_order_ids'],
          'requires_manual_review=', report['requires_manual_review'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Blocked reasons are locally authored constants; restrict what is emitted.
        reason = str(exc) if isinstance(exc, Blocked) else 'UNEXPECTED_FAILURE'
        if not re.fullmatch(r'[A-Z0-9_]{1,80}', reason):
            reason = 'UNEXPECTED_FAILURE'
        stage = STAGE if STAGE in ('initialization', 'configuration', 'local_journal',
                                    'authentication', 'order_history', 'reconciliation',
                                    'report_write') else 'unknown'
        print('V4.4 BLOCKED: stage=' + stage + '; error_code=' + reason +
              '; orders_sent=0; LIVE_ENABLED=false', file=sys.stderr)
        sys.exit(1)
