import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from oneil_execution_v2.domain import (Approval, AccountSnapshot, Blocked, BrokerOrder,
    Order, Quote, RiskPolicy, SessionPermit, check_risk)

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,5,1,tzinfo=timezone.utc)
        self.order = Order('a'*64,'005930','BUY','KRX',2,10000,self.now+timedelta(minutes=1),'v1')
        self.policy = RiskPolicy('v1',100000,1000000,500000,2000000,100000)
        self.quote = Quote('005930','KRX',10000,self.now,True)
        self.snapshot = AccountSnapshot('a'*64,self.now,3000000,2000000,0,0,{},True)
        self.session = SessionPermit('KRX','2026-11-05',self.now-timedelta(seconds=1),self.now+timedelta(minutes=1),'00','verified-fixture',self.now)

    def check(self, **kwargs):
        args=dict(order=self.order,policy=self.policy,quote=self.quote,account=self.snapshot,
                  permit=self.session,now=self.now,daily_buy_krw=0)
        args.update(kwargs)
        return check_risk(**args)

    def test_valid(self): self.check()
    def test_money_rejects_bool_float_negative(self):
        for value in (True, 1.5, -1, 0):
            with self.subTest(value=value), self.assertRaises(Blocked):
                replace(self.order,quantity=value).validate()
    def test_bad_identity(self):
        for field,value in [('account','raw-account'),('ticker','AAPL'),('venue','AUTO'),('side','SHORT')]:
            with self.subTest(field=field), self.assertRaises(Blocked):
                replace(self.order,**{field:value}).validate()
    def test_naive_clock(self):
        with self.assertRaises(Blocked): replace(self.order,expires_at=datetime.now()).validate()
    def test_digest_content_bound(self):
        self.assertNotEqual(self.order.digest,replace(self.order,quantity=3).digest)
        self.assertEqual(self.order,Order.from_payload(self.order.payload()))
    def test_approval_binding(self):
        approval=Approval('a'*32,'owner',self.order.digest,self.order.expires_at)
        approval.validate(self.order,self.now)
        with self.assertRaisesRegex(Blocked,'CONTENT_MISMATCH'): approval.validate(replace(self.order,quantity=3),self.now)
    def test_approval_expired(self):
        with self.assertRaisesRegex(Blocked,'EXPIRED'):
            Approval('a'*32,'owner',self.order.digest,self.now).validate(self.order,self.now)
    def test_future_and_stale_quotes(self):
        for offset in (-6,1):
            with self.subTest(offset=offset), self.assertRaisesRegex(Blocked,'QUOTE_STALE'):
                self.check(quote=replace(self.quote,observed_at=self.now+timedelta(seconds=offset)))
    def test_wrong_venue_quote(self):
        with self.assertRaisesRegex(Blocked,'QUOTE_NOT_EXECUTABLE'): self.check(quote=replace(self.quote,venue='NXT'))
    def test_price_deviation(self):
        with self.assertRaisesRegex(Blocked,'PRICE_DEVIATION'): self.check(order=replace(self.order,limit_price=10101))
    def test_unverified_account(self):
        for account in (replace(self.snapshot,complete=False),replace(self.snapshot,account='b'*64),replace(self.snapshot,observed_at=self.now-timedelta(seconds=11))):
            with self.subTest(account=account), self.assertRaisesRegex(Blocked,'ACCOUNT_UNVERIFIED'): self.check(account=account)
    def test_daily_loss_buy_block_sell_allowed(self):
        snapshot=replace(self.snapshot,daily_loss_krw=100000,positions={'005930':{'quantity':2,'market_value_krw':20000}})
        with self.assertRaisesRegex(Blocked,'DAILY_LOSS_LIMIT'): self.check(account=snapshot)
        self.check(order=replace(self.order,side='SELL'),account=snapshot)
    def test_daily_budget(self):
        with self.assertRaisesRegex(Blocked,'DAILY_BUY_LIMIT'): self.check(daily_buy_krw=990000)
    def test_order_limit(self):
        with self.assertRaisesRegex(Blocked,'ORDER_LIMIT'): self.check(policy=replace(self.policy,max_order_krw=19999))
    def test_cash_limit(self):
        with self.assertRaisesRegex(Blocked,'INSUFFICIENT_CASH'): self.check(account=replace(self.snapshot,cash_available_krw=19999))
    def test_gross_limit(self):
        with self.assertRaisesRegex(Blocked,'GROSS_EXPOSURE_LIMIT'): self.check(account=replace(self.snapshot,gross_exposure_krw=1990000))
    def test_position_limit(self):
        with self.assertRaisesRegex(Blocked,'POSITION_LIMIT'): self.check(account=replace(self.snapshot,positions={'005930':{'quantity':49,'market_value_krw':490000}}))
    def test_no_naked_sell(self):
        with self.assertRaisesRegex(Blocked,'SELL_EXCEEDS_POSITION'): self.check(order=replace(self.order,side='SELL'))
    def test_closed_and_mismatched_sessions(self):
        for permit in (replace(self.session,closes_at=self.now),replace(self.session,venue='NXT'),replace(self.session,trading_day='2026-11-04'),replace(self.session,reference='')):
            with self.subTest(permit=permit), self.assertRaises(Blocked): self.check(permit=permit)
    def test_policy_changed(self):
        with self.assertRaisesRegex(Blocked,'POLICY_CHANGED'): self.check(policy=replace(self.policy,version='v2'))
    def test_fill_validation(self):
        obs=BrokerOrder('r',self.order.digest,'b','a'*64,'2026-11-05','005930','BUY','KRX',2,10000,'FILLED',2)
        obs.validate('r',self.order,'2026-11-05')
        for bad in (replace(obs,filled_quantity=1),replace(obs,quantity=3),replace(obs,venue='NXT'),replace(obs,status='PART_FILLED')):
            with self.subTest(bad=bad), self.assertRaises(Blocked): bad.validate('r',self.order,'2026-11-05')

if __name__=='__main__': unittest.main()
