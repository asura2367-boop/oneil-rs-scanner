import unittest, tempfile
from pathlib import Path
import oneil_v519_live_runner as r
from oneil_v519_order_recovery import OrderJournal

class TestV519Operational(unittest.TestCase):
    def test_stop(self):
        self.assertEqual(r.determine_exit(held_qty=10,average_cost_krw=10000,reference_price_krw=9400)['decision'],'STOP_LOSS')
    def test_takeprofit(self):
        self.assertEqual(r.determine_exit(held_qty=10,average_cost_krw=10000,reference_price_krw=11100)['decision'],'TAKE_PROFIT')
    def test_hold(self):
        self.assertEqual(r.determine_exit(held_qty=10,average_cost_krw=10000,reference_price_krw=10300)['max_sell_qty'],0)
    def test_overholding(self):
        x=r.determine_exit(held_qty=3,average_cost_krw=10000,reference_price_krw=9000)
        self.assertEqual(x['max_sell_qty'],3)
        self.assertEqual(x['orders_sent'],0)
    def test_bad_cost(self):
        with self.assertRaises(r.Blocked):r.determine_exit(held_qty=1,average_cost_krw=0,reference_price_krw=10000)
    def test_bad_stop(self):
        with self.assertRaises(r.Blocked):r.determine_exit(held_qty=1,average_cost_krw=10000,reference_price_krw=10000,stop_loss_pct=0)
    def test_status_with_pending(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);j=OrderJournal(path/'orders.sqlite')
            j.prepare('a'*20,'144960','BUY','KRX',3);j.close()
            x=r.local_status(path)
            self.assertEqual(x['pending_orders'],1)
            self.assertFalse(x['can_start_new_orders'])
    def test_readonly_gate(self):
        self.assertEqual(r.determine_exit(held_qty=1,average_cost_krw=10000,reference_price_krw=8000)['gate'],'DENY')
