"""Conservative end-of-day gate for V4-C paper simulation; never places orders."""
import argparse
import csv
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def check(day, quotes, candidates, state_path):
    if not quotes.is_file(): return False, 'missing quote file'
    if not candidates.is_file(): return False, 'missing candidate file'
    if state_path.exists():
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('mode')!='v4c_paper_only': return False, 'unexpected account mode'
        if day in state.get('processed_dates',[]): return False, 'already processed'
        if state.get('last_processed_day') and day < state['last_processed_day']:
            return False, 'historical replay prohibited'
    cr=rows(candidates)
    if not cr or any(r.get('date')!=day for r in cr):
        return False, 'candidate CSV must contain only the target date'
    # Check all candidate codes are in proper format; empty valid candidate lists
    # require a separate, explicit no-trade policy rather than guessing.
    codes={r.get('ticker','').zfill(6) for r in cr}
    if not all(len(x)==6 and x.isdigit() for x in codes): return False,'bad candidate ticker'
    latest={}
    for r in rows(quotes):
        try:
            dt=datetime.fromisoformat(r['received_at_kst'])
            if dt.tzinfo is None: continue
            dt=dt.astimezone(KST)
            if dt.date().isoformat()!=day: continue
            code=r['ticker'].zfill(6)
            if r.get('api_code')!='MCA00000' or r.get('quote_status')!='snapshot_only': continue
            if float(r['price'])<=0: continue
            if not time(9,10)<=dt.time()<=time(15,20):continue
            latest[code]=max(latest.get(code,dt),dt)
        except (KeyError,ValueError,TypeError): continue
    if not latest: return False,'no valid quote snapshots'
    # If an existing position lacks a near-close quote, do not update its equity or exits.
    held=set()
    if state_path.exists(): held=set(json.loads(state_path.read_text(encoding='utf-8')).get('positions',{}))
    required=held | codes
    # A candidate with volume zero still needs a near-close snapshot to make
    # the quality check conservative; the engine itself rejects zero-volume fills.
    missing=sorted(x for x in required if x not in latest or latest[x].time()<time(15,15))
    if missing: return False, 'missing >=15:15 quote for required ticker(s): '+','.join(missing)
    return True,'quality gate passed'

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--day',required=True)
    a.add_argument('--quotes',required=True,type=Path)
    a.add_argument('--candidates',required=True,type=Path)
    a.add_argument('--state',required=True,type=Path)
    x=a.parse_args()
    ok,msg=check(x.day,x.quotes,x.candidates,x.state)
    print(json.dumps({'ready':ok,'reason':msg,'day':x.day},ensure_ascii=False))
    # 3 means deliberate skip; no account writes
    raise SystemExit(0 if ok else 3)
