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
    def buy_control(self): return {'enabled':True,'connected':True,'revision':1}
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
        valid={'schema_version':1,'enabled':True,'revision':2,'observed_at':now.isoformat()}
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
        self.conn.execute('TRUNCATE v519_order_intents,v519_runs')
        self.conn.execute('UPDATE v519_control SET halted=false')
        self.ledger=PgRuntimeLedger(self.conn,'b'*64)
        self.broker=BrokerFixture()
        self.now=dt.datetime(2026,10,29,10,0,tzinfo=ZoneInfo('Asia/Seoul'))
        self.candidates=[{'ticker':'144960','rs':98}]
    def cycle(self,execute=True):
        return integrated_cycle(self.ledger,self.broker,self.candidates,execute=execute,now=self.now)
    def seed(self,side='BUY',day=None):
        plan=OrderPlan('144960',side,10,10000,'rs','TEST')
        return self.ledger.reserve_plan(plan,day or self.now.date())
    def history_row(self,day,fill=10,remaining=0,side='02',**extra):
        return dict(odno='000123',ord_dt=day,pdno='144960',sll_buy_dvsn_cd=side,
                    ord_qty='10',tot_ccld_qty=str(fill),rmn_qty=str(remaining),cncl_yn='N',**extra)
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
        states=iter([{'enabled':True,'connected':True,'revision':1},{'enabled':False,'connected':True,'revision':2}])
        self.broker.buy_control=lambda:next(states)
        r=self.cycle()
        self.assertEqual(r['status'],'BUY_SWITCH_OFF')
        self.assertEqual(self.broker.calls,[]);self.assertEqual(self.ledger.pending(),[])

    def test_run_log_persists_summary(self):
        self.ledger.start_run('test'); self.ledger.finish_run('test',{'status':'OBSERVED','orders_sent':0})
        result=self.conn.execute("SELECT result FROM v519_runs WHERE run_id='test'").fetchone()[0]
        self.assertEqual(result['orders_sent'],0)

if __name__=='__main__':unittest.main()
