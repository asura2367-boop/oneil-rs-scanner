"""Read-only audit of KIS daily/flow CSV. No broker calls or account writes."""
import argparse, csv, json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

def read(path):
    if not path.is_file(): return []
    with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def audit(data_dir, asof, output):
    daily=read(data_dir/'kis_daily_universe.csv')
    flow=read(data_dir/'kis_investor_flow.csv')
    universe=read(data_dir/'universe_test.csv')
    days=defaultdict(set); flowdays=defaultdict(set)
    for r in daily:
        try:
            ticker=str(r['ticker']).zfill(6); d=date.fromisoformat(r['date'][:10]); close=float(r['close'])
            if d<=asof and close>0: days[ticker].add(d)
        except (KeyError,ValueError,TypeError): pass
    for r in flow:
        try:
            ticker=str(r['ticker']).zfill(6); d=date.fromisoformat(r['date'][:10]); float(r['foreign_net_qty']);float(r['institution_net_qty'])
            if d<=asof: flowdays[ticker].add(d)
        except (KeyError,ValueError,TypeError): pass
    # Freshness uses calendar days as a warning heuristic, not a trading calendar.
    recent={t for t, ds in days.items() if len(ds)>=253 and max(ds)>=asof-timedelta(days=5)}
    flow_covered={t for t in recent if len([d for d in flowdays[t] if d>=asof-timedelta(days=14)])>=5}
    result={'as_of':asof.isoformat(),'universe_rows':len(universe),'daily_tickers':len(days),
            'rs_253_tickers':sum(len(v)>=253 for v in days.values()),
            'rs_253_recent_tickers':len(recent),'rs_recent_with_5_flow_rows':len(flow_covered),
            'flow_tickers':len(flowdays),
            'warning':'Recent means within 5 calendar days; flow 5 rows in 14 calendar days is a preliminary coverage check, not certified last five trading sessions. No candidate generation.'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'))
    p.add_argument('--asof',type=date.fromisoformat,default=date.today())
    p.add_argument('--output',type=Path,default=Path('v10_audit.json'))
    a=p.parse_args();audit(a.data,a.asof,a.output)
