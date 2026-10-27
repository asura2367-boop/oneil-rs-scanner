import unittest
from oneil_buying_power_guard import examine

class GuardTests(unittest.TestCase):
    def test_valid_read_only_denies(self):
        r=examine([dict(ticker='085910',broker_orderable_cash=100000,broker_orderable_qty=10,query_price=5000)],held=set(),unfilled=set())
        self.assertEqual(r['gate'],'DENY')
        self.assertEqual(r['orders_sent'],0)
        self.assertFalse(r['broker_buying_power_verified'])
        self.assertNotIn('broker_orderable_cash',str(r))
    def test_unknown_state_denies(self):
        r=examine([dict(ticker='085910',broker_orderable_cash=1,broker_orderable_qty=1,query_price=1)])
        self.assertIn('ACCOUNT_STATE_NOT_VERIFIED',r['checks'][0]['block_codes'])
    def test_duplicate_denied(self):
        with self.assertRaises(ValueError):examine([{'ticker':'085910'},{'ticker':'085910'}])
    def test_negative_cash_invalid(self):
        r=examine([dict(ticker='085910',broker_orderable_cash=-1,broker_orderable_qty=10,query_price=5)])
        self.assertIn('BUYING_POWER_RESPONSE_INVALID',r['checks'][0]['block_codes'])
    def test_even_all_flags_denies(self):
        r=examine([dict(ticker='085910',broker_orderable_cash=100,broker_orderable_qty=10,query_price=5)],held=set(),unfilled=set(),price_attested=True,daily_loss_verified=True,baseline_verified=True)
        self.assertFalse(r['checks'][0]['orderable_qty_approved'])
    def test_bad_percentage(self):
        with self.assertRaises(ValueError):examine([],reserve_pct=101)
if __name__=='__main__':unittest.main()
