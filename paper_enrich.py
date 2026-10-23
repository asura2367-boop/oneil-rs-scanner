"""Paper-trading reporting only. No broker access, no simulated fills.
Run immediately after `python paper_trade.py` in GitHub Actions.
"""
import csv
import json
import math
import os
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = DATA / 'paper'
STATE = OUT / 'state.json'
SUMMARY = OUT / 'paper_summary.json'
ORDERS = OUT / 'pending_report.csv'
TRADE_DETAIL = OUT / 'closed_trades_report.csv'


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def money(x):
    try:
        val = float(x)
        return val if math.isfinite(val) else 0.0
    except (TypeError, ValueError):
        return 0.0


def write_csv(path, rows, fields):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def latest_prices():
    latest = {}
    for row in read_csv(DATA / 'kis_daily_universe.csv'):
        ticker = row.get('ticker', '').strip().zfill(6)
        date = row.get('date', '')[:10]
        close = money(row.get('close'))
        if ticker and date and close > 0 and (ticker not in latest or date > latest[ticker][0]):
            latest[ticker] = (date, close)
    return latest


def analyze_trades(trades):
    # FIFO closed trade accounting; buy fees capitalized, sell tax/fees deducted.
    lots = defaultdict(deque)
    closed = []
    realized = 0.0
    for row in trades:
        side = str(row.get('side', '')).upper()
        ticker = str(row.get('ticker', '')).zfill(6)
        qty = int(money(row.get('qty')))
        if qty <= 0 or side not in ('BUY', 'SELL'):
            continue
        price = money(row.get('price'))
        total_cost = money(row.get('cost'))
        if side == 'BUY':
            lots[ticker].append([qty, price + total_cost / qty, row.get('date', '')])
        else:
            left = qty
            while left > 0 and lots[ticker]:
                lot = lots[ticker][0]
                matched = min(left, lot[0])
                buy_cost = matched * lot[1]
                net_proceeds = matched * (price - total_cost / qty)
                pnl = net_proceeds - buy_cost
                realized += pnl
                closed.append({
                    'ticker': ticker, 'name': row.get('name', ''),
                    'entry_date': lot[2], 'exit_date': row.get('date', ''),
                    'qty': matched, 'buy_cost': round(buy_cost, 2),
                    'sell_proceeds': round(net_proceeds, 2),
                    'realized_pnl': round(pnl, 2),
                    'return_pct': round(100 * pnl / buy_cost, 3) if buy_cost else 0,
                })
                lot[0] -= matched
                left -= matched
                if lot[0] == 0:
                    lots[ticker].popleft()
            if left:
                print(f'WARNING: SELL {ticker} {left} shares have no matching BUY history')
    wins = sum(c['realized_pnl'] > 0 for c in closed)
    return closed, round(realized, 2), wins


def main():
    if not STATE.exists():
        raise FileNotFoundError(f'Missing simulation state: {STATE}')
    state = json.loads(STATE.read_text(encoding='utf-8'))
    prices = latest_prices()
    slip = float(os.getenv('PAPER_SLIPPAGE', '0.001'))
    fee = float(os.getenv('PAPER_BUY_FEE', '0.00015'))
    if not 0 <= slip < 0.1 or not 0 <= fee < 0.1:
        raise ValueError('Invalid paper slippage or fee')
    initial = money(state.get('initial_cash', 10000000))
    cash = money(state.get('cash'))
    holdings = state.get('positions', {})
    equity_rows = read_csv(OUT / 'equity.csv')
    last_equity = money(equity_rows[-1].get('equity')) if equity_rows else cash + sum(
        money(pos.get('last_close')) * int(pos.get('qty', 0)) for pos in holdings.values()
    )

    # Estimates only: actual next-day simulated fills use NEXT session's close.
    pending_rows = []
    for order in state.get('pending', []):
        ticker = str(order.get('ticker', '')).zfill(6)
        known = prices.get(ticker)
        last_date, last_close = known if known else ('', 0)
        approx_price = last_close * (1 + slip) if order.get('side') == 'BUY' else last_close * (1 - slip)
        if order.get('side') == 'BUY':
            budget = min(last_equity * .2, cash)
            qty = int(budget // (approx_price * (1 + fee))) if approx_price else 0
        else:
            budget = 0
            qty = int(order.get('qty', 0))
        pending_rows.append({
            'signal_date': order.get('signal_date', ''), 'side': order.get('side', ''),
            'ticker': ticker, 'name': order.get('name', ''),
            'reason': order.get('reason', ''), 'reference_date': last_date,
            'reference_close': round(last_close, 2) if last_close else '',
            'indicative_price': round(approx_price, 2) if approx_price else '',
            'estimated_qty': qty, 'estimated_gross': round(qty * approx_price, 2),
            'estimated_fee': round(qty * approx_price * fee, 2) if order.get('side') == 'BUY' else '',
            'note': 'ESTIMATE ONLY; execution depends on next session close and available cash' if order.get('side') == 'BUY' else 'SELL: estimated; actual close can differ',
        })
    fields = ['signal_date', 'side', 'ticker', 'name', 'reason', 'reference_date',
              'reference_close', 'indicative_price', 'estimated_qty', 'estimated_gross', 'estimated_fee', 'note']
    write_csv(ORDERS, pending_rows, fields)
    closed, realized, wins = analyze_trades(read_csv(OUT / 'trades.csv'))
    write_csv(TRADE_DETAIL, closed, ['ticker', 'name', 'entry_date', 'exit_date', 'qty', 'buy_cost',
                                      'sell_proceeds', 'realized_pnl', 'return_pct'])
    peak = initial
    worst = 0.0
    for row in equity_rows:
        value = money(row.get('equity'))
        if value <= 0:
            continue
        peak = max(peak, value)
        worst = min(worst, (value / peak - 1) * 100)
    result = {
        'report_generated_at_kst': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'last_processed': state.get('last_processed'),
        'initial_cash': initial, 'cash': cash,
        'equity': last_equity,
        'total_return_pct': round((last_equity / initial - 1) * 100, 3) if initial else None,
        'positions': len(holdings), 'pending_orders': len(pending_rows),
        'closed_lots': len(closed), 'wins': wins,
        'win_rate_pct': round(wins / len(closed) * 100, 2) if closed else None,
        'realized_pnl': realized, 'max_drawdown_pct': round(worst, 3),
        'estimate_warning': 'Pending prices/quantities are indicative only, not live quotes or actual fills.',
        'pending': pending_rows,
    }
    tmp = SUMMARY.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(SUMMARY)
    print('Paper report:', result['last_processed'], '| pending:', len(pending_rows),
          '| return:', result['total_return_pct'], '% | closed lots:', len(closed),
          '| win rate:', result['win_rate_pct'])


if __name__ == '__main__':
    main()
