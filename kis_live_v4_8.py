#!/usr/bin/env python3
"""KIS V4.8: read-only evidence collection, NEVER authorizes an order.

Records only booleans/diagnostics. Monetary account data is never persisted.
Today's baseline and verified net external cashflows cannot be inferred from a
single balance API call, so V4.7 remains DENY even when this check succeeds.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
REQUIRED = ('tot_evlu_amt', 'scts_evlu_amt')

class Blocked(Exception): pass

def safety_off():
    if os.getenv('LIVE_ENABLED', 'false').strip().lower() != 'false' or os.getenv('KIS_ALLOW_ORDER', 'false').strip().lower() != 'false':
        raise Blocked('ORDER_SWITCH_NOT_OFF')

def parse_equity_summary(payload):
    """Check only shape and presence, without returning or writing balances."""
    if not isinstance(payload, dict) or str(payload.get('rt_cd')) != '0':
        raise Blocked('BALANCE_API_REJECTED')
    blocks = payload.get('output2')
    if not isinstance(blocks, list) or not blocks or not isinstance(blocks[0], dict):
        raise Blocked('BALANCE_SUMMARY_MISSING')
    obj = blocks[0]
    def money(field):
        val = obj.get(field)
        return isinstance(val, str) and val.isascii() and val.isdigit() and len(val) <= 18
    return {k: money(k) for k in REQUIRED}

def check_readonly():
    # Dynamically import only approved read-only transport from existing project.
    from kis_buying_power_readonly import credentials, request, require_ok
    from kis_live_v4_5_2 import _key, acquire_token
    from kis_live_v4_4 import api as order_api
    from urllib.parse import urlencode
    key, secret, cano, product = credentials()
    token, refreshed = acquire_token(_key(), dt.datetime.now(dt.timezone.utc))
    # Refreshed token must not be persisted by this workflow, so reject this mode:
    # use existing V4.5.2 workflow to refresh/encrypt/persist first.
    if refreshed:
        raise Blocked('CACHE_REFRESH_NOT_COMMITTED_USE_V452')
    params={'CANO':cano,'ACNT_PRDT_CD':product,'AFHR_FLPR_YN':'N','OFL_YN':'',
            'INQR_DVSN':'00','UNPR_DVSN':'01','FUND_STTL_ICLD_YN':'N',
            'FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
            'CTX_AREA_FK100':'','CTX_AREA_NK100':''}
    headers={'authorization':'Bearer '+token,'appkey':key,'appsecret':secret,
             'tr_id':'TTTC8434R','custtype':'P'}
    result = require_ok(request('GET','/uapi/domestic-stock/v1/trading/inquire-balance?'+urlencode(params),headers=headers),'Balance')
    fields = parse_equity_summary(result)
    # Call-level success does not establish complete pages, cash flows or baseline.
    return {'balance_response_ok': True, 'equity_fields_well_formed': all(fields.values()),
            'equity_fields_present': fields}

def report_template(now):
    return {'schema_version': 1, 'mode': 'V4_8_READ_ONLY_EVIDENCE_AUDIT',
            'date_kst': now.strftime('%Y-%m-%d'),
            'status': 'DENY', 'orders_sent': 0, 'live_enabled': False,
            'daily_loss_verified': False, 'baseline_verified': False,
            'external_cashflows_verified': False, 'holdings_pagination_verified': False,
            'v4_7_input_generated': False,
            'block_codes': ['DAILY_LOSS_NOT_VERIFIED','BASELINE_NOT_CAPTURED',
                            'EXTERNAL_CASHFLOWS_NOT_RECONCILED','ORDER_TRANSMISSION_NOT_IMPLEMENTED'],
            'balance_response_ok': False, 'equity_fields_well_formed': False,
            'equity_fields_present': {k: False for k in REQUIRED}}

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--report',default='data/live/v4_8_audit.json',type=Path)
    ap.add_argument('--offline',action='store_true',help='No KIS calls; safe dry-run')
    args=ap.parse_args()
    safety_off()
    r=report_template(dt.datetime.now(KST))
    if args.offline:
        r['block_codes'].append('OFFLINE_ONLY')
    else:
        try:
            res=check_readonly()
            r.update(res)
            if not res['equity_fields_well_formed']:
                r['block_codes'].append('BALANCE_FIELDS_MISSING_OR_INVALID')
            else:
                r['block_codes'].append('SINGLE_SNAPSHOT_NOT_PNL_EVIDENCE')
        except HTTPError as e:
            r['block_codes'].append('BROKER_HTTP_'+str(e.code if isinstance(e.code,int) and 100<=e.code<=599 else 'ERROR'))
        except (Blocked, URLError) as e:
            code=str(e) if isinstance(e,Blocked) else 'NETWORK_ERROR'
            allowed={'BALANCE_API_REJECTED','BALANCE_SUMMARY_MISSING','CACHE_REFRESH_NOT_COMMITTED_USE_V452','ORDER_SWITCH_NOT_OFF'}
            r['block_codes'].append(code if code in allowed else 'READ_ONLY_API_OR_CONFIG_ERROR')
        except Exception:
            r['block_codes'].append('READ_ONLY_API_OR_CONFIG_ERROR')
    atomic_json(args.report,r)
    print('V4.8 AUDIT COMPLETE; gate=DENY; orders_sent=0; LIVE_ENABLED=false')
    print('balance_response_ok='+str(r['balance_response_ok']).lower()+'; daily_loss_verified=false')
    print('block_codes='+','.join(r['block_codes']))

if __name__=='__main__':
    try: main()
    except Exception:
        print('V4.8 BLOCKED: CONFIG_OR_STORAGE_ERROR; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        raise SystemExit(1)
