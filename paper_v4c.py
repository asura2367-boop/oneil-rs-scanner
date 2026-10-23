"""V4-C persistent, snapshot-based paper simulator. NO broker calls or real orders."""
import argparse, csv, json, os, tempfile
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo

INITIAL=10_000_000.0
BUY_FEE=.00015; SELL_FEE=.00015; SELL_TAX=.002; SLIP=.001
TRADE_FIELDS=['time','ticker','side','qty','price','cost','cash_change','reason']
KST=ZoneInfo('Asia/Seoul')

def rows(path):
    if not path.exists(): return []
    with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def atomic_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.tmp_')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def csv_save(path,data,headers):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.tmp_')
    try:
        with os.fdopen(fd,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def candidates_for_day(path,day):
    valid=set()
    for r in rows(path):
        try:
            if (r['date']==day and float(r['rs'])>=85 and float(r['foreign_5d'])>0
                and float(r['institution_5d'])>0 and float(r['volume_ratio'])>=1
                and 0<=float(r['channel_position'])<=.75):
                valid.add(r['ticker'].zfill(6))
        except (KeyError,ValueError,TypeError): pass
    return valid

def load_events(path):
    day=path.stem.removeprefix('quotes_');datetime.strptime(day,'%Y-%m-%d')
    ev=[]; seen=set(); rejected=0
    for r in rows(path):
        try:
            dt=datetime.fromisoformat(r['received_at_kst'])
            if dt.tzinfo is None: raise ValueError('naive timestamp')
            dt=dt.astimezone(KST)
            ticker=r['ticker'].zfill(6); price=float(r['price']);vol=float(r['cumulative_volume'])
            if (dt.date().isoformat()!=day or not time(9,10)<=dt.time()<=time(15,20)
                or price<=0 or vol<=0 or r.get('api_code')!='MCA00000'
                or r.get('quote_status')!='snapshot_only'): raise ValueError('invalid snapshot')
            key=(dt.isoformat(),ticker)
            if key in seen:continue
            seen.add(key);ev.append((dt,ticker,price))
        except (KeyError,ValueError,TypeError,OverflowError): rejected+=1
    return day,sorted(ev),rejected

def new_state():
    return {'mode':'v4c_paper_only','initial_cash':INITIAL,'cash':INITIAL,
            'positions':{},'last_prices':{},'last_processed_day':None,'session_index':0,
            'processed_dates':[],'last_event':None}

def run(quotes,candidates,output,allow_backfill=False):
    day,events,rejected=load_events(quotes)
    output.mkdir(parents=True,exist_ok=True)
    state_file=output/'state.json'; trades_file=output/'trades.csv'; equity_file=output/'equity.csv'
    state=json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else new_state()
    if state.get('mode')!='v4c_paper_only':raise ValueError('Unexpected state mode')
    if day in state['processed_dates']:
        print('ALREADY_PROCESSED',day,'no changes');return state
    if state['last_processed_day'] and day<state['last_processed_day']:
        raise ValueError('Historical backfill prohibited after newer dates')
    if not events:raise ValueError('No valid snapshots; state untouched')
    allowed=candidates_for_day(candidates,day)
    positions=state['positions']; cash=float(state['cash']);last=state['last_prices']
    trades=rows(trades_file); equity_rows=rows(equity_file)
    previous_trade_count=len(trades)
    bought_today=set(); buy_count=0
    for dt,ticker,price in events:
        last[ticker]=price
        stamp=dt.isoformat()
        if ticker in positions:
            p=positions[ticker]; qty=0;reason=''
            ret=price/float(p['entry'])-1
            if ret<=-.05:qty=int(p['qty']);reason='stop -5%'
            elif ret>=.15:qty=int(p['qty']);reason='take +15%'
            elif ret>=.08 and not p['partial_done']:
                qty=max(1,int(p['qty'])//2);reason='partial +8%'
            if qty:
                fill=price*(1-SLIP);fee=qty*fill*(SELL_FEE+SELL_TAX)
                delta=qty*fill-fee;cash+=delta;p['qty']-=qty
                trades.append(dict(zip(TRADE_FIELDS,[stamp,ticker,'SELL',qty,round(fill,4),round(fee,2),round(delta,2),reason])))
                if reason=='partial +8%':p['partial_done']=True
                if p['qty']==0:del positions[ticker]
        elif ticker in allowed and ticker not in bought_today and len(positions)<5 and buy_count<2:
            total=cash+sum(int(p['qty'])*float(last.get(t,p['entry'])) for t,p in positions.items())
            fill=price*(1+SLIP);budget=min(total*.2,cash)
            qty=int(budget//(fill*(1+BUY_FEE)))
            if qty:
                fee=qty*fill*BUY_FEE;delta=-(qty*fill+fee);cash+=delta
                positions[ticker]={'qty':qty,'entry':fill,'partial_done':False,'entry_date':day,'entry_session':int(state['session_index'])+1}
                bought_today.add(ticker);buy_count+=1
                trades.append(dict(zip(TRADE_FIELDS,[stamp,ticker,'BUY',qty,round(fill,4),round(fee,2),round(delta,2),'pre-session candidate'])))
        state['last_event']=stamp
    state['session_index']+=1
    # Time exits require a valid tradable quote ON/AFTER session 10. A quote may not exist today.
    for ticker,p in list(positions.items()):
        if state['session_index']-int(p['entry_session'])<10:continue
        if ticker not in {e[1] for e in events}:continue
        price=float(last[ticker]);qty=int(p['qty']);fill=price*(1-SLIP)
        fee=qty*fill*(SELL_FEE+SELL_TAX);delta=qty*fill-fee;cash+=delta
        ticker_time=max(e[0].isoformat() for e in events if e[1]==ticker)
        trades.append(dict(zip(TRADE_FIELDS,[ticker_time,ticker,'SELL',qty,round(fill,4),round(fee,2),round(delta,2),'10-session exit'])))
        del positions[ticker]
    state['cash']=round(cash,4);state['last_processed_day']=day;state['processed_dates'].append(day)
    total=cash+sum(int(p['qty'])*float(last.get(t,p['entry'])) for t,p in positions.items())
    equity_rows.append({'date':day,'cash':round(cash,2),'equity':round(total,2),'positions':len(positions)})
    # Persist all files. Workflow concurrency must be serialized; no parallel writers.
    csv_save(trades_file,trades,TRADE_FIELDS)
    csv_save(equity_file,equity_rows,['date','cash','equity','positions'])
    atomic_json(state_file,state)
    result={'date':day,'candidate_count':len(allowed),'valid_snapshots':len(events),'rejected_snapshots':rejected,
            'new_trades':len(trades)-previous_trade_count,'cash':round(cash,2),'equity':round(total,2),'positions':len(positions)}
    print(json.dumps(result,ensure_ascii=False,indent=2));return state

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--quotes',required=True,type=Path)
    a.add_argument('--candidates',default=Path('data/v4b/candidates.csv'),type=Path)
    a.add_argument('--output',default=Path('data/paper_v4c'),type=Path)
    x=a.parse_args();run(x.quotes,x.candidates,x.output)
