"""Offline tests only: all quote, power and order transport calls are fixtures."""
import copy
import datetime as dt
import unittest
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

import oneil_v519_live_engine as engine
from oneil_v519_market_session import execution_session, limit_order_code

KST = ZoneInfo('Asia/Seoul')

def at(hour, minute=0, second=0):
    return dt.datetime(2026,11,5,hour,minute,second,tzinfo=KST)


def frozen(now):
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    return patch.object(engine.dt,'datetime',Clock)


class SessionTests(unittest.TestCase):
    def test_boundaries_and_codes(self):
        for hour,minute,second,session in [
            (8,59,59,None),(9,0,0,'REGULAR'),(15,15,0,'REGULAR'),
            (15,19,59,'REGULAR'),(15,20,0,None),(15,40,0,None),
            (15,59,59,None),(16,0,0,'AFTER'),(19,59,59,'AFTER'),(20,0,0,None)]:
            with self.subTest(time=(hour,minute,second)):
                self.assertEqual(execution_session(at(hour,minute,second)),session)
        self.assertEqual(limit_order_code('REGULAR'),'00')
        self.assertEqual(limit_order_code('AFTER'),'41')
        for value in (None,'NXT','MARKET',''):
            with self.assertRaises(ValueError):limit_order_code(value)

    def test_timezone_weekend_effective_date_and_naive_clock(self):
        self.assertEqual(execution_session(at(17).astimezone(dt.timezone.utc)),'AFTER')
        self.assertIsNone(execution_session(at(17).replace(day=7)))
        self.assertIsNone(execution_session(at(17).replace(month=9,day=11)))
        with self.assertRaises(ValueError):execution_session(at(17).replace(tzinfo=None))

    def test_activation_window_includes_exchange_break_but_not_orders(self):
        self.assertTrue(engine.monitoring_open(at(15,40)))
        self.assertIsNone(execution_session(at(15,40)))
        self.assertTrue(engine.monitoring_open(at(19,59,59)))
        self.assertFalse(engine.monitoring_open(at(20)))


class EveningInputsTests(unittest.TestCase):
    def request(self, created, expires):
        return {'id':'11111111-1111-4111-8111-111111111111','ticker':'005930',
            'quantity':1,'limit_price':10000,'holding_quantity':10,'buy_revision':1,
            'risk_revision':0,'account_ref':'a'*64,'approved_release':'b'*64,
            'trading_day':created.date().isoformat(),'created_at':created.isoformat(),
            'expires_at':expires.isoformat()}

    def check(self, created, expires, now=None):
        return engine.manual_buy_request(self.request(created,expires),now=now or created,
            fingerprint='a'*64,release='b'*64)

    def test_manual_requests_end_at_current_session_close(self):
        self.check(at(15,10),at(15,20));self.check(at(19,40),at(20))
        self.check(at(16),at(16,40))
        for created,expires,now in [(at(15,10),at(15,21),None),
            (at(15,40),at(16,10),None),(at(19,40),at(20,1),None),
            (at(16),at(16,41),None),(at(19,40),at(20),at(20)),
            (at(15,10),at(15,20),at(16))]:
            with self.assertRaises(engine.TradingBlocked):self.check(created,expires,now)

    def test_stale_scan_does_not_prevent_readonly_activation_inputs(self):
        with patch.object(engine,'scan_candidates',side_effect=engine.TradingBlocked('stale')):
            self.assertEqual(engine.activation_candidates('unused',at(17)),
                ([], 'BLOCKED_INVALID_OR_STALE_SOURCE'))

    def test_audit_crossing_close_cannot_certify_regular_power_as_after(self):
        broker=Mock()
        broker.snapshot.return_value={'positions':{'005930':{}}}
        broker.history.return_value=[]
        broker.execution_quote.return_value=10000
        broker.power.return_value=(100000,10)
        with frozen(at(20)):
            result=engine.audit_broker_evidence(broker,[],now=at(19,59,59),lookback_days=1)
        self.assertFalse(result['after_market_path_verified'])
        self.assertFalse(result['buying_power_read_verified'])
        self.assertIn('ORDER_SESSION_CHANGED',result['errors'])
        broker.power.assert_not_called()

    def test_evening_rank_is_same_day_input_not_a_new_evening_scan(self):
        row={'ticker':'005930','date':'2026-11-05','rs':99,'observed_at':'2026-11-05T14:00:00+09:00'}
        with patch('oneil_live_v5_9.analyze_candidates',return_value={'reason_codes':[],'review_candidates':[row]}):
            self.assertEqual(engine.scan_candidates('unused',at(17)),[row])
        row={**row,'date':'2026-11-04'}
        with patch('oneil_live_v5_9.analyze_candidates',return_value={'reason_codes':[],'review_candidates':[row]}):
            with self.assertRaisesRegex(engine.TradingBlocked,'CANDIDATE_SOURCE_INVALID_OR_STALE'):
                engine.scan_candidates('unused',at(17))


class EveningBrokerTests(unittest.TestCase):
    def setUp(self):
        self.now=at(17)
        self.info={'pdno':'005930','scty_grp_id_cd':'ST','mket_id_cd':'STK','tr_stop_yn':'N'}
        self.status={'stck_shrn_iscd':'005930','temp_stop_yn':'N','short_over_yn':'N',
            'sltr_yn':'N','iscd_stat_cls_code':'55'}
        self.book={'aspr_acpt_hour':'170000','askp1':'10000','bidp1':'9990',
            'askp_rsqn1':'10','bidp_rsqn1':'20','new_mkop_cls_code':'20'}
        self.detail={'stck_shrn_iscd':'005930','vi_cls_code':'N'}
        self.day='20261105';self.calls=[]
        b=self.broker=object.__new__(engine.KisBroker)
        b.execution=True;b.quote_evidence={};b.account='12345678';b.product='01'
        b.key=b.secret=b.access='TEST_ONLY_NOT_A_CREDENTIAL'
        b.buy_control=lambda:{'sell':{'enabled':True,'connected':True,'revision':1}}
        def request(path,tr,params,**kwargs):
            self.calls.append((path,tr,params,kwargs))
            if kwargs.get('post'):
                return None,{'rt_cd':'0','output':{'ODNO':'123'}}
            if path.endswith('search-stock-info'):return None,{'output':self.info}
            if path.endswith('inquire-price'):return None,{'output':self.status}
            if path.endswith('inquire-daily-price'):return None,{'output':[{'stck_bsop_date':self.day}]}
            if path.endswith('inquire-asking-price-exp-ccn'):return None,{'output1':self.book,'output2':self.detail}
            raise AssertionError('Unexpected endpoint')
        b.request=request

    def quote(self,side='BUY'):
        with frozen(self.now):return self.broker.execution_quote('005930',side)

    def test_readers_bind_fresh_side_depth_venue_session_and_order_type(self):
        self.assertEqual(self.quote(),10000)
        self.assertEqual(self.quote('SELL'),9990)
        ev=self.broker.quote_evidence[('005930','BUY')]
        self.assertEqual((ev['venue'],ev['session'],ev['order_type'],ev['size']),('KRX','AFTER','41',10))
        self.assertFalse(any(k.get('post') for _,_,_,k in self.calls))
        self.assertTrue(all(p.get('FID_COND_MRKT_DIV_CODE','J')=='J' for _,_,p,_ in self.calls))

    def test_evening_cash_payload_is_limit_41_never_regular_00_or_market_01(self):
        for side,price,tr in [('BUY',10000,'TTTC0012U'),('SELL',9990,'TTTC0011U')]:
            self.quote(side)
            with frozen(self.now),patch.object(engine,'require_live_release'):
                self.broker.submit(engine.OrderPlan('005930',side,1,price,'fixture','TEST'))
            path,actual_tr,body,kw=self.calls[-1]
            self.assertTrue(kw['post']);self.assertEqual(actual_tr,tr)
            self.assertEqual(body['EXCG_ID_DVSN_CD'],'KRX');self.assertEqual(body['ORD_DVSN'],'41')
            self.assertEqual(body['ORD_UNPR'],str(price))

    def test_after_buying_power_uses_41_without_regular_fallback(self):
        import oneil_v519_live_runner as runner
        with frozen(self.now),patch.object(runner,'api',return_value={'output':{
                'nrcvb_buy_amt':'100000','ord_psbl_cash':'90000','nrcvb_buy_qty':'9'}}) as api:
            self.assertEqual(self.broker.power('005930',10000),(90000,9))
        self.assertEqual(api.call_args.kwargs['params']['ORD_DVSN'],'41')
        with frozen(self.now),patch.object(runner,'api',side_effect=ValueError('fixture rejection')) as api:
            with self.assertRaises(ValueError):self.broker.power('005930',10000)
        self.assertEqual(api.call_count,1)

    def test_unknown_instrument_evidence_never_certifies_quote(self):
        for key in ('pdno','scty_grp_id_cd','mket_id_cd','tr_stop_yn'):
            self.setUp();self.info.pop(key)
            with self.assertRaises(engine.TradingBlocked):self.quote()
            self.assertEqual(self.broker.quote_evidence,{})
        self.setUp();self.info['pdno']='000660'
        with self.assertRaises(engine.TradingBlocked):self.quote()

    def test_etp_other_market_halt_and_risk_flags_are_explicit_exclusions(self):
        for key,value in [('scty_grp_id_cd','EF'),('scty_grp_id_cd','EN'),
                ('mket_id_cd','KNX'),('tr_stop_yn','Y')]:
            self.setUp();self.info[key]=value
            with self.assertRaises(engine.SessionInstrumentUnavailable):self.quote()
        for key,value in [('temp_stop_yn','Y'),('short_over_yn','Y'),('sltr_yn','Y'),
                ('iscd_stat_cls_code','58')]:
            self.setUp();self.status[key]=value
            with self.assertRaises(engine.SessionInstrumentUnavailable):self.quote()
        self.setUp();self.status['iscd_stat_cls_code']='57';self.assertEqual(self.quote(),10000)

    def test_auction_vi_unknown_session_and_ticker_mismatch_fail_closed(self):
        for code in ('00','49','40','',None):
            self.setUp();self.book['new_mkop_cls_code']=code
            with self.assertRaisesRegex(engine.TradingBlocked,'AFTER_MARKET_SESSION_UNVERIFIED'):self.quote()
        for vi in ('Y','',None,'0'):
            self.setUp();self.detail['vi_cls_code']=vi
            with self.assertRaises(engine.TradingBlocked):self.quote()
        self.setUp();self.detail['stck_shrn_iscd']='000660'
        with self.assertRaises(engine.TradingBlocked):self.quote()

    def test_old_day_regular_book_future_stale_crossed_wide_or_empty_book_rejected(self):
        for patch_book in [{'aspr_acpt_hour':'151000'},{'aspr_acpt_hour':'165956'},
                {'aspr_acpt_hour':'170001'},{'bidp1':'10001'},{'askp1':'11000'},
                {'askp_rsqn1':'0'}]:
            self.setUp();self.book.update(patch_book)
            with self.assertRaises(engine.TradingBlocked):self.quote()
            self.assertEqual(self.broker.quote_evidence,{})
        self.setUp();self.day='20261104'
        with self.assertRaises(engine.TradingBlocked):self.quote()

    def test_failed_refresh_removes_previous_certificate(self):
        self.quote();self.detail['vi_cls_code']='Y'
        with self.assertRaises(engine.TradingBlocked):self.quote()
        self.assertEqual(self.broker.quote_evidence,{})

    def test_session_close_or_certificate_mismatch_prevents_any_post(self):
        self.now=at(19,59,59);self.book['aspr_acpt_hour']='195959';self.quote()
        plan=engine.OrderPlan('005930','BUY',1,10000,'fixture','TEST')
        with frozen(at(20)),patch.object(engine,'require_live_release'):
            with self.assertRaisesRegex(engine.OrderNotSent,'ORDER_SESSION_CHANGED'):self.broker.submit(plan)
        self.assertFalse(any(k.get('post') for _,_,_,k in self.calls))
        for field,value in [('venue','NXT'),('session','REGULAR'),('order_type','00')]:
            self.setUp();self.quote();self.broker.quote_evidence[('005930','BUY')][field]=value
            with frozen(self.now),patch.object(engine,'require_live_release'):
                with self.assertRaises(engine.OrderNotSent):self.broker.submit(plan)
            self.assertFalse(any(k.get('post') for _,_,_,k in self.calls))

    def test_weekend_gap_and_close_do_not_read_broker(self):
        for now in (at(15,40),at(20),at(17).replace(day=7)):
            self.now=now
            with self.assertRaises(engine.TradingBlocked):self.quote()
        self.assertEqual(self.calls,[])

    def test_unapproved_release_cannot_send_even_with_correct_evening_book(self):
        self.quote()
        with frozen(self.now),patch.dict('os.environ',{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','V519_APPROVED_RELEASE':''}):
            with self.assertRaises(engine.TradingBlocked):
                self.broker.submit(engine.OrderPlan('005930','BUY',1,10000,'fixture','TEST'))
        self.assertFalse(any(k.get('post') for _,_,_,k in self.calls))


if __name__=='__main__':unittest.main()
