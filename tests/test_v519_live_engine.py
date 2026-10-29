import tempfile, pathlib, unittest
from oneil_v519_live_engine import *
class Tests(unittest.TestCase):
 def setUp(self):
  self.a=VerifiedAccount(5000000,5000000,0,0,False,False,True,True,True)
  self.p=CertifiedPrice("144960","KRX",10000,True,True,True,True)
 def test_cash_based_five_million(self):
  r=budget(self.a,self.p)
  self.assertEqual(r["trading_cap_krw"],3000000)
  self.assertEqual(r["per_stock_cap_krw"],900000)
  self.assertEqual(r["qty"],90)
 def test_cash_based_two_million(self):
  a=VerifiedAccount(2000000,2000000,0,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],36)
 def test_broker_cash_cap(self):
  a=VerifiedAccount(5000000,90000,0,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],9)
 def test_invested_exposure(self):
  a=VerifiedAccount(5000000,5000000,2900000,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],10)
 def test_existing_ticker(self):
  a=VerifiedAccount(5000000,5000000,0,0,True,False,True,True,True)
  with self.assertRaises(TradingBlocked): budget(a,self.p)
 def test_no_certified_quote(self):
  p=CertifiedPrice("144960","NXT",10000,True,True,False,True)
  with self.assertRaises(TradingBlocked): budget(self.a,p)
 def test_no_verified_cash(self):
  a=VerifiedAccount(5000000,5000000,0,0,False,False,True,True,False)
  with self.assertRaises(TradingBlocked): budget(a,self.p)
 def test_loss_halt(self):
  self.assertTrue(daily_loss(1000000,975000,0,baseline_authenticated=True,current_authenticated=True,cashflows_reconciled=True)["halt"])
 def test_deposit_not_profit(self):
  r=daily_loss(1000000,1090000,100000,baseline_authenticated=True,current_authenticated=True,cashflows_reconciled=True)
  self.assertEqual(r["pnl_krw"],-10000)
 def test_missing_pnl_provenance(self):
  with self.assertRaises(TradingBlocked):daily_loss(1000000,980000,0)
 def test_ledger_rejects_duplicate(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"orders.sqlite")
   x.reserve("same","144960","BUY","KRX",10)
   with self.assertRaises(TradingBlocked):x.reserve("same","144960","BUY","KRX",10)
   x.close()
 def test_unknown_submission_no_retry(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   x.close()
   y=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   with self.assertRaises(TradingBlocked):y.reserve("one","144960","BUY","KRX",10)
   y.close()
 def test_partial_fill(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   y=x.reconcile("one",broker_id="abc",status="PART_FILLED",filled=4)
   self.assertEqual(y["pending_qty"],6)
   self.assertFalse(y["auto_resubmit"])
   x.close()
 def test_overfill(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   with self.assertRaises(TradingBlocked):x.reconcile("one",broker_id="abc",status="FILLED",filled=11)
   x.close()
 def test_order_adapter_map(self):
  a=KisCashOrderAdapter(None)
  r=a.prepare(side="BUY",ticker="144960",qty=2,limit_price_krw=10000,cano="12345678",product="01")
  self.assertEqual(r["tr_id"],"TTTC0012U")
 def test_order_submit_never_live(self):
  with self.assertRaises(TradingBlocked):KisCashOrderAdapter(None).submit()
 def test_gate_deny(self):
  self.assertEqual(readiness()["gate"],"DENY")
if __name__=="__main__":unittest.main()

# Integration tests live in the existing engine test module. No validator scripts.
import os
import datetime as dt
from contextlib import contextmanager
from unittest.mock import patch

class BrokerFixture:
    def __init__(self):
        self.calls=[]; self.rows={}; self.failure=None
        self.cash=5_000_000; self.invested=0; self.positions={}; self.pending=set()
        self.prices={}; self.amount=5_000_000; self.broker_qty=10000
    def buy_control(self): return {'enabled':True,'connected':True,'revision':1,'risk':{'connected':True,'reserve_pct':40,'per_stock_pct':30,'revision':0,'updated_at':None}}
    def snapshot(self):
        return dict(cash=self.cash,invested=self.invested,positions=self.positions,pending=self.pending)
    def quote(self,ticker,side): return self.prices.get((ticker,side),10000)
    def power(self,ticker,price): return self.amount,self.broker_qty
    def submit(self,plan):
        self.calls.append(plan)
        if self.failure: raise self.failure
        return {'rt_cd':'0','output':{'ODNO':f'{122+len(self.calls):06d}'}}
    def history(self,day): return self.rows.get(day,[])

class RuntimeRiskTests(unittest.TestCase):
    def test_owner_risk_percentages_set_cash_and_stock_limits(self):
        from v519_cash_policy import limits
        for cash in (499,500_000,5_000_000):
            for reserve in (0,20,40,75,100):
                for stock in (0,10,30,100):
                    b=limits(cash,0,cash,reserve_pct=reserve,per_stock_pct=stock)
                    self.assertEqual(b['trading'],cash*(100-reserve)//100)
                    self.assertEqual(b['per_stock'],b['trading']*stock//100)
                    self.assertEqual(b['reserve']+b['trading'],cash)
                    self.assertEqual((b['reserve_pct'],b['per_stock_pct']),(reserve,stock))
        for bad in (-1,101,True,'40',40.5,None):
            with self.assertRaises(ValueError):limits(500,0,500,reserve_pct=bad)
            with self.assertRaises(ValueError):limits(500,0,500,per_stock_pct=bad)

    def test_risk_response_missing_or_invalid_never_enables_buy(self):
        import io
        now=dt.datetime(2026,10,29,0,0,tzinfo=dt.timezone.utc)
        body={'schema_version':1,'enabled':True,'revision':1,'observed_at':now.isoformat()}
        def read(risk):
            return read_buy_control(access_token='test',now=now,
                opener=lambda *a,**k:io.StringIO(json.dumps({**body,'risk':risk})))
        valid={'reserve_pct':60,'per_stock_pct':20,'revision':3}
        result=read(valid);self.assertTrue(result['enabled'])
        self.assertEqual(result['risk']['reserve_pct'],60)
        for invalid in (None,{},[],{**valid,'reserve_pct':True},{**valid,'per_stock_pct':101},{**valid,'revision':-1}):
            self.assertFalse(read(invalid)['enabled'])

    def test_failure_diagnostics_hide_http_secrets(self):
        from urllib.error import HTTPError
        exc=HTTPError('https://example.test/?token=SECRET',403,'SECRET',{'key':'SECRET'},None)
        data=failure_details(exc)
        self.assertEqual(data['failure_kind'],'HTTP_ERROR')
        self.assertEqual(data['http_status'],403)
        self.assertNotIn('SECRET',json.dumps(data))

    def test_failure_diagnostics_sqlstate_is_validated(self):
        class DatabaseFailure(Exception):
            sqlstate='42501'
        data=failure_details(DatabaseFailure('postgresql://SECRET'))
        self.assertEqual(data['sqlstate'],'42501')
        self.assertEqual(data['failure_kind'],'DATABASE_ERROR')
        self.assertNotIn('SECRET',json.dumps(data))
        DatabaseFailure.sqlstate='SECRET'
        self.assertNotIn('sqlstate',failure_details(DatabaseFailure()))

    def test_failure_diagnostics_trace_has_only_code_location(self):
        namespace={}
        exec(compile("def broken():\n raise KeyError('SECRET')",'/private/oneil_v519_live_engine.py','exec'),namespace)
        try:
            namespace['broken']()
        except KeyError as exc:
            data=failure_details(exc)
        self.assertEqual(data['failure_kind'],'MISSING_FIELD')
        self.assertEqual(data['failure_frames'],[{'file':'oneil_v519_live_engine.py','function':'broken','line':2}])
        self.assertNotIn('SECRET',json.dumps(data))
        self.assertNotIn('/private',json.dumps(data))

    def test_scanner_live_flags_preserve_filtering_and_standalone_lock(self):
        from oneil_live_v5_9 import analyze
        now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'candidates.csv'
            path.write_text('date,ticker,rs,foreign_5d,institution_5d,volume_ratio,channel_up,channel_position\n'
                '2026-10-29,144960,95,1,0,2,true,0.3\n'
                '2026-10-29,005930,89,1,0,2,true,0.3\n')
            with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
                expected=scan_candidates(path,now)
            with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true'}):
                self.assertEqual(scan_candidates(path,now),expected)
                self.assertEqual([x['ticker'] for x in expected],['144960'])
                with self.assertRaisesRegex(RuntimeError,'LIVE_FLAGS_NOT_DISABLED'):
                    analyze(path,now)
                path.write_text('date,ticker,rs\n2026-10-29,144960,95\n')
                with self.assertRaisesRegex(TradingBlocked,'CANDIDATE_SOURCE_INVALID_OR_STALE'):
                    scan_candidates(path,now)

    def test_multiple_cash_sizes_no_fixed_cap(self):
        for cash in (500_000,2_000_000,5_000_000,50_000_000):
            b=BrokerFixture(); b.cash=cash; b.amount=cash
            snap=b.snapshot(); snap['managed']={}
            plans,lim=plan_orders([{'ticker':str(100000+i)} for i in range(10)],snap,b.quote,b.power)
            charges=[(p.quantity*p.price*1005+999)//1000 for p in plans]
            self.assertLessEqual(sum(charges),cash*60//100)
            self.assertTrue(all(n<=(cash*60//100)*30//100 for n in charges))
            self.assertEqual(lim['reserve'],cash-cash*60//100)
    def test_holdings_and_broker_limits(self):
        b=BrokerFixture(); b.invested=2_950_000; b.amount=40_000; b.broker_qty=2
        snap=b.snapshot(); snap['managed']={}
        plans,_=plan_orders([{'ticker':'144960'}],snap,b.quote,b.power)
        self.assertEqual(plans[0].quantity,2)
        b.invested=3_000_000; snap=b.snapshot(); snap['managed']={}
        self.assertEqual(plan_orders([{'ticker':'144960'}],snap,b.quote,b.power)[0],[])
    def test_pending_blocks_both_sides(self):
        b=BrokerFixture(); snap=b.snapshot(); snap['managed']={}; snap['pending']={'005930'}
        with self.assertRaises(TradingBlocked):plan_orders([{'ticker':'144960'}],snap,b.quote,b.power)
    def test_exits_only_managed_sellable_positions(self):
        b=BrokerFixture(); b.positions={'144960':{'quantity':10,'sellable':7,'average':'11000.25'},
            '005930':{'quantity':100,'sellable':100,'average':'10000'}}
        snap=b.snapshot(); snap['managed']={'144960':8}
        plans,_=plan_orders([],snap,b.quote,b.power)
        self.assertEqual([(p.ticker,p.side,p.quantity,p.reason) for p in plans],
                         [('144960','SELL',7,'STOP_LOSS')])
        b.prices[('144960','SELL')]=13000
        self.assertEqual(plan_orders([],snap,b.quote,b.power)[0][0].reason,'TAKE_PROFIT')
    def test_repeat_buy_today_and_held_block(self):
        b=BrokerFixture(); snap=b.snapshot(); snap['managed']={}; snap['bought_today']={'144960'}
        self.assertEqual(plan_orders([{'ticker':'144960'}],snap,b.quote,b.power)[0],[])
    def test_all_live_flags_cannot_unlock_release_or_legacy_submit(self):
        from oneil_v519_live_runner import live_submit,Blocked
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        with patch.dict(os.environ,flags),patch('urllib.request.urlopen') as wire:
            with self.assertRaises(TradingBlocked):require_live_release()
            with self.assertRaises(Blocked):live_submit('t','k','s','12345678','01','144960',1,10000,'BUY')
            b=object.__new__(KisBroker)
            with self.assertRaises(TradingBlocked):b._send_verified(OrderPlan('144960','BUY',1,10000,'rs','RS'))
            wire.assert_not_called()
    def test_live_broker_construction_is_locked_before_authentication(self):
        with patch('oneil_v519_live_runner.token') as auth:
            with self.assertRaisesRegex(TradingBlocked,'RELEASE_LOCKED'):
                KisBroker(execution=True)
            auth.assert_not_called()

    def test_shared_credentials_dont_block_an_approved_execution_profile(self):
        # Simulate a FUTURE reviewed release; all wire calls remain mocked.
        # This prevents the legacy read-only config guard from making the
        # integrated execution profile impossible to activate after approval.
        flags={'KIS_APP_KEY':'test','KIS_APP_SECRET':'test','KIS_ACCOUNT_NO':'12345678',
               'KIS_ACCOUNT_PRODUCT':'01','LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true',
               'I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        import oneil_v519_live_engine as engine
        with patch.dict(os.environ,flags),patch.object(engine,'RELEASE_LIVE_APPROVED',True), \
                patch('oneil_v519_live_runner.token',return_value='test-token'), \
                patch('urllib.request.urlopen') as wire:
            broker=KisBroker(execution=True)
            self.assertEqual(broker.account,'12345678')
            wire.assert_not_called()

    def test_monitor_hours_use_korea_time_and_exclude_close(self):
        zone=ZoneInfo('Asia/Seoul')
        self.assertFalse(monitoring_open(dt.datetime(2026,10,29,8,59,tzinfo=zone)))
        self.assertTrue(monitoring_open(dt.datetime(2026,10,29,9,0,tzinfo=zone)))
        self.assertTrue(monitoring_open(dt.datetime(2026,10,29,19,59,tzinfo=zone)))
        self.assertFalse(monitoring_open(dt.datetime(2026,10,29,20,0,tzinfo=zone)))
        self.assertTrue(monitoring_open(dt.datetime(2026,10,29,0,0,tzinfo=dt.timezone.utc)))

    def test_dashboard_encryption_roundtrip_and_tamper_protection(self):
        import base64,json
        from cryptography.hazmat.primitives.asymmetric import rsa,padding
        from cryptography.hazmat.primitives import serialization,hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        with tempfile.TemporaryDirectory() as directory:
            pub=pathlib.Path(directory)/'public.pem'; out=pathlib.Path(directory)/'encrypted.json'
            pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
            original={'cash':5321987,'name':'PRIVATE_ACCOUNT_TEST'}
            encrypt_dashboard(original,pub,out)
            raw=out.read_text(); self.assertNotIn('PRIVATE_ACCOUNT_TEST',raw);self.assertNotIn('5321987',raw)
            envelope=json.loads(raw); dec=base64.b64decode
            aes=key.decrypt(dec(envelope['wrapped_key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
            aad=('oneil-dashboard-v1:'+envelope['key_id']).encode()
            plain=AESGCM(aes).decrypt(dec(envelope['nonce']),dec(envelope['ciphertext']),aad)
            self.assertEqual(json.loads(plain),original)
            altered=bytearray(dec(envelope['ciphertext']));altered[0]^=1
            with self.assertRaises(InvalidTag):AESGCM(aes).decrypt(dec(envelope['nonce']),bytes(altered),aad)

    def test_buy_control_missing_or_failed_blocks_buys(self):
        self.assertFalse(read_buy_control(access_token='')['enabled'])
        def failed(*args,**kwargs):raise TimeoutError()
        result=read_buy_control(access_token='test',opener=failed)
        self.assertFalse(result['enabled']);self.assertFalse(result['connected'])

    def test_buy_control_requires_valid_fresh_response(self):
        import io
        now=dt.datetime(2026,10,29,0,0,tzinfo=dt.timezone.utc)
        valid={'schema_version':1,'enabled':True,'revision':2,'observed_at':now.isoformat(),'risk':{'reserve_pct':40,'per_stock_pct':30,'revision':0}}
        read=lambda body:read_buy_control(access_token='test',now=now,opener=lambda *a,**k:io.StringIO(json.dumps(body)))
        self.assertTrue(read(valid)['enabled'])
        self.assertFalse(read({**valid,'enabled':'true'})['enabled'])
        self.assertFalse(read({**valid,'observed_at':(now-dt.timedelta(minutes=1)).isoformat()})['enabled'])

    def test_control_redirect_never_forwards_service_credential(self):
        import urllib.request
        from unittest.mock import Mock
        director=Mock()
        director.open.side_effect=TradingBlocked('CONTROL_REDIRECT_REJECTED')
        with patch('urllib.request.build_opener',return_value=director) as factory:
            result=read_buy_control(access_token='fake-service-credential')
        self.assertFalse(result['enabled']);self.assertFalse(result['connected'])
        handler=factory.call_args.args[0]
        original=urllib.request.Request('https://oneil-control-desk.asura2367.chatgpt.site/api/runtime-control',
            headers={'OAI-Sites-Authorization':'Bearer fake-service-credential'})
        for target in ('https://other.example/login','https://oneil-control-desk.asura2367.chatgpt.site/signin-with-chatgpt'):
            with self.assertRaisesRegex(TradingBlocked,'REDIRECT_REJECTED'):
                handler.redirect_request(original,None,302,'Found',{},target)
        self.assertEqual(director.open.call_count,1)

    def test_real_order_http_mapping_is_intercepted_for_both_sides(self):
        # Exercise the actual adapter->Request encoding. Every network call is
        # intercepted; these fixtures do not establish KIS execution success.
        import io
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        broker=object.__new__(KisBroker)
        broker.execution=True;broker.account='12345678';broker.product='01'
        broker.access='fixture';broker.key='fixture';broker.secret='fixture'
        broker.quote_evidence={}
        for side,tr in (('BUY','TTTC0012U'),('SELL','TTTC0011U')):
            broker.quote_evidence[('144960',side)]={'price':10000,'size':10,'at':now}
            response=io.BytesIO(b'{"rt_cd":"0","output":{"ODNO":"000123"}}')
            response.headers={}
            with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags), \
                    patch('urllib.request.urlopen',return_value=response) as wire:
                result=broker.submit(OrderPlan('144960',side,2,10000,'fixture','TEST'))
            request=wire.call_args.args[0]; body=json.loads(request.data)
            self.assertEqual(wire.call_count,1)
            self.assertEqual(request.get_method(),'POST')
            self.assertTrue(request.full_url.endswith('/uapi/domestic-stock/v1/trading/order-cash'))
            self.assertEqual(request.get_header('Tr_id'),tr)
            self.assertEqual((body['PDNO'],body['ORD_QTY'],body['ORD_UNPR'],body['EXCG_ID_DVSN_CD']),
                             ('144960','2','10000','KRX'))
            self.assertEqual(result['output']['ODNO'],'000123')

    def test_order_transport_failure_is_not_retried(self):
        broker=object.__new__(KisBroker)
        broker.access=broker.key=broker.secret='fixture'
        for error in (TimeoutError(),ConnectionResetError()):
            with patch('urllib.request.urlopen',side_effect=error) as wire:
                with self.assertRaises(type(error)):
                    broker.request('/uapi/domestic-stock/v1/trading/order-cash','TTTC0012U',{},post=True)
                self.assertEqual(wire.call_count,1)

    def test_broker_audit_empty_history_does_not_claim_fill_verification(self):
        b=BrokerFixture();b.execution_quote=lambda *args:10000
        with patch.object(b,'submit') as wire:
            result=audit_broker_evidence(b,[{'ticker':'144960'}],now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul')))
            wire.assert_not_called()
        self.assertEqual(result['history_days_verified'],5)
        self.assertEqual(result['order_fill_evidence'],'NO_REAL_ROWS')
        self.assertFalse(result['actual_order_submission_verified'])
        self.assertFalse(result['restart_recovery_verified'])
        self.assertEqual(result['orders_sent'],0)

    def test_broker_audit_validates_real_row_shapes_without_private_output(self):
        b=BrokerFixture();b.execution_quote=lambda *args:10000
        day='20261029'
        base={'ord_dt':day,'pdno':'144960','sll_buy_dvsn_cd':'02','ord_qty':'10','cncl_yn':'N'}
        b.rows[day]=[{**base,'odno':'000123','tot_ccld_qty':'10','rmn_qty':'0'},
            {**base,'odno':'000124','tot_ccld_qty':'4','rmn_qty':'6'},
            {**base,'odno':'000125','tot_ccld_qty':'3','rmn_qty':'0','cncl_yn':'Y'}]
        r=audit_broker_evidence(b,[{'ticker':'144960'}],now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul')))
        self.assertEqual(r['history_rows_verified'],3)
        self.assertEqual([r['fill_states_observed'][k] for k in ('FILLED','PART_FILLED','CANCELLED')],[1,1,1])
        self.assertNotIn('000123',json.dumps(r));self.assertNotIn('144960',json.dumps(r))
        self.assertFalse(r['restart_recovery_verified'])

    def test_broker_audit_rejects_conflicting_history(self):
        now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        base={'ord_dt':'20261029','pdno':'144960','sll_buy_dvsn_cd':'02','ord_qty':'10',
              'odno':'000123','tot_ccld_qty':'4','rmn_qty':'6','cncl_yn':'N'}
        for changes in ({'ord_dt':'20261028'},{'ord_qty':'0'},{'tot_ccld_qty':'11'},
                        {'sll_buy_dvsn_cd':'00'},{'rmn_qty':None}):
            with self.subTest(changes=changes):
                b=BrokerFixture();b.execution_quote=lambda *args:10000
                b.rows['20261029']=[{**base,**changes}]
                r=audit_broker_evidence(b,[{'ticker':'144960'}],now=now,lookback_days=1)
                self.assertEqual(r['status'],'BLOCKED_BROKER_EVIDENCE')
                self.assertEqual(r['history_rows_verified'],0)
                self.assertIn('HISTORY_RESPONSE_INVALID',r['errors'])

    def test_broker_audit_is_bounded_before_network(self):
        b=BrokerFixture()
        for days in (0,11,True):
            with patch.object(b,'snapshot') as read:
                with self.assertRaises(TradingBlocked):
                    audit_broker_evidence(b,[],now=dt.datetime.now(ZoneInfo('Asia/Seoul')),lookback_days=days)
                read.assert_not_called()

    def quote_reader(self, book=None, day='20261029'):
        broker=object.__new__(KisBroker);broker.quote_evidence={}
        book=book or {'aspr_acpt_hour':'100000','askp1':'10000','bidp1':'9990',
                     'askp_rsqn1':'10','bidp_rsqn1':'20'}
        broker.request=lambda path,*args,**kwargs:(None,{'output':[{'stck_bsop_date':day}]}
            if 'inquire-daily-price' in path else {'output1':book})
        return broker

    def test_execution_book_uses_current_day_price_side_and_depth(self):
        now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return now if tz else now.replace(tzinfo=None)
        b=self.quote_reader()
        with patch('oneil_v519_live_engine.dt.datetime',Clock):
            self.assertEqual(b.execution_quote('144960','BUY'),10000)
            self.assertEqual(b.execution_quote('144960','SELL'),9990)
        self.assertEqual(b.quote_evidence[('144960','BUY')]['size'],10)
        self.assertEqual(b.quote_evidence[('144960','SELL')]['size'],20)

    def test_execution_book_blocks_stale_future_wide_and_empty_quotes(self):
        now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return now if tz else now.replace(tzinfo=None)
        base={'aspr_acpt_hour':'100000','askp1':'10000','bidp1':'9990',
              'askp_rsqn1':'10','bidp_rsqn1':'20'}
        for changes in ({'aspr_acpt_hour':'095956'},{'aspr_acpt_hour':'100001'},
                        {'aspr_acpt_hour':''},{'askp1':'11000'},{'askp_rsqn1':'0'}):
            with self.subTest(changes=changes),patch('oneil_v519_live_engine.dt.datetime',Clock):
                b=self.quote_reader({**base,**changes})
                with self.assertRaises(TradingBlocked):b.execution_quote('144960','BUY')
                self.assertEqual(b.quote_evidence,{})
        with patch('oneil_v519_live_engine.dt.datetime',Clock):
            with self.assertRaisesRegex(TradingBlocked,'EXCHANGE_DAY_NOT_CONFIRMED'):
                self.quote_reader(day='20261028').execution_quote('144960','BUY')

    def test_exact_code_approval_and_environment_flags_are_both_required(self):
        expected=release_fingerprint()
        with patch.dict(os.environ,{'V519_APPROVED_RELEASE':expected,'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
            self.assertTrue(release_is_approved())
            with self.assertRaisesRegex(TradingBlocked,'USER_APPROVAL_MISSING'):require_live_release()
        with patch.dict(os.environ,{'V519_APPROVED_RELEASE':'0'*64}):self.assertFalse(release_is_approved())

    def test_release_digest_changes_with_trading_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name in RELEASE_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            before=release_fingerprint(root)
            (root/RELEASE_FILES[0]).write_text('changed')
            self.assertNotEqual(before,release_fingerprint(root))

    def test_rs_priority_and_bounded_execution_plan(self):
        b=BrokerFixture();snap=b.snapshot();snap['managed']={}
        rows=[{'ticker':'005930','rs':92},{'ticker':'144960','rs':99},{'ticker':'000660','rs':95}]
        with patch.object(b,'quote',wraps=b.quote) as quote:
            plans,_=plan_orders(rows,snap,quote,b.power,max_buys=1)
        self.assertEqual([p.ticker for p in plans],['144960'])
        self.assertEqual(quote.call_count,1)

    def test_exit_plan_precedes_all_candidate_quote_work(self):
        b=BrokerFixture();snap=b.snapshot();snap['managed']={'144960':10}
        snap['positions']={'144960':{'quantity':10,'sellable':10,'average':'12000'}}
        with patch.object(b,'quote',wraps=b.quote) as quote:
            plans,_=plan_orders([{'ticker':'005930','rs':99}],snap,quote,b.power,max_buys=1)
        self.assertEqual([p.side for p in plans],['SELL'])
        quote.assert_called_once_with('144960','SELL')

    def test_missing_and_stale_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TradingBlocked):scan_candidates(pathlib.Path(directory)/'none',dt.datetime.now(ZoneInfo('Asia/Seoul')))

@unittest.skipUnless(os.getenv('V519_TEST_DB_URL'),'PostgreSQL integration URL not supplied')
class PostgresRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg,uuid
        from urllib.parse import urlparse
        from v519_pg_ledger import migrate_runtime
        cls.url=os.environ['V519_TEST_DB_URL']
        if urlparse(cls.url).hostname not in ('localhost','127.0.0.1'):
            raise RuntimeError('TEST_DATABASE_MUST_BE_LOCAL')
        cls.schema='v519_test_'+uuid.uuid4().hex
        cls.conn=psycopg.connect(cls.url,autocommit=True)
        cls.conn.execute('CREATE SCHEMA '+cls.schema)
        cls.conn.execute('SET search_path TO '+cls.schema)
        migrate_runtime(cls.conn)
    @classmethod
    def tearDownClass(cls):
        cls.conn.execute('DROP SCHEMA '+cls.schema+' CASCADE'); cls.conn.close()
    def setUp(self):
        from v519_pg_ledger import PgRuntimeLedger
        self.conn.execute('TRUNCATE v519_order_intents,v519_runs,v519_live_activation')
        self.conn.execute('UPDATE v519_control SET halted=false')
        self.ledger=PgRuntimeLedger(self.conn,'b'*64)
        self.ledger.activate_release(release_fingerprint(),'fixture')
        self.broker=BrokerFixture()
        self.now=dt.datetime(2026,10,29,10,0,tzinfo=ZoneInfo('Asia/Seoul'))
        self.candidates=[{'ticker':'144960','rs':98}]
    def cycle(self,execute=True):
        return integrated_cycle(self.ledger,self.broker,self.candidates,execute=execute,now=self.now,clock=lambda:self.now)
    def seed(self,side='BUY',day=None):
        plan=OrderPlan('144960',side,10,10000,'rs','TEST')
        return self.ledger.reserve_plan(plan,day or self.now.date())
    def history_row(self,day,fill=10,remaining=0,side='02',**extra):
        return dict(odno='000123',ord_dt=day,pdno='144960',sll_buy_dvsn_cd=side,
                    ord_qty='10',tot_ccld_qty=str(fill),rmn_qty=str(remaining),cncl_yn='N',**extra)
    def with_risk(self,reserve,stock,revision=1):
        control=self.broker.buy_control()
        risk={'connected':True,'reserve_pct':reserve,'per_stock_pct':stock,'revision':revision}
        self.broker.buy_control=lambda:{**control,'risk':risk}
        return control,risk

    def test_saved_owner_risk_controls_actual_quantity_and_snapshot(self):
        self.with_risk(60,10)
        result=self.cycle()
        self.assertEqual(self.broker.calls[0].quantity,19)
        data=dashboard_snapshot(self.ledger,result)
        self.assertEqual(data['allocation']['reserve'],3_000_000)
        self.assertEqual(data['allocation']['trading'],2_000_000)
        self.assertEqual(data['allocation']['per_stock'],200_000)
        self.assertEqual(data['risk_control']['revision'],1)
        self.assertEqual(data['risk_control']['reserve_pct'],60)

    def test_risk_change_just_before_buy_creates_no_intent(self):
        control=self.broker.buy_control();changed={**control,'risk':{**control['risk'],'revision':1,'reserve_pct':80}}
        states=iter([control,changed]);self.broker.buy_control=lambda:next(states)
        result=self.cycle()
        self.assertEqual(result['status'],'RISK_SETTINGS_CHANGED')
        self.assertEqual(self.broker.calls,[]);self.assertEqual(self.ledger.pending(),[])

    def test_risk_change_between_batch_buys_stops_remaining_candidates(self):
        self.two_candidates();self.fill_each_submission()
        control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'risk':{**control['risk'],'revision':int(bool(self.broker.calls))}}
        result=self.cycle()
        self.assertEqual(result['batch_stop_reason'],'RISK_SETTINGS_CHANGED')
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual(len(self.broker.calls),1)

    def test_zero_buy_allocation_sends_no_order(self):
        for reserve,stock in ((100,30),(40,0)):
            self.with_risk(reserve,stock)
            self.assertEqual(self.cycle()['orders_sent'],0)
        self.assertEqual(self.broker.calls,[])

    def test_risk_missing_blocks_buys_but_does_not_disable_exit(self):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':10,'average':'12000'}}
        self.broker.buy_control=lambda:{'enabled':True,'connected':True,'revision':1}
        result=self.cycle()
        self.assertEqual(result['planned_buys'],0)
        self.assertEqual([p.side for p in self.broker.calls],['SELL'])

    def test_reducing_risk_budget_never_forces_sale(self):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':10,'average':'10000'}}
        self.with_risk(100,0)
        self.assertEqual(self.cycle()['orders_sent'],0)
        self.assertEqual(self.broker.calls,[])

    def fill_each_submission(self, *, fill=None, balance=True, debit=True):
        fixture=self.broker
        original=fixture.submit
        def submit(plan):
            response=original(plan)
            quantity=plan.quantity if fill is None else min(fill,plan.quantity)
            row=dict(odno=response['output']['ODNO'],ord_dt=self.now.strftime('%Y%m%d'),
                pdno=plan.ticker,sll_buy_dvsn_cd='02',ord_qty=str(plan.quantity),
                tot_ccld_qty=str(quantity),rmn_qty=str(plan.quantity-quantity),cncl_yn='N')
            fixture.rows.setdefault(row['ord_dt'],[]).append(row)
            if balance:
                cost=quantity*plan.price
                fixture.positions[plan.ticker]={'quantity':quantity,'sellable':quantity,
                    'average':str(plan.price),'cost':cost,'valuation':cost,'name':plan.ticker}
                if debit:
                    fixture.cash-=cost;fixture.invested+=cost
            if quantity<plan.quantity:fixture.pending.add(plan.ticker)
            return response
        fixture.submit=submit

    def two_candidates(self):
        self.candidates=[{'ticker':'100001','rs':99},{'ticker':'100002','rs':98}]

    def test_batch_two_buys_after_exact_fills_and_fresh_balance(self):
        self.two_candidates();self.fill_each_submission()
        reads=[];snapshot=self.broker.snapshot
        self.broker.snapshot=lambda:(reads.append(len(self.broker.calls)) or snapshot())
        result=self.cycle()
        self.assertEqual(result['orders_sent'],2)
        self.assertEqual([p.ticker for p in self.broker.calls],['100001','100002'])
        self.assertEqual(reads,[0,1,2])
        self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(result['recovery']['unresolved'],0)
        self.assertLess(self.broker.calls[1].quantity,self.broker.calls[0].quantity)
        data=dashboard_snapshot(self.ledger,result)
        self.assertEqual({p['ticker'] for p in data['positions']},{'100001','100002'})
        self.assertTrue(all(o['status']=='FILLED' for o in data['orders']))
        self.assertEqual(data['account']['cash'],self.broker.cash)

    def test_batch_kis_history_cache_is_invalidated_after_each_post(self):
        self.two_candidates();self.fill_each_submission()
        fixture,live=self.intercepted_live_broker(10000,10000)
        day=self.now.strftime('%Y%m%d');live.history_cache[day]=[]
        reads=[]
        def history(day):
            if day not in live.history_cache:
                reads.append(len(fixture.calls))
                live.history_cache[day]=list(fixture.rows.get(day,[]))
            return live.history_cache[day]
        live.history=history
        result=self.cycle()
        self.assertEqual(result['orders_sent'],2)
        self.assertEqual(reads,[1,2])
        self.assertEqual(self.ledger.pending(),[])

    def test_batch_never_reuses_initial_budget_even_if_cash_has_not_debited(self):
        self.candidates=[{'ticker':str(100000+i),'rs':99} for i in range(8)]
        self.fill_each_submission(debit=False)
        result=self.cycle()
        charges=[(p.quantity*p.price*1005+999)//1000 for p in self.broker.calls]
        self.assertGreater(len(charges),1)
        self.assertLessEqual(sum(charges),3_000_000)
        self.assertTrue(all(c<=900_000 for c in charges))
        self.assertEqual(result['orders_sent'],len(charges))
        self.assertEqual(len({p.ticker for p in self.broker.calls}),len(charges))

    def test_batch_ack_without_fill_stops_followup_and_restart_never_resends(self):
        self.two_candidates()
        result=self.cycle()
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual(result['batch_stop_reason'],'PENDING_FILL_CONFIRMATION')
        self.assertEqual(result['recovery']['unresolved'],1)
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_partial_fill_stops_without_spending_remaining_budget(self):
        self.two_candidates();self.fill_each_submission(fill=3)
        result=self.cycle()
        self.assertEqual(result['batch_stop_reason'],'PENDING_FILL_CONFIRMATION')
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.ledger.pending()[0]['filled_quantity'],3)

    def test_batch_fill_without_balance_update_stops(self):
        self.two_candidates();self.fill_each_submission(balance=False)
        result=self.cycle()
        self.assertEqual(result['batch_stop_reason'],'BALANCE_UPDATE_PENDING')
        self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_next_buy_obeys_new_broker_power(self):
        self.two_candidates();self.fill_each_submission()
        self.broker.power=lambda *args:(5_000_000,10000) if not self.broker.calls else (0,0)
        result=self.cycle()
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_buy_off_after_first_fill_stops_next_candidate(self):
        self.two_candidates();self.fill_each_submission()
        control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'enabled':not bool(self.broker.calls)}
        result=self.cycle()
        self.assertEqual(result['batch_stop_reason'],'BUY_SWITCH_OFF')
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_second_timeout_preserves_first_ack_and_unknown_second(self):
        self.two_candidates();self.fill_each_submission()
        original=self.broker.submit
        def submit(plan):
            if self.broker.calls:
                self.broker.calls.append(plan)
                raise TimeoutError('not logged')
            return original(plan)
        self.broker.submit=submit
        with self.assertRaisesRegex(TradingBlocked,'UNCERTAIN') as caught:self.cycle()
        progress=caught.exception.execution_progress
        self.assertEqual(progress,{'orders_sent':None,'confirmed_orders_sent':1,'order_attempts':2})
        self.assertTrue(self.ledger.halted())
        self.assertEqual(len(self.broker.calls),2)
        self.assertEqual(self.ledger.pending()[0]['status'],'SUBMISSION_UNKNOWN')
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),2)

    def test_batch_post_order_read_failure_reports_known_send_not_zero(self):
        self.two_candidates();self.fill_each_submission()
        original=self.broker.snapshot
        def snapshot():
            if self.broker.calls:raise TimeoutError('not logged')
            return original()
        self.broker.snapshot=snapshot
        with self.assertRaises(TimeoutError) as caught:self.cycle()
        self.assertEqual(caught.exception.execution_progress,
            {'orders_sent':1,'confirmed_orders_sent':1,'order_attempts':1})
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_halt_between_buys_stops_second(self):
        self.two_candidates();self.fill_each_submission()
        original=self.broker.snapshot
        def snapshot():
            if self.broker.calls:self.ledger.halt()
            return original()
        self.broker.snapshot=snapshot
        with self.assertRaisesRegex(TradingBlocked,'EMERGENCY_HALT'):self.cycle()
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_session_closes_after_first_buy(self):
        self.two_candidates();self.fill_each_submission()
        clock=lambda:self.now if not self.broker.calls else self.now.replace(hour=15,minute=15)
        with self.assertRaisesRegex(TradingBlocked,'OUTSIDE_KRX'):
            integrated_cycle(self.ledger,self.broker,self.candidates,execute=True,now=self.now,clock=clock)
        self.assertEqual(len(self.broker.calls),1)

    def test_batch_does_not_reuse_preexisting_unresolved_order(self):
        self.two_candidates();ident=self.seed();self.ledger.ack(ident,'000122')
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(self.broker.calls,[])

    def test_observe_halted_no_reservation_no_send(self):
        self.ledger.halt(); r=self.cycle(False)
        self.assertEqual(r['planned_buys'],1); self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.pending(),[])
    def test_history_outage_blocks_before_reservation(self):
        self.broker.history=lambda day:None
        with self.assertRaisesRegex(TradingBlocked,'HISTORY_INCOMPLETE'):
            self.cycle()
        self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(self.broker.calls,[])

    def test_commit_before_wire_and_ack_restart(self):
        import psycopg
        original=self.broker.submit
        def submit(plan):
            with psycopg.connect(self.url,autocommit=True) as other:
                other.execute('SET search_path TO '+self.schema)
                self.assertEqual(other.execute('SELECT status FROM v519_order_intents').fetchone()[0],'SUBMISSION_UNKNOWN')
            return original(plan)
        self.broker.submit=submit
        self.assertEqual(self.cycle()['orders_sent'],1)
        self.assertEqual(self.ledger.pending()[0]['broker_order_id'],'000123')
        # Restart with an empty broker history never resubmits.
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)
    def test_timeout_unknown_halts_and_never_resubmits(self):
        self.broker.failure=TimeoutError()
        with self.assertRaisesRegex(TradingBlocked,'UNCERTAIN'):self.cycle()
        self.assertTrue(self.ledger.halted())
        self.assertEqual(self.ledger.pending()[0]['status'],'SUBMISSION_UNKNOWN')
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)
    def test_ack_db_failure_keeps_unknown_and_halts(self):
        with patch.object(self.ledger,'ack',side_effect=RuntimeError()):
            with self.assertRaises(TradingBlocked):self.cycle()
        self.assertTrue(self.ledger.halted()); self.assertIsNone(self.ledger.pending()[0]['broker_order_id'])
    def test_malformed_ack_retains_unknown_halts_and_never_resends(self):
        self.broker.submit=lambda plan:{'rt_cd':'0','output':{'ODNO':''}}
        with self.assertRaisesRegex(TradingBlocked,'UNCERTAIN'):
            self.cycle()
        self.assertTrue(self.ledger.halted())
        self.assertEqual(self.ledger.pending()[0]['status'],'SUBMISSION_UNKNOWN')
        with patch.object(self.broker,'submit') as retry:
            self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
            retry.assert_not_called()

    def test_reservation_db_failure_sends_nothing(self):
        with patch.object(self.ledger,'reserve_plan',side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError):self.cycle()
        self.assertEqual(self.broker.calls,[])
    def test_duplicate_day_intent_after_cancel_stays_blocked(self):
        from v519_pg_ledger import LedgerBlocked
        ident=self.seed(); self.ledger.ack(ident,'000123')
        self.ledger.reconcile(self.ledger.pending()[0],'CANCELLED',0)
        with self.assertRaises(LedgerBlocked):self.seed()
        self.assertEqual(self.cycle()['planned_buys'],0)
    def test_partial_fill_then_full_recovery_across_day(self):
        day=self.now.date()-dt.timedelta(days=1); ident=self.seed(day=day)
        self.ledger.ack(ident,'000123'); raw=day.strftime('%Y%m%d')
        self.broker.rows[raw]=[self.history_row(raw,fill=4,remaining=6)]
        r=recover_pending(self.ledger,self.broker)
        self.assertEqual(r['unresolved'],1); self.assertEqual(self.ledger.managed(),{'144960':4})
        self.broker.rows[raw]=[self.history_row(raw)]
        self.assertEqual(recover_pending(self.ledger,self.broker)['unresolved'],0)
        self.assertEqual(self.ledger.managed(),{'144960':10})
    def test_cancelled_partial_retains_filled_position(self):
        ident=self.seed(); self.ledger.ack(ident,'000123'); day=self.now.strftime('%Y%m%d')
        row=self.history_row(day,fill=3,remaining=0); row['cncl_yn']='Y'
        self.broker.rows[day]=[row]
        self.assertEqual(recover_pending(self.ledger,self.broker)['unresolved'],0)
        self.assertEqual(self.ledger.managed(),{'144960':3})
    def test_wrong_side_or_date_does_not_unlock(self):
        ident=self.seed(); self.ledger.ack(ident,'000123'); day=self.now.strftime('%Y%m%d')
        self.broker.rows[day]=[self.history_row(day,side='01')]
        with self.assertRaises(TradingBlocked):recover_pending(self.ledger,self.broker)
        self.broker.rows[day]=[self.history_row('20261028')]
        self.assertEqual(recover_pending(self.ledger,self.broker)['unresolved'],1)
    def test_fill_regression_rejected(self):
        from v519_pg_ledger import LedgerBlocked
        ident=self.seed(); self.ledger.ack(ident,'000123'); row=self.ledger.pending()[0]
        self.ledger.reconcile(row,'PART_FILLED',5)
        with self.assertRaises(LedgerBlocked):self.ledger.reconcile(row,'PART_FILLED',4)
    def test_unknown_id_never_fuzzy_recovers(self):
        self.seed(); day=self.now.strftime('%Y%m%d')
        self.broker.rows[day]=[self.history_row(day)]
        self.assertEqual(recover_pending(self.ledger,self.broker)['unresolved'],1)
    def test_emergency_halt_before_send_blocks(self):
        original=self.ledger.reserve_plan
        def reserve(*args):
            ident=original(*args); self.ledger.halt(); return ident
        with patch.object(self.ledger,'reserve_plan',side_effect=reserve):
            with self.assertRaises(TradingBlocked):self.cycle()
        self.assertEqual(self.broker.calls,[])
    def test_distributed_lock_and_account_scope(self):
        import psycopg
        from v519_pg_ledger import PgRuntimeLedger,LedgerBlocked
        with psycopg.connect(self.url,autocommit=True) as conn:
            conn.execute('SET search_path TO '+self.schema)
            other=PgRuntimeLedger(conn,'b'*64)
            with self.ledger.lock():
                with self.assertRaises(LedgerBlocked):
                    with other.lock():pass
            # The lock is released after normal/exceptional exits.
            with other.lock():pass
            self.seed()
            isolated=PgRuntimeLedger(conn,'c'*64)
            self.assertEqual(isolated.pending(),[])
    def test_sell_is_durable_and_cannot_oversell(self):
        ident=self.seed(); self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':6,'average':'12000'}}
        self.candidates=[]
        r=self.cycle()
        self.assertEqual(r['orders_sent'],1)
        self.assertEqual((self.broker.calls[0].side,self.broker.calls[0].quantity),('SELL',6))
    def test_private_dashboard_uses_real_observation_and_no_credentials(self):
        self.cycle(False)
        data=dashboard_snapshot(self.ledger,{'status':'OBSERVED','orders_sent':0})
        self.assertEqual(data['account']['cash'],self.broker.cash)
        self.assertEqual(data['allocation']['reserve'],self.broker.cash*40//100)
        self.assertEqual(data['orders'],[])
        self.assertFalse(data['orders_enabled'])
        self.assertNotIn('account_fingerprint',json.dumps(data,default=str))

    def test_buy_off_preserves_sell_management(self):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':6,'average':'12000'}}
        self.broker.buy_control=lambda:{'enabled':False,'connected':True,'revision':2}
        r=self.cycle()
        self.assertEqual(r['planned_buys'],0)
        self.assertEqual(self.broker.calls[0].side,'SELL')

    def test_buy_off_rechecked_before_reservation(self):
        control=self.broker.buy_control()
        states=iter([{**control,'enabled':True,'revision':1},{**control,'enabled':False,'revision':2}])
        self.broker.buy_control=lambda:next(states)
        r=self.cycle()
        self.assertEqual(r['status'],'BUY_SWITCH_OFF')
        self.assertEqual(self.broker.calls,[]);self.assertEqual(self.ledger.pending(),[])

    def test_missing_account_activation_blocks_every_order(self):
        self.conn.execute('TRUNCATE v519_live_activation')
        with self.assertRaisesRegex(TradingBlocked,'ACCOUNT_RELEASE_NOT_ACTIVATED'):self.cycle()
        self.assertEqual(self.ledger.pending(),[]);self.assertEqual(self.broker.calls,[])

    def test_account_activation_is_bound_to_reviewed_release(self):
        self.ledger.activate_release('a'*64,'fixture')
        with self.assertRaisesRegex(TradingBlocked,'ACCOUNT_RELEASE_NOT_ACTIVATED'):self.cycle()
        self.assertEqual(self.broker.calls,[])

    def test_operator_activation_requires_confirmation_and_sends_nothing(self):
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        flags['GITHUB_REPOSITORY_OWNER']='fixture'
        self.ledger.halt();self.conn.execute('TRUNCATE v519_live_activation')
        with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags):
            with self.assertRaises(TradingBlocked):
                activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,operator='fixture')
            self.assertTrue(self.ledger.halted())
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),self.assertRaisesRegex(TradingBlocked,'ACCOUNT_OWNER_ACTIVATION_REQUIRED'):
                activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,operator='other',acknowledged=True)
            r=activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                operator='fixture',acknowledged=True)
        self.assertEqual(r['status'],'LIVE_ACCOUNT_ACTIVATED')
        self.assertFalse(self.ledger.halted());self.assertTrue(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.calls,[]);self.assertEqual(self.ledger.pending(),[])

    def test_confirmed_partial_exit_can_finish_remaining_position(self):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':6,'average':'12000'}}
        self.candidates=[]
        self.cycle();first=self.ledger.pending()[0]
        self.ledger.reconcile(first,'FILLED',6)
        self.broker.positions={'144960':{'quantity':4,'sellable':4,'average':'12000'}}
        self.cycle();second=self.ledger.pending()[0]
        self.assertNotEqual(first['intent_id'],second['intent_id'])
        self.assertEqual([p.quantity for p in self.broker.calls],[6,4])

    def intercepted_live_broker(self, price, depth):
        fixture=self.broker
        live=object.__new__(KisBroker);live.execution=True;live.quote_evidence={};live.history_cache={}
        for name in ('history','snapshot','quote','power','buy_control','submit'):
            setattr(live,name,getattr(fixture,name))
        def fresh(ticker,side):
            live.quote_evidence[(ticker,side)]={'price':price,'size':depth,'at':self.now}
            return price
        live.execution_quote=fresh
        self.broker=live
        return fixture,live

    def test_fresh_buy_reprice_rechecks_cash_cap_and_book_depth(self):
        fixture,live=self.intercepted_live_broker(11000,2)
        powers=iter([(5_000_000,10000),(25000,2)])
        live.power=lambda *args:next(powers)
        r=self.cycle()
        sent=fixture.calls[0]
        self.assertEqual((sent.price,sent.quantity),(11000,2))
        self.assertLessEqual((sent.price*sent.quantity*1005+999)//1000,25000)
        self.assertEqual(r['gate'],'ALLOW')

    def test_sell_reprice_inside_exit_band_creates_no_new_intent(self):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':10,'average':'12000'}}
        self.candidates=[]
        fixture,live=self.intercepted_live_broker(11900,10)
        r=self.cycle()
        self.assertEqual(r['status'],'QUOTE_CHANGED_NO_ORDER')
        self.assertEqual(fixture.calls,[]);self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(self.ledger.dashboard_state['plans'],[])

    def test_live_snapshot_reports_armed_state_without_fabricated_execution(self):
        with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True):
            self.cycle(False);data=dashboard_snapshot(self.ledger,{'status':'OBSERVED','execution_requested':False})
        self.assertEqual(data['mode'],'LIVE_ARMED');self.assertTrue(data['orders_enabled'])
        self.assertFalse(data['activation']['broker_receipt_observed'])
        self.assertFalse(data['activation']['restart_failure_drill_verified'])

    def test_run_log_persists_summary(self):
        self.ledger.start_run('test'); self.ledger.finish_run('test',{'status':'OBSERVED','orders_sent':0})
        result=self.conn.execute("SELECT result FROM v519_runs WHERE run_id='test'").fetchone()[0]
        self.assertEqual(result['orders_sent'],0)

if __name__=='__main__':unittest.main()
