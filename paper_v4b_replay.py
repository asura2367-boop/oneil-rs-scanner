"""V4-B historical quote replay; no broker endpoints, no V3 account writes.
Candidate file must contain pre-session validated signals, not generated from same-day prices.
"""
import argparse
import csv
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

INITIAL = 10_000_000.0
BUY_FEE, SELL_FEE, SELL_TAX, SLIP = .00015, .00015, .002, .001
TRADE_FIELDS = ['time','ticker','side','qty','price','cost','cash_change','reason']

def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def candidates_for_day(path, day):
    if not path.exists():
        return set()
    codes = set()
    for r in read_rows(path):
        try:
            if (r['date'] == day and float(r['rs']) >= 85
                    and float(r['foreign_5d']) > 0 and float(r['institution_5d']) > 0
                    and float(r['volume_ratio']) >= 1
                    and 0 <= float(r['channel_position']) <= .75):
                codes.add(r['ticker'].zfill(6))
        except (ValueError, TypeError, KeyError):
            continue
    return codes

def replay(quotes_path, candidate_path, output_dir):
    day = quotes_path.stem.removeprefix('quotes_')
    datetime.strptime(day, '%Y-%m-%d')
    candidates = candidates_for_day(candidate_path, day)
    events = []
    for r in read_rows(quotes_path):
        try:
            dt = datetime.fromisoformat(r['received_at_kst'])
            if dt.tzinfo is None:
                continue
            dt = dt.astimezone(ZoneInfo('Asia/Seoul'))
            price = float(r['price'])
            vol = float(r['cumulative_volume'])
            if (dt.date().isoformat() != day or not time(9,10) <= dt.time() <= time(15,20)
                    or price <= 0 or vol <= 0 or r.get('api_code') != 'MCA00000'
                    or r.get('quote_status') != 'snapshot_only'):
                continue
            events.append((dt, r['ticker'].zfill(6), price))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    events.sort()
    cash = INITIAL
    positions, last_prices, partial_done, trades = {}, {}, set(), []
    for dt, ticker, price in events:
        last_prices[ticker] = price
        if ticker in positions:
            pos = positions[ticker]
            ret = price / pos['entry'] - 1
            qty, reason = 0, ''
            if ret <= -.05:
                qty, reason = pos['qty'], 'stop -5%'
            elif ret >= .15:
                qty, reason = pos['qty'], 'take +15%'
            elif ret >= .08 and ticker not in partial_done:
                qty, reason = max(1, pos['qty']//2), 'partial +8%'
            if qty:
                fill = price * (1-SLIP)
                cost = qty*fill*(SELL_FEE+SELL_TAX)
                change = qty*fill-cost
                cash += change
                pos['qty'] -= qty
                trades.append([dt.isoformat(),ticker,'SELL',qty,round(fill,4),round(cost,2),round(change,2),reason])
                if reason == 'partial +8%':
                    partial_done.add(ticker)
                if pos['qty'] == 0:
                    del positions[ticker]
        elif ticker in candidates and len(positions) < 5 and sum(t[2]=='BUY' for t in trades) < 2:
            fill = price*(1+SLIP)
            equity = cash + sum(p['qty']*last_prices.get(t,p['entry']) for t,p in positions.items())
            qty = int(min(equity*.2,cash)//(fill*(1+BUY_FEE)))
            if qty > 0:
                cost = qty*fill*BUY_FEE
                change = -qty*fill-cost
                cash += change
                positions[ticker] = {'qty':qty,'entry':fill}
                trades.append([dt.isoformat(),ticker,'BUY',qty,round(fill,4),round(cost,2),round(change,2),'pre-session candidate'])
    equity = cash + sum(p['qty']*last_prices.get(t,p['entry']) for t,p in positions.items())
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir/f'trades_{day}.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(TRADE_FIELDS);w.writerows(trades)
    summary={'mode':'v4b_replay_only','date':day,'cash':round(cash,2),'equity':round(equity,2),
             'positions':positions,'candidate_count':len(candidates),'valid_snapshots':len(events),
             'trade_count':len(trades),'warning':'Snapshot-based hypothetical fills; no guarantee of actual fills or intraday stop execution'}
    (output_dir/f'summary_{day}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--quotes',type=Path,required=True)
    p.add_argument('--candidates',type=Path,default=Path('data/v4b/candidates.csv'))
    p.add_argument('--output',type=Path,default=Path('data/paper_v4b_replay'))
    a=p.parse_args()
    replay(a.quotes,a.candidates,a.output)
