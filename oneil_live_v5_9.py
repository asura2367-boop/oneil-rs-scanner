#!/usr/bin/env python3
"""V5.9: Prepare read-only LIVE candidate review from collected V4B CSV.

No brokerage API, orderable cash inference, execution, or order authorization.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
REQUIRED=('date','ticker','rs','foreign_5d','institution_5d',
          'volume_ratio','channel_up','channel_position')
TICKER=re.compile(r'^\d{6}$')

def analyze(path, now, min_rs=85, min_volume=1.0, max_channel=.75):
    if any(os.getenv(n,'false').lower()!='false' for n in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise RuntimeError('LIVE_FLAGS_NOT_DISABLED')
    return analyze_candidates(path, now, min_rs, min_volume, max_channel)

def analyze_candidates(path, now, min_rs=85, min_volume=1.0, max_channel=.75):
    """Pure CSV filtering for the integrated runtime; grants no order authority.

    The standalone analyze/CLI entry point retains its flags-off guard. Live
    authorization, cash checks and order transmission remain in the engine.
    """
    report={'version':'5.9','status':'DENY','mode':'LIVE_CANDIDATE_REVIEW_ONLY',
            'orders_sent':0,'broker_order_api_calls':0,
            'live_enabled':False,'allow_order':False,
            'source':'V4B_CSV_UNVERIFIED','generated_at_kst':now.isoformat(),
            'reason_codes':[],'review_candidates':[], 'approved_order_intents':[], 'candidate_diagnostics':[]}
    def reason(x):
        if x not in report['reason_codes']: report['reason_codes'].append(x)
    if not path.is_file():
        reason('SOURCE_FILE_MISSING');return report
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        columns=set(reader.fieldnames or [])
        missing=set(REQUIRED)-columns
        if missing:
            reason('REQUIRED_COLUMNS_MISSING')
            report['missing_columns']=sorted(missing)
            return report
        rows=list(reader)
    if not rows:
        reason('NO_CANDIDATES'); return report
    seen=set(); invalid=0; stale=0
    for row in rows:
        try:
            date=dt.date.fromisoformat(row['date'])
            code=row['ticker'].strip()
            if not TICKER.fullmatch(code) or (date,code) in seen: raise ValueError()
            seen.add((date,code))
            # V4B signals may be created after the close for the following day.
            # A business-day holiday calendar is NOT certified: allow review only.
            if date>now.date() or (now.date()-date).days>4:
                stale+=1
                report['candidate_diagnostics'].append({'ticker':code,'reason_codes':['SIGNAL_DATE_STALE_OR_FUTURE']})
                continue
            basis=(row.get('signal_basis') or '').strip()
            observed=None
            if basis not in ('','PREVIOUS_CONFIRMED_SESSION','INTRADAY_KIS_SNAPSHOT'):
                raise ValueError()
            if basis=='INTRADAY_KIS_SNAPSHOT':
                # An intraday signal expires at the session boundary. It must
                # never use the legacy four-day opening-signal grace period.
                observed=dt.datetime.fromisoformat(row['observed_at_kst'])
                local=now.astimezone(KST);at=observed.astimezone(KST)
                if (observed.tzinfo is None or date!=local.date() or at.date()!=local.date()
                        or dt.date.fromisoformat(row['signal_date'])!=date
                        or not dt.date.fromisoformat(row['flow_signal_date'])<date
                        or at.weekday()>=5 or not dt.time(9)<=at.time().replace(tzinfo=None)<dt.time(15,15)
                        or (now-observed).total_seconds() < -2):
                    raise ValueError()
            elif basis=='PREVIOUS_CONFIRMED_SESSION' and not dt.date.fromisoformat(row['signal_date'])<date:
                raise ValueError()
            rs=float(row['rs']); f=float(row['foreign_5d']); inst=float(row['institution_5d'])
            volume=float(row['volume_ratio']); channel=float(row['channel_position'])
            # Optional historical close must never be treated as executable price.
            raw_close=(row.get('reference_close') or '').strip()
            close=float(raw_close) if raw_close else None
            numbers=[rs,f,inst,volume,channel]+([close] if close is not None else [])
            if not all(x==x and abs(x)!=float('inf') for x in numbers): raise ValueError()
            up=row['channel_up'].strip().lower() in ('true','1','yes')
            if not (0 <= rs <= 100 and volume>=0) or (close is not None and close<=0): raise ValueError()
            blocks=[]
            if rs<min_rs: blocks.append('RS_BELOW_MIN')
            if volume<min_volume: blocks.append('VOLUME_BELOW_MIN')
            if not up: blocks.append('CHANNEL_NOT_UP')
            if not 0<=channel<=max_channel: blocks.append('CHANNEL_POSITION_OUTSIDE')
            if not (f>0 or inst>0): blocks.append('NO_POSITIVE_FLOW')
            report['candidate_diagnostics'].append({'ticker':code, 'reason_codes':blocks,
                'rs':rs, 'volume_ratio':volume, 'foreign_5d':f, 'institution_5d':inst,
                'flow_category':('BOTH' if f>0 and inst>0 else 'FOREIGN_ONLY' if f>0 else 'INSTITUTION_ONLY' if inst>0 else 'NEITHER')})
            if not blocks:
                item={'date':date.isoformat(),'ticker':code,'rs':rs,
                      'flag':'REVIEW_ONLY_NOT_LIVE_QUOTE', 'price_verified':False,
                      'foreign_5d':f, 'institution_5d':inst,
                      'flow_category': ('BOTH' if f>0 and inst>0 else 'FOREIGN_ONLY' if f>0 else 'INSTITUTION_ONLY')}
                item.update(name=(row.get('name') or code).strip(),signal_basis=basis or 'LEGACY_PRE_SESSION',
                    signal_date=row.get('signal_date'),observed_at_kst=observed.isoformat() if observed else None,
                    flow_signal_date=row.get('flow_signal_date'),volume_ratio=volume,channel_position=channel)
                if close is not None: item['reference_close_krw']=close
                report['review_candidates'].append(item)
        except (ValueError,TypeError,OverflowError,KeyError):
            invalid+=1
    if invalid: reason('INVALID_ROWS_PRESENT')
    if stale: reason('STALE_SIGNAL_ROWS')
    if not report['review_candidates']: reason('NO_VERIFIED_RULE_MATCHES')
    report['review_candidates'].sort(key=lambda x:(-x['rs'],x['ticker']))
    report['review_candidates']=report['review_candidates'][:10]
    report['candidate_diagnostics']=report['candidate_diagnostics'][:200]
    reason('BROKER_CASH_NOT_VERIFIED')
    reason('LIVE_QUOTE_NOT_VERIFIED')
    reason('DAILY_LOSS_NOT_VERIFIED')
    reason('NO_ORDER_TRANSMISSION')
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',default='data/v4b/candidates.csv')
    p.add_argument('--output',default='v5_9_candidates_review.json')
    p.add_argument('--rs',type=float,default=85)
    p.add_argument('--volume',type=float,default=1.0)
    p.add_argument('--channel',type=float,default=.75)
    a=p.parse_args()
    if not (0<=a.rs<=100 and 0<a.volume<=100 and 0<=a.channel<=1): raise ValueError('INVALID_PARAMETERS')
    output=analyze(Path(a.source),dt.datetime.now(KST),a.rs,a.volume,a.channel)
    Path(a.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V5.9 GATE=DENY; candidates_for_review='+str(len(output['review_candidates']))+
          '; diagnostics='+json.dumps(output['candidate_diagnostics'],ensure_ascii=False,separators=(',',':'))+
          '; reasons='+','.join(output['reason_codes'])+
          '; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()
