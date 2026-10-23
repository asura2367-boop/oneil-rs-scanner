"""Isolated multi-session regression tests for the uploaded paper_v4c.py."""
import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import paper_v4c as engine

QHEAD=['received_at_kst','ticker','price','cumulative_volume','api_code','quote_status']
CHEAD=['date','ticker','rs','foreign_5d','institution_5d','volume_ratio','channel_position']

class V4CMultidayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.output=self.base/'v4c'
        self.candidates=self.base/'candidates.csv'
        self.day=date(2026,10,23)
        self.qn=0

    def csvfile(self,path,header,records):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(header);w.writerows(records)

    def run_day(self, day, price, *, volume=10000, candidate=False, ticker='024950'):
        d=day.isoformat()
        quote=self.base/f'quotes_{d}.csv'
        self.csvfile(quote,QHEAD,[[f'{d}T10:00:00+09:00',ticker,price,volume,'MCA00000','snapshot_only']])
        self.csvfile(self.candidates,CHEAD,[[d,ticker,90,100,100,1.2,.4]] if candidate else [])
        return engine.run(quote,self.candidates,self.output)

    def transactions(self):
        return engine.rows(self.output/'trades.csv')

    def test_buy_then_hold_across_sessions(self):
        self.run_day(self.day,5000,candidate=True)
        first=json.loads((self.output/'state.json').read_text())
        self.assertEqual(len(first['positions']),1)
        qty=first['positions']['024950']['qty']
        self.run_day(self.day+timedelta(days=3),5100)
        s=json.loads((self.output/'state.json').read_text())
        self.assertEqual(s['positions']['024950']['qty'],qty)
        self.assertEqual(s['session_index'],2)
        self.assertEqual(len(self.transactions()),1)

    def test_stop_loss(self):
        self.run_day(self.day,5000,candidate=True)
        self.run_day(self.day+timedelta(days=3),4700)
        self.assertEqual(len(self.transactions()),2)
        self.assertEqual(self.transactions()[-1]['reason'],'stop -5%')
        self.assertEqual(engine.new_state()['initial_cash'],10000000)

    def test_partial_then_full_profit(self):
        self.run_day(self.day,5000,candidate=True)
        self.run_day(self.day+timedelta(days=3),5450)
        p=json.loads((self.output/'state.json').read_text())['positions']['024950']
        self.assertTrue(p['partial_done'])
        self.assertGreater(p['qty'],0)
        self.run_day(self.day+timedelta(days=4),5800)
        self.assertEqual([x['reason'] for x in self.transactions()],['pre-session candidate','partial +8%','take +15%'])

    def test_time_exit_on_tenth_elapsed_session(self):
        self.run_day(self.day,5000,candidate=True)
        for i in range(1,11):
            self.run_day(self.day+timedelta(days=i),5000)
        self.assertEqual(self.transactions()[-1]['reason'],'10-session exit')
        self.assertEqual(json.loads((self.output/'state.json').read_text())['positions'],{})

    def test_duplicate_day_and_zero_volume(self):
        with self.assertRaisesRegex(ValueError,'No valid snapshots'):
            self.run_day(self.day,5000,candidate=True,volume=0)
        self.assertFalse((self.output/'state.json').exists())
        next_day=self.day+timedelta(days=3)
        self.run_day(next_day,5000,candidate=True)
        before=(self.output/'trades.csv').read_bytes()
        self.run_day(next_day,5000,candidate=True)
        self.assertEqual(before,(self.output/'trades.csv').read_bytes())

if __name__=='__main__':
    unittest.main(verbosity=2)
