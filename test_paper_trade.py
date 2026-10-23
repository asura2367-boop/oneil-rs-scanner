"""Isolated regression tests for paper_trade.py. Never touches data/paper or broker APIs.
Run: python -m unittest -v test_paper_trade.py
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import paper_trade as pt


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.patches = [
            patch.object(pt, 'OUT', folder),
            patch.object(pt, 'STATE', folder / 'state.json'),
            patch.object(pt, 'TRADES', folder / 'trades.csv'),
            patch.object(pt, 'EQUITY', folder / 'equity.csv'),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.dates = pd.bdate_range('2026-09-01', periods=15)

    def feed(self, prices):
        rows = [dict(ticker='000001', name='테스트종목', date=d, close=float(v), volume=10000)
                for d, v in zip(self.dates, prices)]
        return pd.DataFrame(rows), pd.DataFrame(columns=['ticker','date','foreign_net_qty','institution_net_qty'])

    def seed(self, entry=100, qty=100, partial=False, index=0):
        state = dict(mode='paper_only_v1', initial_cash=10000000, cash=9990000.0,
                     positions={'000001':dict(name='테스트종목', qty=qty, entry=entry,
                                              entry_idx=index, partial=partial, last_close=entry)},
                     pending=[], last_processed=(self.dates[0]-pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
                     session_index=-1)
        pt.STATE.write_text(json.dumps(state), encoding='utf-8')

    def run_engine(self, prices, picks=None):
        data = self.feed(prices)
        with patch.object(pt, 'load_data', return_value=data), patch.object(pt, 'signals', return_value=picks or []):
            pt.main()
        state = json.loads(pt.STATE.read_text(encoding='utf-8'))
        trades = pd.read_csv(pt.TRADES) if pt.TRADES.exists() else pd.DataFrame()
        return state, trades

    def test_buy_signal_fills_next_session_and_does_not_repeat(self):
        self.seed(qty=0)
        state = json.loads(pt.STATE.read_text(encoding='utf-8'))
        state['positions'] = {}
        state['cash'] = 10000000.0
        pt.STATE.write_text(json.dumps(state), encoding='utf-8')
        with patch.object(pt, 'load_data', return_value=self.feed([100]*15)):
            with patch.object(pt, 'signals', side_effect=lambda day, *_: [dict(ticker='000001', name='테스트종목', rs=95, score=1.0)] if day == self.dates[0] else []):
                pt.main()
        trades = pd.read_csv(pt.TRADES)
        buys = trades[trades.side == 'BUY']
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys.iloc[0]['date'], self.dates[1].strftime('%Y-%m-%d'))
        self.assertLessEqual(-float(buys.iloc[0]['cash_change']), 2000000.01)
        before = len(trades)
        with patch.object(pt, 'load_data', return_value=self.feed([100]*15)), patch.object(pt, 'signals', return_value=[]):
            pt.main()
        self.assertEqual(len(pd.read_csv(pt.TRADES)), before)

    def test_stop_loss(self):
        self.seed()
        state, trades = self.run_engine([100, 94, 94]+[94]*12)
        self.assertTrue(any('손절' in str(x) for x in trades.get('reason', [])))
        self.assertEqual(len(state['positions']), 0)

    def test_first_take_profit_half(self):
        self.seed()
        state, trades = self.run_engine([100, 109, 109]+[109]*12)
        sells = trades[trades.side == 'SELL']
        self.assertGreaterEqual(len(sells), 1)
        self.assertEqual(int(sells.iloc[0]['qty']), 50)
        self.assertIn('1차 익절', sells.iloc[0]['reason'])

    def test_second_take_profit(self):
        self.seed()
        state, trades = self.run_engine([100, 116, 116]+[116]*12)
        self.assertTrue(any('+15%' in str(x) for x in trades.get('reason', [])))
        self.assertEqual(len(state['positions']), 0)

    def test_ten_session_exit(self):
        self.seed()
        state, trades = self.run_engine([100]*15)
        self.assertTrue(any('10거래일' in str(x) for x in trades.get('reason', [])))
        self.assertEqual(len(state['positions']), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
