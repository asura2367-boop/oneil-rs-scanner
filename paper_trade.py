"""O'Neil-style paper-only close-to-close simulation. NEVER places broker orders."""
import json, os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'; OUT=DATA/'paper'
STATE=OUT/'state.json'; TRADES=OUT/'trades.csv'; EQUITY=OUT/'equity.csv'
TC=['date','ticker','name','side','qty','price','cost','cash_change','reason']
EC=['date','cash','holdings_value','equity','positions','pending']
COMPARISON=OUT/'signal_comparison.csv'
CC=['date','original_signals','balanced_signals','additional_signals','original_tickers','balanced_tickers']
ORIGINAL={'rs':90,'volume':1.2,'channel':.65}
BALANCED={'rs':85,'volume':1.0,'channel':.75}
STRATEGY=os.getenv('PAPER_STRATEGY','balanced').lower()
if STRATEGY not in ('original','balanced'): raise ValueError('PAPER_STRATEGY must be original or balanced')

FEE_BUY=float(os.getenv('PAPER_BUY_FEE','0.00015'))
FEE_SELL=float(os.getenv('PAPER_SELL_FEE','0.00015'))
TAX_SELL=float(os.getenv('PAPER_SELL_TAX','0.002'))
SLIP=float(os.getenv('PAPER_SLIPPAGE','0.001'))
# New BUY orders only. Existing holdings and historical fills are never resized.
RESERVE_PCT=float(os.getenv('PAPER_RESERVE_PCT','40'))
STOCK_PCT=float(os.getenv('PAPER_STOCK_PCT','30'))
if not (0 <= RESERVE_PCT <= 100 and 0 < STOCK_PCT <= 100):
    raise ValueError('PAPER_RESERVE_PCT must be 0..100; PAPER_STOCK_PCT must be >0..100')



def save_json(path, data):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

def save_csv(path, rows, columns):
    tmp=path.with_suffix('.tmp'); pd.DataFrame(rows,columns=columns).to_csv(tmp,index=False,encoding='utf-8-sig'); tmp.replace(path)

def load_data():
    p=pd.read_csv(DATA/'kis_daily_universe.csv',dtype={'ticker':str},encoding='utf-8-sig')
    needed={'ticker','name','date','close','volume'}
    if not needed.issubset(p): raise ValueError(f'일봉 열 누락: {needed-set(p)}')
    p['ticker']=p.ticker.str.zfill(6); p['date']=pd.to_datetime(p.date,errors='coerce').dt.normalize()
    for c in ['close','volume']: p[c]=pd.to_numeric(p[c],errors='coerce')
    p=p.dropna(subset=list(needed)); p=p[(p.close>0)&(p.volume>=0)]
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    p=p[p.date.dt.date < now.date()] if now.time()<time(16) else p[p.date.dt.date<=now.date()]
    p=p.sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last')
    fpath=DATA/'kis_investor_flow.csv'
    f=pd.DataFrame(columns=['ticker','date','foreign_net_qty','institution_net_qty'])
    if fpath.exists():
        f=pd.read_csv(fpath,dtype={'ticker':str},encoding='utf-8-sig')
        required={'ticker','date','foreign_net_qty','institution_net_qty'}
        if not required.issubset(f): raise ValueError(f'수급 열 누락: {required-set(f)}')
        f['ticker']=f.ticker.str.zfill(6); f['date']=pd.to_datetime(f.date,errors='coerce').dt.normalize()
        for c in ['foreign_net_qty','institution_net_qty']: f[c]=pd.to_numeric(f[c],errors='coerce')
        f=f.dropna(subset=list(required)).sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last')
    return p,f

def signals(day, prices, flows, sessions, config=None):
    config = (BALANCED if STRATEGY == "balanced" else ORIGINAL) if config is None else config
    rows=[]
    for code,h0 in prices.items():
        h=h0.loc[:day]
        if len(h)<253 or h.index[-1]!=day or (h.volume.iloc[-21:]<=0).any(): continue
        close=h.close.to_numpy(float); vol=h.volume.to_numpy(float)
        returns=[close[-1]/close[-1-n]-1 for n in (63,126,189,252)]
        score=float(np.dot(returns,[.4,.2,.2,.2]))
        base=vol[-21:-1].mean(); ratio=vol[-1]/base if base>0 else 0
        y=np.log(close[-60:]); x=np.arange(60); slope,intercept=np.polyfit(x,y,1)
        mid=intercept+slope*x; width=max(2*np.std(y-mid),1e-8)
        channel=(y[-1]-mid[-1]+width)/(2*width)
        rows.append({'ticker':code,'name':str(h.name.iloc[-1]),'score':score,
                     'ratio':ratio,'channel_position':float(channel),'channel_up':bool(slope>0)})
    if not rows:return []
    df=pd.DataFrame(rows); df['rs']=np.ceil(df.score.rank(pct=True)*99).clip(1,99)
    df=df[(df.rs>=config['rs'])&(df.ratio>=config['volume'])&df.channel_up&(df.channel_position>=0)&(df.channel_position<=config['channel'])]
    selected=[]
    for _,r in df.iterrows():
        fh=flows.get(r.ticker)
        if fh is None:continue
        fh=fh.loc[:day]
        if len(fh)<5:continue
        lag=sessions.searchsorted(day,side='right')-sessions.searchsorted(fh.index[-1],side='right')
        if lag not in (0,1):continue
        five=fh.tail(5)
        if five.foreign_net_qty.sum()>0 and five.institution_net_qty.sum()>0:
            selected.append({'ticker':r.ticker,'name':r['name'],'rs':int(r.rs),'score':float(r.score)})
    return sorted(selected,key=lambda r:(-r['rs'],-r['score'],r['ticker']))

def compare_signals(day, prices, flows, sessions):
    old=signals(day,prices,flows,sessions,ORIGINAL)
    new=signals(day,prices,flows,sessions,BALANCED)
    a={x['ticker'] for x in old}; b={x['ticker'] for x in new}
    return {'date':day.strftime('%Y-%m-%d'),'original_signals':len(a),
            'balanced_signals':len(b),'additional_signals':len(b-a),
            'original_tickers':';'.join(sorted(a)),'balanced_tickers':';'.join(sorted(b))}

def save_comparison(row):
    path=OUT/'signal_comparison.csv'
    old=pd.read_csv(path,dtype=str).to_dict('records') if path.exists() else []
    old=[r for r in old if r['date']!=row['date']]
    save_csv(path,sorted(old+[row],key=lambda r:r['date']),CC)



def realized_fifo(trades, as_of):
    """Calculate paper realized P&L from historical BUY/SELL fills with FIFO lots.

    Includes prorated recorded BUY commission and full recorded SELL costs.
    Never infers missing historical fills; incomplete journals are reported unverified.
    """
    lots = {}
    daily = 0.0
    cumulative = 0.0
    wins = 0
    losses = 0
    sell_count = 0
    for i, trade in sorted(enumerate(trades), key=lambda v: (str(v[1]['date']), v[0])):
        day = str(trade['date'])
        if day > as_of:
            continue
        ticker = str(trade['ticker']).zfill(6)
        qty = int(trade['qty'])
        price = float(trade['price'])
        fee = float(trade['cost'])
        if qty <= 0 or price <= 0 or fee < 0:
            raise ValueError('Invalid journal price, quantity, or cost')
        if trade['side'] == 'BUY':
            lots.setdefault(ticker, []).append([qty, price + fee / qty])
        elif trade['side'] == 'SELL':
            remaining = qty
            cost_basis = 0.0
            queue = lots.get(ticker, [])
            while remaining:
                if not queue:
                    raise ValueError('Missing BUY lots for a SELL; cannot verify realized P&L')
                available, unit_cost = queue[0]
                taken = min(remaining, available)
                cost_basis += taken * unit_cost
                remaining -= taken
                if taken == available:
                    queue.pop(0)
                else:
                    queue[0][0] -= taken
            pnl = qty * price - fee - cost_basis
            cumulative += pnl
            sell_count += 1
            if day == as_of:
                daily += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        else:
            raise ValueError('Unknown journal side')
    return {'realized_pnl_today': round(daily, 2),
            'realized_pnl_cumulative': round(cumulative, 2),
            'sell_events': sell_count,
            'profitable_sell_events': wins,
            'losing_sell_events': losses,
            'sell_event_win_rate_pct': round(wins / sell_count * 100, 2) if sell_count else None,
            'win_rate_note': 'Win rate counts SELL events (including partial sells), not completed positions.'}


def create_daily_report(state, trades, equity_rows):
    """Report only from persisted paper journal. No invented price or trade data."""
    if not equity_rows or not state.get('last_processed'):
        return
    e=sorted(equity_rows,key=lambda x:str(x['date']))
    today=str(state['last_processed'])
    latest=next((r for r in reversed(e) if str(r['date'])==today),None)
    if latest is None:
        raise RuntimeError('PAPER_REPORT_EQUITY_MISSING')
    earlier=[r for r in e if str(r['date'])<today]
    prior_equity=float(earlier[-1]['equity']) if earlier else float(state['initial_cash'])
    current=float(latest['equity']); init=float(state['initial_cash'])
    if prior_equity <= 0 or init <= 0:
        raise RuntimeError('PAPER_REPORT_INVALID_EQUITY')
    today_trades=[r for r in trades if str(r['date'])==today]
    # A paper BUY is not a realized trading profit or loss.
    closed_today=sum(1 for r in today_trades if r['side']=='SELL')
    fee_today=sum(float(r['cost']) for r in today_trades)
    pending=[{'ticker':o['ticker'],'name':o.get('name',''),
              'side':o['side'],'reason':o.get('reason','')}
             for o in state.get('pending',[])]
    positions=[]
    for ticker,pos in sorted(state.get('positions',{}).items()):
        qty=int(pos['qty']); close=float(pos['last_close']); entry=float(pos['entry'])
        positions.append({'ticker':ticker,'name':pos.get('name',''), 'qty':qty,
                          'last_close':round(close,4), 'entry':round(entry,4),
                          'unrealized_pnl_before_sell_cost':round(qty*(close-entry),2),
                          'unrealized_return_pct':round((close/entry-1)*100,4) if entry else None})
    try:
        realized = realized_fifo(trades, today)
        realized_verified = True
    except (ValueError, KeyError, TypeError, OverflowError):
        realized = {'realized_pnl_today': None, 'realized_pnl_cumulative': None,
                    'sell_events': None, 'profitable_sell_events': None,
                    'losing_sell_events': None, 'sell_event_win_rate_pct': None,
                    'win_rate_note': 'Incomplete/invalid trade ledger; cannot certify realized profit.'}
        realized_verified = False
    report={
        'as_of':today,'mode':'PAPER_ONLY','latest_processed':today,
        'initial_cash':init,'cash':float(latest['cash']),
        'holdings_value':float(latest['holdings_value']),'equity':current,
        'daily_pnl_mark_to_market':round(current-prior_equity,2),
        'daily_return_pct':round((current/prior_equity-1)*100,4),
        'cumulative_pnl':round(current-init,2),
        'cumulative_return_pct':round((current/init-1)*100,4),
        'trade_count_today':len(today_trades),'sell_trade_count_today':closed_today,
        'trade_cost_today':round(fee_today,2),
        'realized_pnl_today':realized['realized_pnl_today'],
        'realized_pnl_cumulative':realized['realized_pnl_cumulative'],
        'realized_pnl_verified':realized_verified,
        'sell_events':realized['sell_events'],
        'profitable_sell_events':realized['profitable_sell_events'],
        'losing_sell_events':realized['losing_sell_events'],
        'sell_event_win_rate_pct':realized['sell_event_win_rate_pct'],
        'realized_pnl_note':realized['win_rate_note'],
        'positions':positions,
        'next_session_strategy':{'mode':'REVIEW_ONLY','pending_orders':pending,
             'checks':['Confirm priority OHLCV for positions and pending tickers',
                       'Check next-session liquidity, flow, RS and gap before action',
                       'Use reserve/trading/stock caps only for future paper BUYs']},
        'allocation_for_future_buys':{'reserve_pct':RESERVE_PCT,
             'trading_pct':100-RESERVE_PCT,'stock_pct_of_trading_budget':STOCK_PCT},
        'orders_sent':0
    }
    save_json(OUT/'daily_review.json',report)
    print(f"PAPER DAILY REVIEW {today}: daily_pnl={report['daily_pnl_mark_to_market']:,.0f} KRW; "
          f"cumulative_pct={report['cumulative_return_pct']:.3f}; "
          f"positions={len(positions)}; pending={len(pending)}; orders_sent=0")


def main():
    if any(not 0<=v<.1 for v in (FEE_BUY,FEE_SELL,TAX_SELL,SLIP)):raise ValueError('수수료·슬리피지 설정 오류')
    OUT.mkdir(parents=True,exist_ok=True)
    p,f=load_data()
    if p.empty:raise RuntimeError('확정 일봉 없음')
    prices={c:g.set_index('date').sort_index() for c,g in p.groupby('ticker')}
    flows={c:g.set_index('date').sort_index() for c,g in f.groupby('ticker')}
    sessions=pd.DatetimeIndex(sorted(p.loc[p.volume>0,'date'].unique()))
    if sessions.empty: raise RuntimeError('거래량 양수인 확정 거래일 없음')
    if STATE.exists():
        state=json.loads(STATE.read_text(encoding='utf-8'))
        if state.get('mode')!='paper_only_v1':raise RuntimeError('모의 계좌 상태 형식 오류')
    else:
        state={'mode':'paper_only_v1','initial_cash':10000000,'cash':10000000.0,
               'positions':{},'pending':[],'last_processed':None,'session_index':-1}
    trades=pd.read_csv(TRADES,dtype={'ticker':str}).to_dict('records') if TRADES.exists() else []
    equity=pd.read_csv(EQUITY).to_dict('records') if EQUITY.exists() else []
    last=pd.Timestamp(state['last_processed']) if state['last_processed'] else None
    todo=[d for d in sessions if last is None or d>last]
    if last is None:
        todo=[sessions[-1]]
        print('신규 계좌: 과거 거래를 소급 생성하지 않고 마지막 확정 거래일부터 시작')
    latest=sessions[-1].strftime('%Y-%m-%d')
    print(f"DATA CHECK: latest_available={latest}; last_processed={state.get('last_processed')}; rows={len(p)}")
    for order in state.get('pending', []):
        code=str(order['ticker']).zfill(6)
        hist=prices.get(code)
        latest_ticker=(hist.index[-1].strftime('%Y-%m-%d') if hist is not None and len(hist) else 'MISSING')
        print(f"PENDING DATA: ticker={code}; side={order['side']}; latest_price_date={latest_ticker}")
    if not todo:
        now=datetime.now(ZoneInfo('Asia/Seoul'))
        # At night, a missing trading date is a failed data dependency, not a healthy run.
        if now.weekday()<5 and now.time()>=time(20,30) and latest<now.strftime('%Y-%m-%d'):
            raise RuntimeError(f'PAPER_DATA_NOT_READY: latest={latest}, expected={now.date()}; no state changed')
        row=compare_signals(sessions[-1],prices,flows,sessions)
        save_comparison(row)
        print(f"새 거래일 없음: 중복 처리하지 않음 / 기존 {row['original_signals']}개, 완화 {row['balanced_signals']}개")
        create_daily_report(state, trades, equity)
        return
    for day in todo:
        date=day.strftime('%Y-%m-%d')
        closes={c:float(h.loc[day,'close']) for c,h in prices.items() if day in h.index and h.loc[day,'volume']>0}
        # No partial fills or state mutation when a due order lacks verified day-price data.
        due_codes=sorted({str(o['ticker']).zfill(6) for o in state['pending'] if o['signal_date']<date})
        missing=[code for code in due_codes if code not in closes]
        if missing:
            raise RuntimeError(f'PAPER_MISSING_ORDER_PRICES: day={date}; tickers={",".join(missing)}; state unchanged for day')
        state['session_index']+=1;idx=state['session_index']
        due=[o for o in state['pending'] if o['signal_date']<date]
        state['pending']=[o for o in state['pending'] if o['signal_date']>=date]
        unfilled=[]
        # Snapshot once per trading day: avoid re-allocating 60% of a shrinking
        # balance for each separate BUY in the same session.
        buy_budget_total = None
        buy_budget_left = None
        buy_cap_stock = None
        for o in sorted(due,key=lambda o:0 if o['side']=='SELL' else 1):
            code=o['ticker'];pos=state['positions'].get(code)
            if code not in closes:
                # Keep a pending sell until a valid quote becomes available.
                # Do not pretend that an unfilled take-profit was executed.
                if o['side']=='SELL' and pos:
                    unfilled.append(o)
                continue
            if o['side']=='SELL':
                if not pos:continue
                qty=min(int(o['qty']),pos['qty']);fill=closes[code]*(1-SLIP)
                cost=qty*fill*(FEE_SELL+TAX_SELL);change=qty*fill-cost
                state['cash']+=change;pos['qty']-=qty
                if '1차 익절' in o.get('reason','') and qty>0:
                    pos['partial']=True
                if pos['qty']==0:del state['positions'][code]
            else:
                if pos or len(state['positions'])>=5:continue
                fill=closes[code]*(1+SLIP)
                total=state['cash']+sum(v['qty']*closes.get(k,v['last_close']) for k,v in state['positions'].items())
                if buy_budget_total is None:
                    # Available paper cash at the first BUY is the allocation base.
                    # Existing portfolio exposure is untouched, not retroactively resized.
                    base_cash=max(0.0,float(state['cash']))
                    buy_budget_total=base_cash*(100-RESERVE_PCT)/100
                    buy_budget_left=buy_budget_total
                    buy_cap_stock=buy_budget_total*STOCK_PCT/100
                    print(f'PAPER BUDGET {date}: available_cash={base_cash:.0f}; reserve_pct={RESERVE_PCT:g}; trading_pct={100-RESERVE_PCT:g}; stock_pct={STOCK_PCT:g}; per_stock_cap={buy_cap_stock:.0f}')
                budget=min(buy_cap_stock,buy_budget_left,max(0.0,float(state['cash'])-base_cash*RESERVE_PCT/100))
                qty=int(budget//(fill*(1+FEE_BUY)))
                if qty<1:continue
                cost=qty*fill*FEE_BUY;change=-qty*fill-cost;state['cash']+=change
                buy_budget_left=max(0.0,buy_budget_left+change)
                state['positions'][code]={'name':o['name'],'qty':qty,'entry':fill,'entry_idx':idx,
                                          'partial':False,'last_close':closes[code]}
            trades.append({'date':date,'ticker':code,'name':o['name'],'side':o['side'],
                           'qty':qty,'price':round(fill,4),'cost':round(cost,2),
                           'cash_change':round(change,2),'reason':o['reason']})
        for code,pos in state['positions'].items():
            if code in closes:pos['last_close']=closes[code]
        pending=list(unfilled)
        for code,pos in state['positions'].items():
            if code not in closes or any(o['ticker']==code and o['side']=='SELL' for o in pending):
                continue
            ret=closes[code]/pos['entry']-1;held=idx-pos['entry_idx']
            if ret<=-.05:reason,qty='손절 신호 -5%',pos['qty']
            elif ret>=.15:reason,qty='익절 신호 +15%',pos['qty']
            elif held>=10:reason,qty='10거래일 청산',pos['qty']
            elif ret>=.08 and not pos['partial']:
                reason,qty='1차 익절 신호 +8%',max(1,pos['qty']//2)
            else:continue
            pending.append({'signal_date':date,'side':'SELL','ticker':code,'name':pos['name'],
                            'qty':qty,'reason':reason})
        row=compare_signals(day,prices,flows,sessions)
        save_comparison(row)
        print(f"신호 비교 {date}: 기존 {row['original_signals']} / 완화 {row['balanced_signals']} / 추가 {row['additional_signals']}")
        picks=signals(day,prices,flows,sessions)
        slots=max(0,5-len(state['positions']))
        for pick in picks:
            if slots==0 or sum(o['side']=='BUY' for o in pending)>=2:break
            code=pick['ticker']
            if code in state['positions'] or any(o['ticker']==code for o in pending):continue
            pending.append({'signal_date':date,'side':'BUY','ticker':code,'name':pick['name'],
                            'reason':f'RS {pick["rs"]} + 수급/거래량/채널'})
            slots-=1
        state['pending']=pending
        holdings=sum(pos['qty']*pos['last_close'] for pos in state['positions'].values())
        equity.append({'date':date,'cash':round(state['cash'],2),'holdings_value':round(holdings,2),
                       'equity':round(state['cash']+holdings,2),'positions':len(state['positions']),
                       'pending':len(pending)})
        state['last_processed']=date
        save_json(STATE,state);save_csv(TRADES,trades,TC);save_csv(EQUITY,equity,EC)
        print(f'{date}: 가상 총자산 {state["cash"]+holdings:,.0f}원 / 보유 {len(state["positions"])} / 다음 거래일 대기 주문 {len(pending)}')
    create_daily_report(state, trades, equity)
    print('모의매매 완료. 실제 주문 API는 호출하지 않았습니다.')

if __name__=='__main__':main()
