import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_live_v5_4 import gate, Denied
NOW=dt.datetime(2026,10,26,19,0,tzinfo=ZoneInfo('Asia/Seoul'))
def valid():
    return dict(schema_version=1,as_of='2026-10-26',
        broker_cash_verified=True,positions_verified=True,unfilled_verified=True,
        daily_loss_verified=True,quotes_fresh_verified=True,exchange_tradability_verified=True,
        broker_history_reconciled=True,emergency_halt=False,daily_loss_krw=0,
        daily_loss_limit_krw=20000,buying_power_krw=1000000,
        today_reserved_and_filled_buy_krw=0,evening_spent_and_reserved_krw=0,
        candidates=[dict(ticker='052460',broker_held=False,unfilled_buy=False,
             indicative_limit_price_krw=5000,existing_exposure_krw=0,
             broker_symbol_buying_power_krw=1000000)])
class GateTests(unittest.TestCase):
    def test_never_authorizes_orders(self):
        r=gate(valid(),NOW)
        self.assertEqual(r['orders_sent'],0)
        self.assertFalse(r['actual_broker_trade_authorization'])
        self.assertEqual(r['trading_budget_krw'],600000)
        self.assertEqual(r['per_stock_cap_krw'],180000)
        self.assertEqual(r['evening_budget_cap_krw'],180000)
    def test_missing_evidence_denied(self):
        d=valid();d['daily_loss_verified']=False
        with self.assertRaises(Denied):gate(d,NOW)
    def test_stale_denied(self):
        d=valid();d['as_of']='2026-10-23'
        with self.assertRaises(Denied):gate(d,NOW)
    def test_halt_denied(self):
        d=valid();d['emergency_halt']=True
        with self.assertRaises(Denied):gate(d,NOW)
    def test_existing_exposure(self):
        d=valid();d['candidates'][0]['existing_exposure_krw']=180000
        self.assertEqual(gate(d,NOW)['intents'][0]['qty'],0)
    def test_market_closed(self):
        with self.assertRaises(Denied):gate(valid(),NOW.replace(hour=20,minute=1))
    def test_duplicate_code_denied(self):
        d=valid();d['candidates']*=2
        with self.assertRaises(Denied):gate(d,NOW)
    def test_configurable_ratios(self):
        r=gate(valid(),NOW,50,20,10)
        self.assertEqual(r['trading_budget_krw'],500000)
        self.assertEqual(r['per_stock_cap_krw'],100000)
        self.assertEqual(r['evening_budget_cap_krw'],50000)
if __name__=='__main__':unittest.main()
