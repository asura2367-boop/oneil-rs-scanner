import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Blocked, Order, RiskPolicy, check_risk

class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.broker=SimulatedBroker(lambda:self.now,cash_krw=10000,behavior='fill')
        self.order=Order('a'*64,'005930','BUY','KRX',1,10000,self.now+timedelta(minutes=1),'v1')
        self.policy=RiskPolicy('v1',10000,30000,10000,10000,1000)

    def test_fills_reduce_cash_and_increase_exposure(self):
        b=self.broker; o=self.order
        b.submit('one',o,b.session(o)); snapshot=b.account(o.account)
        self.assertEqual(snapshot.cash_available_krw,0)
        self.assertEqual(snapshot.positions[o.ticker]['quantity'],1)
        self.assertEqual(snapshot.gross_exposure_krw,10000)
        self.assertEqual(snapshot.fill_watermarks,{'one':1})
        with self.assertRaisesRegex(Blocked,'INSUFFICIENT_CASH'):
            check_risk(o,self.policy,b.quote(o),snapshot,b.session(o),self.now,10000)

    def test_balances_isolated_by_account(self):
        self.broker.submit('one',self.order,self.broker.session(self.order))
        self.assertEqual(self.broker.account('b'*64).cash_available_krw,10000)
        self.assertEqual(self.broker.account('b'*64).positions,{})

    def test_single_unit_partial_is_full_fill(self):
        self.broker.behavior='partial'
        result=self.broker.submit('one',self.order,self.broker.session(self.order))
        self.assertEqual(result.status,'FILLED')
        result.validate('one',self.order,result.trading_day)

    def test_partial_fill_applied_once_per_delta(self):
        b=SimulatedBroker(lambda:self.now,cash_krw=50000,behavior='partial')
        o=replace(self.order,quantity=4)
        b.submit('one',o,b.session(o))
        self.assertEqual(b.account(o.account).cash_available_krw,30000)
        b.advance('one',status='PART_FILLED',filled_quantity=2)
        self.assertEqual(b.account(o.account).cash_available_krw,30000)
        b.advance('one',status='FILLED',filled_quantity=4)
        self.assertEqual(b.account(o.account).cash_available_krw,10000)
        self.assertEqual(b.account(o.account).fill_watermarks,{'one':4})

    def test_sells_reduce_holdings(self):
        b=SimulatedBroker(lambda:self.now,cash_krw=0,positions={'005930':{'quantity':1,'market_value_krw':10000}},behavior='fill')
        o=replace(self.order,side='SELL')
        b.submit('one',o,b.session(o))
        snapshot=b.account(o.account)
        self.assertEqual(snapshot.cash_available_krw,10000)
        self.assertNotIn('005930', snapshot.positions)
        with self.assertRaisesRegex(Blocked,'SELL_EXCEEDS_POSITION'):
            check_risk(o,self.policy,b.quote(o),snapshot,b.session(o),self.now,0)

    def test_lost_ack_still_changes_economics_when_filled(self):
        b=self.broker
        b.behavior='fill_timeout_after_accept'
        with self.assertRaises(TimeoutError):
            b.submit('one',self.order,b.session(self.order))
        self.assertEqual(b.account(self.order.account).cash_available_krw,0)
        self.assertEqual(len(b.calls),1)

    def test_invalid_advance_cannot_mutate_economics(self):
        b=SimulatedBroker(lambda:self.now,cash_krw=30000)
        b.submit('one',self.order,b.session(self.order))
        before=b.account(self.order.account)
        with self.assertRaisesRegex(Blocked,'BAD_FILL'):
            b.advance('one',status='FILLED',filled_quantity=2)
        self.assertEqual(b.account(self.order.account),before)
        self.assertEqual(b.orders['one'].status,'ACKNOWLEDGED')

    def test_duplicate_attempt_is_visible_failure(self):
        b=self.broker
        b.submit('one',self.order,b.session(self.order))
        with self.assertRaisesRegex(Blocked,'SIMULATOR_DUPLICATE_ATTEMPT'):
            b.submit('one',self.order,b.session(self.order))
        self.assertEqual(len(b.calls),2)
        self.assertEqual(b.account(self.order.account).cash_available_krw,0)

if __name__=='__main__': unittest.main()
