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
    def buy_control(self): return {'enabled':True,'connected':True,'revision':1,'sell':{'enabled':True,'connected':True,'revision':0},'risk':{'connected':True,'reserve_pct':40,'per_stock_pct':30,'revision':0,'updated_at':None}}
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
    def test_sell_control_is_independent_and_invalid_values_deny_sale(self):
        import io
        now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        body={'schema_version':1,'enabled':True,'revision':3,'observed_at':now.isoformat(),
            'risk':{'reserve_pct':40,'per_stock_pct':30,'revision':0}}
        for value in (None,{}, {'enabled':True,'revision':True},{'enabled':'true','revision':0}):
            result=read_buy_control(access_token='fixture',now=now,opener=lambda *a,**k:io.StringIO(json.dumps({**body,'sell':value})))
            self.assertTrue(result['enabled']);self.assertFalse(sell_policy(result)['enabled'])
        for enabled in (True,False):
            value={**body,'sell':{'enabled':enabled,'revision':2}}
            result=read_buy_control(access_token='fixture',now=now,opener=lambda *a,**k:io.StringIO(json.dumps(value)))
            self.assertEqual(sell_policy(result)['enabled'],enabled)
            self.assertTrue(sell_policy(result)['connected'])

    def test_sell_off_blocks_actual_wire_adapter_before_network(self):
        broker=object.__new__(KisBroker)
        broker.buy_control=lambda:{'sell':{'enabled':False,'connected':True,'revision':1}}
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags),patch('urllib.request.urlopen') as wire:
            with self.assertRaises(SellDisabledBeforeSend):broker._send_verified(OrderPlan('144960','SELL',1,10000,'exit','STOP_LOSS'))
            wire.assert_not_called()

    def test_take_profit_wire_ignores_stop_lock_but_requires_release_and_book(self):
        broker=object.__new__(KisBroker);broker.execution=True;broker.account='12345678';broker.product='01'
        broker.buy_control=lambda:{'sell':{'enabled':False,'connected':True,'revision':1}}
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        broker.quote_evidence={('144960','SELL'):{'price':10000,'size':1,'at':now}}
        calls=[];broker.request=lambda *a,**k:(calls.append((a,k)) or ({},{'rt_cd':'0','output':{'ODNO':'000123'}}))
        plan=OrderPlan('144960','SELL',1,10000,'exit','TAKE_PROFIT')
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags):
            self.assertEqual(broker._send_verified(plan)['output']['ODNO'],'000123')
            self.assertEqual(len(calls),1)
            broker.quote_evidence={}
            with self.assertRaisesRegex(TradingBlocked,'EXECUTABLE_BOOK_REQUIRED'):broker._send_verified(plan)
            self.assertEqual(len(calls),1)

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

    def test_scheduled_dispatch_uses_read_only_flags_outside_krx_hours(self):
        # Execute the existing workflow shell only against stub date/python
        # binaries in a temporary directory. No DB, broker or live engine runs.
        import subprocess,textwrap
        path=pathlib.Path(__file__).resolve().parents[1]/'.github/workflows/oneil_v519_live_engine_review.yml'
        source=path.read_text().split('      - name: Run existing integrated engine\n',1)[1]
        script=textwrap.dedent(source.split('        run: |\n',1)[1].split('      - name:',1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);commands=root/'commands';commands.mkdir()
            date=commands/'date';date.write_text('''#!/bin/sh
printf '%s\\n' "$TEST_SESSION"
''');date.chmod(0o755)
            python=commands/'python';python.write_text('''#!/bin/sh
printf '%s|%s|%s|%s\\n' "$*" "$LIVE_ENABLED" "$KIS_ALLOW_ORDER" "$I_APPROVE_LIVE_KIS_ORDERS" >> "$TEST_CALLS"
''');python.chmod(0o755)
            log=root/'calls'
            for session,approved,expected in [('4 0859','test',False),('4 0900','test',True),
                    ('4 1514','test',True),('4 1515','test',False),('4 1910','test',False),
                    ('6 1000','test',False),('4 1000','',False)]:
                log.write_text('')
                environment={**os.environ,'PATH':str(commands)+os.pathsep+os.environ['PATH'],
                    'OPERATION':'auto','APPROVED_RELEASE':approved,'TEST_SESSION':session,'TEST_CALLS':str(log),
                    'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','I_APPROVE_LIVE_KIS_ORDERS':''}
                subprocess.run(['bash','-e','-c',script],cwd=root,env=environment,check=True,capture_output=True)
                calls=log.read_text().splitlines();last=calls[-1].split('|')
                self.assertEqual('--execute' in last[0],expected,(session,calls))
                self.assertEqual(last[1:3],['true','true'] if expected else ['false','false'])
                self.assertEqual(last[3],'YES' if expected else '')
                self.assertIn('--run',last[0]);self.assertEqual(len(calls),3)

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
        broker.buy_control=BrokerFixture().buy_control
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

    def owner_review(self):
        return dict(schema_version=1,operator='asura2367-boop',approved_release=release_fingerprint(),
                    source_commit='a'*40,review_run_id=123,revision=1,approved_at='2026-10-30T01:00:00+00:00')

    def test_dashboard_review_requires_exact_owner_schema_and_source(self):
        review=self.owner_review()
        self.assertEqual(release_review_policy(review),review)
        for field,value in [('operator','other'),('approved_release','bad\nvalue'),('source_commit','bad'),
                            ('revision',True),('revision',0),('review_run_id',False),('approved_at','2026-10-30')]:
            self.assertIsNone(release_review_policy({**review,field:value}),field)
        flags={'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main',
               'GITHUB_REPOSITORY':'asura2367-boop/oneil-rs-scanner','OPERATION':'auto'}
        with patch.dict(os.environ,flags):
            self.assertEqual(dashboard_release_env({'release':review}),review)
            self.assertIsNone(dashboard_release_env({'release':{**review,'approved_release':'b'*64}}))
            for field,value in [('GITHUB_REF','refs/pull/1/merge'),('OPERATION','observe'),('GITHUB_ACTIONS','false'),('GITHUB_REPOSITORY','other/repo')]:
                with patch.dict(os.environ,{field:value}):self.assertIsNone(dashboard_release_env({'release':review}))

    def test_public_review_contains_only_source_and_ci_metadata(self):
        import subprocess
        sha='a'*40
        def git(args,**kwargs):
            if args[1:3]==['show','-s']:return 'Tested code change'
            if args[-1]=='HEAD':return sha
            return 'b'*40
        with patch.dict(os.environ,{'GITHUB_SHA':sha,'GITHUB_RUN_ID':'123'}),patch.object(subprocess,'check_output',side_effect=git):
            review=public_release_review()
        self.assertEqual(set(review['source_files']),set(RELEASE_FILES))
        self.assertEqual(review['expected_release'],release_fingerprint())
        self.assertEqual(set(review),{'schema_version','expected_release','source_commit','review_run_id','summary','source_files'})

    def test_dashboard_review_env_resolver_writes_only_approved_current_digest(self):
        import sys,io
        review=self.owner_review()
        with tempfile.TemporaryDirectory() as directory:
            target=pathlib.Path(directory)/'github-env'
            flags={'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'asura2367-boop/oneil-rs-scanner','OPERATION':'auto','GITHUB_ENV':str(target)}
            with patch.dict(os.environ,flags),patch.object(sys,'argv',['engine','--dashboard-release-env']),patch('oneil_v519_live_engine.read_buy_control',return_value={'release':review}),patch('sys.stdout',new_callable=io.StringIO):
                runtime_main()
            self.assertEqual(target.read_text(),'APPROVED_RELEASE='+release_fingerprint()+'\nV519_APPROVED_RELEASE='+release_fingerprint()+'\nV519_DASHBOARD_REVIEW_REVISION=1\n')
            target.unlink()
            with patch.dict(os.environ,flags),patch.object(sys,'argv',['engine','--dashboard-release-env']),patch('oneil_v519_live_engine.read_buy_control',return_value={'release':None}),patch('sys.stdout',new_callable=io.StringIO):runtime_main()
            self.assertFalse(target.exists())

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
        self.conn.execute('TRUNCATE v519_order_intents,v519_runs,v519_live_activation,v519_sell_alerts')
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
        self.broker.buy_control=lambda:{'enabled':True,'connected':True,'revision':1,'sell':{'enabled':True,'connected':True,'revision':0}}
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

    def prepare_exit(self,average='12000'):
        ident=self.seed();self.ledger.ack(ident,'000122')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',10)
        self.broker.positions={'144960':{'quantity':10,'sellable':6,'average':average,'cost':int(average)*10,'valuation':100000,'name':'테스트 종목'}}
        self.candidates=[]

    def test_stop_lock_records_deduplicated_stop_alerts_without_intents(self):
        self.prepare_exit()
        control=self.broker.buy_control();self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':1}}
        for price in (10000,9000):
            self.broker.prices[('144960','SELL')]=price
            result=self.cycle();self.assertEqual(result['status'],'SELL_ALERT_ONLY')
            self.assertEqual(result['orders_sent'],0);self.assertEqual(self.ledger.pending(),[])
        alerts=self.ledger.sell_alerts();self.assertEqual(len(alerts),1)
        self.assertEqual((alerts[0]['reason'],alerts[0]['quantity'],alerts[0]['reference_price']),('STOP_LOSS',6,9000))
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_order_intents').fetchone()[0],1)
        self.assertEqual(dashboard_snapshot(self.ledger,result)['sell_control']['enabled'],False)

    def sell_off_with_new_candidates(self):
        self.prepare_exit();self.two_candidates()
        control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':1}}
        self.broker.invested=120000

    def test_sell_off_alerts_allow_independent_batch_buys_with_cash_caps(self):
        self.sell_off_with_new_candidates();self.fill_each_submission()
        result=self.cycle()
        self.assertEqual([p.ticker for p in self.broker.calls],['100001','100002'])
        self.assertTrue(all(p.side=='BUY' for p in self.broker.calls))
        self.assertEqual(result['orders_sent'],2);self.assertEqual(result['sell_alert_count'],1)
        self.assertEqual(result['status'],'ACKNOWLEDGED')
        self.assertEqual(len(self.ledger.sell_alerts()),1)
        self.assertEqual(self.ledger.managed()['144960'],10)
        self.assertEqual(self.ledger.pending(),[])
        charges=[(p.quantity*p.price*1005+999)//1000 for p in self.broker.calls]
        self.assertTrue(all(x<=900000 for x in charges));self.assertLessEqual(sum(charges),2880000)
        self.assertEqual(dashboard_snapshot(self.ledger,result)['sell_control']['enabled'],False)

    def test_stop_lock_keeps_take_profit_automatic_and_exit_priority(self):
        self.sell_off_with_new_candidates();self.broker.prices[('144960','SELL')]=14000
        self.candidates=self.candidates[:1]
        result=self.cycle()
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual((self.broker.calls[0].side,self.broker.calls[0].reason),('SELL','TAKE_PROFIT'))
        self.assertEqual(self.ledger.sell_alerts(),[])

    def test_stop_lock_keeps_profit_sell_when_buy_is_off(self):
        self.sell_off_with_new_candidates();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'enabled':False}
        self.broker.prices[('144960','SELL')]=14000
        result=self.cycle();self.assertEqual(result['orders_sent'],1)
        self.assertEqual(self.broker.calls[0].reason,'TAKE_PROFIT')

    def test_stop_lock_records_stop_and_sells_other_profit_in_same_cycle(self):
        self.prepare_exit();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':1}}
        plan=OrderPlan('100001','BUY',2,10000,'rs','TEST')
        ident=self.ledger.reserve_plan(plan,self.now.date());self.ledger.ack(ident,'000121')
        self.ledger.reconcile(self.ledger.pending()[0],'FILLED',2)
        self.broker.positions['100001']={'quantity':2,'sellable':2,'average':'8000'}
        result=self.cycle();self.assertEqual(result['orders_sent'],1)
        self.assertEqual((self.broker.calls[0].ticker,self.broker.calls[0].reason),('100001','TAKE_PROFIT'))
        self.assertEqual([(a['ticker'],a['reason']) for a in self.ledger.sell_alerts()],[('144960','STOP_LOSS')])

    def test_take_profit_still_blocks_on_unknown_control_and_halt(self):
        self.prepare_exit(average='8000');control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'sell':None}
        self.assertEqual(self.cycle()['status'],'SELL_CONTROL_UNAVAILABLE');self.assertEqual(self.broker.calls,[])
        self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':1}}
        self.ledger.halt()
        with self.assertRaises(TradingBlocked):self.cycle()
        self.assertEqual(self.broker.calls,[])

    def test_sell_off_alert_does_not_bypass_buy_off_or_broker_power(self):
        self.sell_off_with_new_candidates();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'enabled':False}
        self.assertEqual(self.cycle()['orders_sent'],0)
        self.broker.buy_control=lambda:control
        self.broker.power=lambda *args:(0,0)
        self.assertEqual(self.cycle()['orders_sent'],0)
        self.assertEqual(self.broker.calls,[])

    def test_sell_off_retains_held_exposure_and_saved_reserve_limit(self):
        self.sell_off_with_new_candidates();self.with_risk(80,10)
        self.broker.cash=100000;self.broker.invested=120000
        result=self.cycle()
        self.assertEqual(result['orders_sent'],0);self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.dashboard_state['allocation']['remaining'],0)
        self.assertEqual(len(self.ledger.sell_alerts()),1)

    def test_sell_off_pending_buy_still_blocks_remaining_candidates(self):
        self.sell_off_with_new_candidates()
        first=self.cycle();self.assertEqual(first['orders_sent'],1)
        self.assertEqual(first['batch_stop_reason'],'PENDING_FILL_CONFIRMATION')
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)

    def test_sell_off_observe_plans_buys_but_never_submits(self):
        self.sell_off_with_new_candidates()
        result=self.cycle(False)
        self.assertEqual(result['planned_buys'],2);self.assertEqual(result['planned_sells'],0)
        self.assertEqual(self.broker.calls,[]);self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(len(self.ledger.sell_alerts()),1)

    def test_sell_unavailable_with_exit_and_candidates_keeps_safety_block(self):
        self.sell_off_with_new_candidates();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'sell':None}
        result=self.cycle();self.assertEqual(result['status'],'SELL_CONTROL_UNAVAILABLE')
        self.assertEqual(self.broker.calls,[])

    def test_sell_on_keeps_exit_priority_over_new_buys(self):
        self.prepare_exit();self.two_candidates()
        result=self.cycle();self.assertEqual(result['orders_sent'],1)
        self.assertEqual([p.side for p in self.broker.calls],['SELL'])

    def test_missing_sell_control_blocks_sale_and_preserves_buy_setting(self):
        self.prepare_exit();control=self.broker.buy_control();self.broker.buy_control=lambda:{**control,'sell':None}
        result=self.cycle();self.assertEqual(result['status'],'SELL_CONTROL_UNAVAILABLE')
        self.assertEqual(self.broker.calls,[]);self.assertTrue(self.ledger.dashboard_state['buy_control']['enabled'])
        self.assertEqual(len(self.ledger.sell_alerts()),1)

    def test_sell_off_before_reserve_creates_no_order_intent(self):
        self.prepare_exit();on=self.broker.buy_control();off={**on,'sell':{'enabled':False,'connected':True,'revision':1}}
        controls=iter([on,off]);self.broker.buy_control=lambda:next(controls)
        result=self.cycle();self.assertEqual(result['status'],'SELL_ALERT_ONLY')
        self.assertEqual(result['order_attempts'],0);self.assertEqual(self.ledger.pending(),[])
        self.assertFalse(self.ledger.halted());self.assertEqual(self.broker.calls,[])

    def test_sell_off_at_wire_boundary_voids_only_verified_unsent_and_can_resume(self):
        self.prepare_exit();on=self.broker.buy_control();off={**on,'sell':{'enabled':False,'connected':True,'revision':1}}
        controls=iter([on,on,off,off]);self.broker.buy_control=lambda:next(controls)
        result=self.cycle();self.assertTrue(result['pre_send_cancelled'])
        self.assertEqual(result['orders_sent'],0);self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.pending(),[]);self.assertFalse(self.ledger.halted())
        row=self.conn.execute("SELECT status,verified_not_sent,broker_order_id FROM v519_order_intents WHERE side='SELL'").fetchone()
        self.assertEqual(row,('REJECTED',True,None))
        self.broker.buy_control=lambda:on
        result=self.cycle();self.assertEqual(result['orders_sent'],1)
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM v519_order_intents WHERE side='SELL'").fetchone()[0],2)

    def test_sell_network_timeout_never_becomes_verified_unsent_or_retransmitted(self):
        self.prepare_exit();self.broker.failure=TimeoutError('no acknowledgement')
        with self.assertRaises(TradingBlocked):self.cycle()
        row=self.ledger.pending()[0];self.assertFalse(row['verified_not_sent'])
        self.assertEqual(row['status'],'SUBMISSION_UNKNOWN');self.assertTrue(self.ledger.halted())
        result=self.cycle();self.assertEqual(result['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)

    def test_sell_off_does_not_require_executable_book_for_notification(self):
        self.prepare_exit();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':1}}
        fixture,live=self.intercepted_live_broker(10000,10)
        live.reference_quote=lambda code:10000
        live.execution_quote=lambda *args:(_ for _ in ()).throw(AssertionError('off must not request executable book'))
        result=self.cycle();self.assertEqual(result['status'],'SELL_ALERT_ONLY');self.assertEqual(fixture.calls,[])
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
        self.broker.buy_control=lambda:{'enabled':False,'connected':True,'revision':2,'sell':{'enabled':True,'connected':True,'revision':0}}
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

    def dashboard_owner_review(self):
        return dict(schema_version=1,operator='asura2367-boop',approved_release=release_fingerprint(),
                    source_commit='a'*40,review_run_id=123,revision=1,approved_at='2026-10-30T01:00:00+00:00')

    def test_dashboard_owner_activation_is_readonly_and_preserves_switches(self):
        review=self.dashboard_owner_review();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':review}
        self.conn.execute('TRUNCATE v519_live_activation')
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES','V519_APPROVED_RELEASE':release_fingerprint(),'GITHUB_REPOSITORY_OWNER':'asura2367-boop'}
        with patch.dict(os.environ,flags):
            result=activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                operator='asura2367-boop',acknowledged=True,dashboard_review=review)
        self.assertEqual(result['status'],'LIVE_ACCOUNT_ACTIVATED')
        self.assertEqual(result['owner_review_revision'],1)
        self.assertEqual(result['orders_sent'],0);self.assertEqual(self.broker.calls,[])
        self.assertTrue(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.buy_control()['enabled'],control['enabled'])

    def test_dashboard_owner_approval_cannot_clear_emergency_halt(self):
        review=self.dashboard_owner_review();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':review}
        self.conn.execute('TRUNCATE v519_live_activation');self.ledger.halt()
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES','V519_APPROVED_RELEASE':release_fingerprint(),'GITHUB_REPOSITORY_OWNER':'asura2367-boop'}
        with patch.dict(os.environ,flags),self.assertRaisesRegex(Exception,'EMERGENCY_HALT_ACTIVE'):
            activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                operator='asura2367-boop',acknowledged=True,dashboard_review=review)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.calls,[])

    def test_dashboard_review_changed_during_check_cannot_activate(self):
        review=self.dashboard_owner_review();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':{**review,'revision':2}}
        self.conn.execute('TRUNCATE v519_live_activation')
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES','V519_APPROVED_RELEASE':release_fingerprint(),'GITHUB_REPOSITORY_OWNER':'asura2367-boop'}
        with patch.dict(os.environ,flags),self.assertRaisesRegex(TradingBlocked,'OWNER_RELEASE_REVIEW_CHANGED'):
            activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                operator='asura2367-boop',acknowledged=True,dashboard_review=review)
        self.assertFalse(self.ledger.release_authorized(release_fingerprint()));self.assertEqual(self.broker.calls,[])

    def test_dashboard_review_cannot_activate_unresolved_order(self):
        review=self.dashboard_owner_review();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':review}
        self.seed();self.conn.execute('TRUNCATE v519_live_activation')
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES','V519_APPROVED_RELEASE':release_fingerprint(),'GITHUB_REPOSITORY_OWNER':'asura2367-boop'}
        with patch.dict(os.environ,flags),self.assertRaisesRegex(TradingBlocked,'ACTIVATION_REQUIRES_RECONCILED_ORDERS'):
            activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                operator='asura2367-boop',acknowledged=True,dashboard_review=review)
        self.assertFalse(self.ledger.release_authorized(release_fingerprint()));self.assertEqual(self.broker.calls,[])

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

class CandidateRefreshScheduleTests(unittest.TestCase):
    def source(self):
        return (pathlib.Path(__file__).resolve().parents[1]/'.github/workflows/v4b_candidate_save_auto.yml').read_text()

    def script(self, name):
        import textwrap
        step=self.source().split('      - name: '+name+'\n',1)[1].split('      - name:',1)[0]
        return textwrap.dedent(step.split("          python - <<'PY'\n",1)[1].split('\n          PY',1)[0])

    def test_refresh_crons_are_three_weekday_kst_slots(self):
        import re
        crons=re.findall(r"- cron: '([^']+)'",self.source())
        start=dt.datetime(2026,10,25,tzinfo=dt.timezone.utc)
        scheduled=[]
        for hour in range(7*24):
            utc=start+dt.timedelta(hours=hour)
            for cron in crons:
                minute,hours,_,_,weekdays=cron.split()
                low,high=map(int,weekdays.split('-'))
                if int(minute)==0 and utc.hour in map(int,hours.split(',')) and low<=(utc.weekday()+1)%7<=high:
                    scheduled.append(utc.astimezone(ZoneInfo('Asia/Seoul')))
        self.assertEqual(len(scheduled),15)
        for day in range(26,31):
            self.assertEqual(sorted(t.hour for t in scheduled if t.day==day),[8,11,14])
        self.assertTrue(all(t.weekday()<5 for t in scheduled))

    def test_workflow_selects_live_snapshots_only_in_intraday_session(self):
        import subprocess
        # Execute the real workflow date/mode step against a fixed clock and
        # a stub exchange calendar; no KIS, credentials or trading engine.
        bootstrap='''import datetime,sys,types,os
real=datetime
class Clock(real.datetime):
 @classmethod
 def now(cls,tz=None):return real.datetime.fromisoformat(os.environ['TEST_NOW'])
shim=types.ModuleType('datetime');shim.__dict__.update(vars(real));shim.datetime=Clock
sys.modules['datetime']=shim
class Calendar:
 def is_session(self,day):return real.date.fromisoformat(day).weekday()<5
 def previous_session(self,day):return real.datetime(2026,10,28)
calendar=types.ModuleType('exchange_calendars');calendar.get_calendar=lambda name:Calendar()
sys.modules['exchange_calendars']=calendar
'''
        for stamp,schedule,expected in [('08:00','0 23 * * 0-4','pre-session'),
                ('11:00','0 2,5 * * 1-5','intraday'),('14:00','0 2,5 * * 1-5','intraday'),
                ('15:15','0 2,5 * * 1-5',None)]:
            with tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory)
                result=subprocess.run(['python','-c',bootstrap+'\n'+self.script('Determine target date and confirm market session')],
                    cwd=root,env={**os.environ,'TEST_NOW':'2026-10-29T'+stamp+':00+09:00',
                    'SCHEDULE':schedule,'EVENT_NAME':'schedule','REQUESTED_DAY':'','REQUESTED_MODE':'',
                    'RUN_ID':'123','RUN_ATTEMPT':'1'},capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                if expected:self.assertEqual((root/'candidate_mode.txt').read_text(),expected)
                else:self.assertFalse((root/'candidate_target.txt').exists())

    def test_refresh_keeps_morning_archive_and_records_source_time(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);data=root/'data/v4b';history=data/'history';history.mkdir(parents=True)
            morning=history/'candidates_2026-10-29.csv';morning.write_text('opening archive')
            (root/'candidate_target.txt').write_text('2026-10-29')
            (root/'candidate_previous.txt').write_text('2026-10-28')
            (root/'candidate_mode.txt').write_text('pre-session')
            (root/'candidate_archive.txt').write_text('candidates_2026-10-29_refresh_123_1.csv')
            (data/'candidates.csv').write_text('date,ticker,rs,foreign_5d,institution_5d,volume_ratio,channel_position,signal_date,signal_basis\n2026-10-29,234690,95,10,0,1.2,0.4,2026-10-28,PREVIOUS_CONFIRMED_SESSION\n')
            (data/'diagnostics.json').write_text(json.dumps({'latest_signal_day':'2026-10-28','latest_history_day':'2026-10-28','target_date':'2026-10-29','source_basis':'PREVIOUS_CONFIRMED_SESSION','selected':1}))
            subprocess.run(['python','-c',self.script('Validate and archive candidates')],cwd=root,check=True,capture_output=True)
            subprocess.run(['python','-c',self.script('Record candidate refresh time and data basis')],cwd=root,
                env={**os.environ,'GITHUB_STEP_SUMMARY':str(root/'summary')},check=True,capture_output=True)
            self.assertEqual(morning.read_text(),'opening archive')
            self.assertEqual((history/'candidates_2026-10-29_refresh_123_1.csv').read_bytes(),(data/'candidates.csv').read_bytes())
            report=json.loads((data/'diagnostics.json').read_text())
            self.assertEqual(report['source_basis'],'PREVIOUS_CONFIRMED_SESSION')
            self.assertEqual(dt.datetime.fromisoformat(report['refreshed_at_kst']).utcoffset(),dt.timedelta(hours=9))
            self.assertIn('orders sent by this workflow: 0',(root/'summary').read_text())

    def test_invalid_refresh_cannot_archive_or_overwrite_old_snapshot(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);data=root/'data/v4b';history=data/'history';history.mkdir(parents=True)
            old=history/'candidates_2026-10-29.csv';old.write_text('opening archive')
            (root/'candidate_target.txt').write_text('2026-10-29')
            (root/'candidate_previous.txt').write_text('2026-10-28')
            (root/'candidate_mode.txt').write_text('pre-session')
            (data/'diagnostics.json').write_text(json.dumps({'latest_history_day':'2026-10-28','target_date':'2026-10-29','source_basis':'PREVIOUS_CONFIRMED_SESSION'}))
            for signal,archive in [('2026-10-29','candidates_2026-10-29_refresh_123_1.csv'),
                    ('2026-10-27','candidates_2026-10-29_refresh_123_1.csv'),
                    ('2026-10-28','../../candidates_2026-10-29.csv')]:
                (root/'candidate_archive.txt').write_text(archive)
                (data/'candidates.csv').write_text('date,ticker,rs,foreign_5d,institution_5d,volume_ratio,channel_position,signal_date\n2026-10-29,234690,95,10,0,1.2,0.4,'+signal+'\n')
                result=subprocess.run(['python','-c',self.script('Validate and archive candidates')],cwd=root,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(old.read_text(),'opening archive')
                self.assertEqual(len(list(history.iterdir())),1)

class IntradayCandidateTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,29,11,5,tzinfo=ZoneInfo('Asia/Seoul'))

    def quote(self,code='234690',**overrides):
        return {'ticker':code,'price':12760,'cumulative_volume':2000,'trading_day':'2026-10-29',
            'received_at_kst':self.now.isoformat(),**overrides}

    def history(self,root):
        import pandas as pd,numpy as np
        days=pd.bdate_range(end='2026-10-28',periods=253)
        prices=pd.DataFrame({'date':days,'ticker':'234690','name':'녹십자웰빙',
            'close':10000*np.exp(.001*np.arange(253))*(1+.02*np.sin(np.arange(253)*2*np.pi/20)),
            'volume':[1000]*252+[500]})
        flows=pd.DataFrame({'date':days[-5:],'ticker':'234690','foreign_net_qty':10,'institution_net_qty':0})
        p=root/'prices.csv';f=root/'flows.csv'
        prices.to_csv(p,index=False);flows.to_csv(f,index=False)
        return p,f

    def test_live_price_and_volume_create_candidate_absent_at_open(self):
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=self.history(root);out=root/'candidates.csv'
            self.assertEqual(generate(p,f,'2026-10-29',out),[])
            # Same confirmed history and flow; only current price/volume change.
            rows=generate(p,f,'2026-10-29',out,intraday={'234690':self.quote()},now=self.now)
            self.assertEqual([r['ticker'] for r in rows],['234690'])
            self.assertEqual(rows[0]['signal_date'],'2026-10-29')
            self.assertEqual(rows[0]['flow_signal_date'],'2026-10-28')
            self.assertEqual(rows[0]['signal_basis'],'INTRADAY_KIS_SNAPSHOT')
            candidates=scan_candidates(out,self.now)
            self.assertEqual(candidates[0]['name'],'녹십자웰빙')
            self.assertEqual(candidates[0]['observed_at_kst'],self.now.isoformat())
            with self.assertRaises(TradingBlocked):scan_candidates(out,self.now+dt.timedelta(days=1))

    def test_invalid_or_partial_quotes_preserve_previous_candidate_output(self):
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=self.history(root);out=root/'candidates.csv';out.write_text('previous list')
            for quotes in ({},{'234690':self.quote(price=0)},{'234690':self.quote(cumulative_volume=-1)},
                    {'234690':self.quote(received_at_kst=(self.now-dt.timedelta(minutes=11)).isoformat())},
                    {'234690':self.quote(received_at_kst=(self.now+dt.timedelta(minutes=1)).isoformat())},
                    {'234690':self.quote(trading_day='2026-10-28')}):
                with self.assertRaises(ValueError):generate(p,f,'2026-10-29',out,intraday=quotes,now=self.now)
                self.assertEqual(out.read_text(),'previous list')

    def test_current_intraday_bar_never_pollutes_opening_history(self):
        import pandas as pd
        from build_v4b_candidates import generate,intraday_universe
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=self.history(root);out=root/'candidates.csv'
            data=pd.read_csv(p,dtype={'ticker':str});data.loc[len(data)]=['2026-10-29','234690','녹십자웰빙',999999,999999]
            data.to_csv(p,index=False)
            self.assertEqual(generate(p,f,'2026-10-29',out),[])
            self.assertEqual(intraday_universe(p,f,'2026-10-29','2026-10-28'),['234690'])
            with self.assertRaises(ValueError):intraday_universe(p,f,'2026-10-29','2026-10-27')

    def client(self,body,status=200):
        from quote_collector_v4a import CandidateQuoteClient
        class Response:
            status_code=status
            def json(self):return body
        class Session:
            def __init__(self):self.calls=[]
            def get(self,url,**kwargs):self.calls.append((url,kwargs));return Response()
        client=object.__new__(CandidateQuoteClient)
        client.key='test';client.secret='test';client.token='test';client.session=Session()
        return client

    def body(self):
        return {'rt_cd':'0','output1':{'stck_shrn_iscd':'234690','stck_prpr':'12760','acml_vol':'2000'},
            'output2':[{'stck_bsop_date':'20261029','stck_clpr':'12760','acml_vol':'2000'}]}

    def test_kis_collector_uses_only_fixed_krx_quote_get(self):
        client=self.client(self.body());row=client.daily_candidate_snapshot('234690',clock=lambda:self.now)
        self.assertEqual(row,self.quote())
        url,kwargs=client.session.calls[0]
        self.assertTrue(url.endswith('/quotations/inquire-daily-itemchartprice'))
        self.assertEqual(kwargs['params']['FID_COND_MRKT_DIV_CODE'],'J')
        self.assertEqual(kwargs['params']['FID_INPUT_DATE_1'],'20261029')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('account',row)

    def test_kis_collector_excludes_unverified_day_and_zero_volume(self):
        body=self.body();body['output2'][0]['stck_bsop_date']='20261028'
        self.assertIsNone(self.client(body).daily_candidate_snapshot('234690',clock=lambda:self.now))
        body=self.body();body['output1']['acml_vol']='0'
        self.assertIsNone(self.client(body).daily_candidate_snapshot('234690',clock=lambda:self.now))
        body=self.body();body['output1']['stck_shrn_iscd']='005930'
        with self.assertRaises(ValueError):self.client(body).daily_candidate_snapshot('234690',clock=lambda:self.now)
        with self.assertRaises(ValueError):self.client(self.body(),302).daily_candidate_snapshot('234690',clock=lambda:self.now)

    def test_collector_failure_or_market_close_never_returns_partial_batch(self):
        from quote_collector_v4a import collect_candidate_quotes
        class Client:
            calls=[]
            def daily_candidate_snapshot(inner,code,**kwargs):
                inner.calls.append(code)
                if code=='005930':raise ValueError('failed GET')
                return self.quote(code)
        client=Client()
        with self.assertRaises(ValueError):collect_candidate_quotes(['234690','005930'],'2026-10-29',
            client=client,clock=lambda:self.now,pause=lambda n:None)
        closed=self.now.replace(hour=15,minute=15)
        client.calls=[]
        with self.assertRaises(ValueError):collect_candidate_quotes(['234690'],'2026-10-29',
            client=client,clock=lambda:closed,pause=lambda n:None)
        self.assertEqual(client.calls,[])
        expired=iter([self.now,self.now+dt.timedelta(minutes=11)])
        with self.assertRaises(ValueError):collect_candidate_quotes(['234690'],'2026-10-29',
            client=client,clock=lambda:next(expired),pause=lambda n:None)
        with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
            with self.assertRaises(ValueError):collect_candidate_quotes(['234690'],'2026-10-29',
                client=client,clock=lambda:self.now,pause=lambda n:None)
        self.assertEqual(client.calls,[])

    def test_collector_reuses_encrypted_cache_without_extra_token_request(self):
        from quote_collector_v4a import CandidateQuoteClient
        with patch.dict(os.environ,{'KIS_APP_KEY':'test','KIS_APP_SECRET':'test','LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}),\
                patch('kis_live_v4_5_2._key',return_value=b'0'*32),\
                patch('kis_live_v4_5_2.acquire_token',return_value=('test',False)) as acquire,\
                patch('kis_live_v4_5_2._persist') as persist:
            client=CandidateQuoteClient()
            self.assertEqual(client.token,'test');acquire.assert_called_once();persist.assert_not_called()
            client.close()

    def test_transient_quote_get_retries_once_without_order_api(self):
        client=self.client(self.body());first=client.session.get;calls=[]
        class Unavailable:
            status_code=500
        def get(url,**kwargs):
            calls.append(url)
            return Unavailable() if len(calls)==1 else first(url,**kwargs)
        client.session.get=get
        pauses=[]
        self.assertEqual(client.daily_candidate_snapshot('234690',clock=lambda:self.now,pause=pauses.append),self.quote())
        self.assertEqual(len(calls),2);self.assertEqual(pauses,[.8])
        self.assertTrue(all('/quotations/' in url for url in calls))

    def test_intraday_source_and_money_guards_remain_separate(self):
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=self.history(root);out=root/'candidates.csv'
            generate(p,f,'2026-10-29',out,intraday={'234690':self.quote()},now=self.now)
            candidates=scan_candidates(out,self.now)
            snapshot={'cash':500000,'invested':0,'pending':set(),'positions':{},'managed':{}}
            quote=lambda t,s:12760;power=lambda t,p:(500000,100)
            plans,allocation=plan_orders(candidates,snapshot,quote,power,reserve_pct=40,per_stock_pct=30)
            self.assertTrue(plans);self.assertLessEqual(plans[0].quantity*plans[0].price,90000)
            snapshot['bought_today']={'234690'}
            self.assertEqual(plan_orders(candidates,snapshot,quote,power)[0],[])

class RsCoverageCollectionTests(unittest.TestCase):
    def test_collection_budget_saves_real_partial_progress_without_losing_old_bars(self):
        import build_data as b,pandas as pd,io
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            original=pd.DataFrame([{'ticker':'000001','name':'a','date':'2026-10-28','close':100,'volume':10}])
            original.to_csv(root/'kis_daily_universe.csv',index=False)
            delta=pd.DataFrame([{'date':pd.Timestamp('2026-10-29'),'close':200,'volume':20}])
            with patch.object(b,'OUT',root),patch.object(b,'load_universe',return_value=[('000001','a'),('000002','b'),('000003','c')]),\
                    patch.object(b,'confirmed_session',return_value=dt.date(2026,10,29)),\
                    patch.object(b,'fetch_incremental',return_value=(delta,True)) as fetch,\
                    patch.object(b.time,'monotonic',side_effect=[0,0,61]),patch.object(b.time,'sleep'),\
                    patch.dict(os.environ,{'KIS_APP_KEY':'fixture','KIS_APP_SECRET':'fixture','KIS_COLLECTION_SECONDS':'60'}),patch('sys.stdout',new=io.StringIO()):
                b.main(shared_token='memory-only-fixture')
            saved=pd.read_csv(root/'kis_daily_universe.csv',dtype={'ticker':str});status=json.loads((root/'collection_status.json').read_text())
        self.assertEqual(set(saved.ticker),{'000001','000002'});self.assertEqual(fetch.call_count,1)
        self.assertEqual(status['last_run_attempted'],1);self.assertEqual(status['remaining_planned_symbols'],2)
        self.assertEqual(saved.loc[saved.ticker=='000001','close'].tolist(),[100])

    def test_failed_refresh_preserves_history_and_does_not_log_raw_exception(self):
        import build_data as b,pandas as pd,io
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            pd.DataFrame([{'ticker':'000001','name':'a','date':'2026-10-28','close':100,'volume':10}]).to_csv(root/'kis_daily_universe.csv',index=False)
            output=io.StringIO()
            with patch.object(b,'OUT',root),patch.object(b,'load_universe',return_value=[('000001','a')]),\
                    patch.object(b,'confirmed_session',return_value=dt.date(2026,10,29)),\
                    patch.object(b,'fetch_incremental',side_effect=RuntimeError('SENSITIVE_FIXTURE')),\
                    patch.object(b.time,'sleep'),patch.dict(os.environ,{'KIS_APP_KEY':'fixture','KIS_APP_SECRET':'fixture'}),patch('sys.stdout',new=output):
                with self.assertRaises(RuntimeError):b.main(shared_token='memory-only-fixture')
            saved=pd.read_csv(root/'kis_daily_universe.csv');status=json.loads((root/'collection_status.json').read_text())
        self.assertEqual(saved.close.tolist(),[100]);self.assertEqual(status['last_run_failures'],1)
        self.assertNotIn('SENSITIVE_FIXTURE',output.getvalue())

    def test_refreshes_all_stale_histories_instead_of_five_slots(self):
        import build_data as b,pandas as pd
        universe=[(str(n).zfill(6),str(n)) for n in range(1,16)]
        old=pd.DataFrame([{'ticker':str(n).zfill(6),'date':pd.Timestamp('2026-10-28')} for n in range(1,13)])
        plan=b.collection_plan(universe,old,dt.date(2026,10,29),bootstrap_limit=2)
        self.assertEqual([c for c,_ in plan[:2]],['000013','000014'])
        self.assertEqual(len(plan),14)
        self.assertTrue(all((str(n).zfill(6),str(n)) in plan for n in range(1,13)))

    def test_oldest_refresh_first_current_skipped(self):
        import build_data as b,pandas as pd
        universe=[('000001','a'),('000002','b'),('000003','c'),('000004','d')]
        old=pd.DataFrame([{'ticker':'000001','date':pd.Timestamp('2026-10-29')},
            {'ticker':'000002','date':pd.Timestamp('2026-10-27')},
            {'ticker':'000003','date':pd.Timestamp('2026-10-23')}])
        self.assertEqual(b.collection_plan(universe,old,dt.date(2026,10,29),bootstrap_limit=1),
            [('000004','d'),('000003','c'),('000002','b')])

    def test_confirmed_session_excludes_live_daily_bars_weekends(self):
        from build_data import confirmed_session
        for stamp,day in [('2026-10-30T14:00:00+09:00','2026-10-29'),
                ('2026-10-30T16:00:00+09:00','2026-10-30'),
                ('2026-10-31T10:00:00+09:00','2026-10-30'),
                ('2026-11-02T08:00:00+09:00','2026-10-30')]:
            self.assertEqual(confirmed_session(dt.datetime.fromisoformat(stamp)).isoformat(),day)

    def test_increment_reads_overlap_and_keeps_history(self):
        import build_data as b,pandas as pd
        old=pd.DataFrame({'date':pd.to_datetime(['2026-10-27','2026-10-28']),'close':[100,101],'volume':[10,11]})
        delta=pd.DataFrame({'date':pd.to_datetime(['2026-10-28','2026-10-29']),'close':[101,102],'volume':[11,12]})
        with patch.object(b,'fetch_year',return_value=delta) as fetch:
            result,replaced=b.fetch_incremental(None,'k','s','memory-token','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertFalse(replaced);self.assertEqual(result.close.tolist(),[101,102])
        self.assertEqual(fetch.call_count,1);self.assertEqual(fetch.call_args.kwargs,{'min_rows':1})
        self.assertLess(fetch.call_args.args[-2],dt.date(2026,10,28))

    def test_adjusted_overlap_requires_full_refetch(self):
        import build_data as b,pandas as pd
        old=pd.DataFrame({'date':pd.to_datetime(['2026-10-28']),'close':[100],'volume':[10]})
        revised=pd.DataFrame({'date':pd.to_datetime(['2026-10-28','2026-10-29']),'close':[50,51],'volume':[20,21]})
        with patch.object(b,'fetch_year',side_effect=[revised,revised]) as fetch:
            _,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertTrue(replaced);self.assertEqual(fetch.call_count,2)
        self.assertEqual(fetch.call_args.args[-2],dt.date(2025,9,1))

    def test_missing_overlap_requires_full_refetch(self):
        import build_data as b,pandas as pd
        old=pd.DataFrame({'date':pd.to_datetime(['2026-10-20']),'close':[100]})
        delta=pd.DataFrame({'date':pd.to_datetime(['2026-10-29']),'close':[50]})
        with patch.object(b,'fetch_year',side_effect=[delta,delta]) as fetch:
            _,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertTrue(replaced);self.assertEqual(fetch.call_count,2)

    def test_short_ipo_history_is_not_padded(self):
        import build_data as b,pandas as pd
        short=pd.DataFrame({'date':pd.to_datetime(['2026-10-29']),'close':[100]})
        with patch.object(b,'fetch_year',return_value=short) as fetch:
            result,replaced=b.fetch_incremental(None,'k','s','t','000001',pd.DataFrame(),dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertTrue(replaced);self.assertEqual(len(result),1);self.assertEqual(fetch.call_count,1)

    def test_coverage_is_current_not_ever_collected(self):
        import build_data as b,pandas as pd
        rows=pd.DataFrame([{'ticker':'000001','date':d} for d in pd.bdate_range(end='2026-10-29',periods=253)]
            +[{'ticker':'000002','date':pd.Timestamp('2026-10-28')}])
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'status.json'
            b.write_status(path,[('000001','a'),('000002','b'),('000003','c')],rows,dt.date(2026,10,30),1,0,expected=dt.date(2026,10,29),attempted=1,remaining_batch=1)
            d=json.loads(path.read_text())
        self.assertEqual(d['collected_symbols'],2);self.assertEqual(d['current_session_symbols'],1)
        self.assertEqual(d['current_history_253_symbols'],1);self.assertEqual(d['current_session_coverage_pct'],33.33)
        self.assertEqual(d['remaining_planned_symbols'],1)

    def test_calendar_gaps_do_not_enter_rs(self):
        import pandas as pd
        from build_v4b_candidates import history_quality
        days=pd.bdate_range(end='2026-10-29',periods=254)
        h=pd.DataFrame({'close':100,'volume':100},index=days[-253:])
        self.assertIsNone(history_quality(h,days[-1],days))
        gap=pd.DataFrame({'close':100,'volume':100},index=days.delete(100))
        self.assertEqual(history_quality(gap,days[-1],days),'misaligned_history')
        self.assertEqual(history_quality(h.iloc[:-1],days[-1],days),'missing_history')

    def test_rs_reports_actual_cohort_and_uncollected_registry(self):
        import pandas as pd,io
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=IntradayCandidateTests().history(root)
            pd.DataFrame([{'ticker':'234690'},{'ticker':'000001'},{'ticker':'000002'}]).to_csv(root/'universe_test.csv',index=False)
            diagnostic=root/'diagnostic.json'
            with patch('sys.stdout',new=io.StringIO()):generate(p,f,'2026-10-29',root/'out.csv',diagnostic)
            data=json.loads(diagnostic.read_text())
        self.assertEqual(data['rs_cohort'],1);self.assertEqual(data['registered_universe_symbols'],3)
        self.assertEqual(data['uncollected_symbols'],2);self.assertEqual(data['rs_coverage_pct'],33.33)
        self.assertEqual(data['rs_formula'],'0.4*R63+0.2*R126+0.2*R189+0.2*R252')
        self.assertEqual(data['volume_basis'],'ACTUAL_CUMULATIVE_VS_CONFIRMED_20D_DAILY')

    def test_stale_high_return_stock_cannot_distort_current_rank(self):
        import pandas as pd,numpy as np,io
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=IntradayCandidateTests().history(root)
            source=pd.read_csv(p,dtype={'ticker':str})
            old=source.copy();old.ticker='000001';old.date=pd.bdate_range(end='2026-10-27',periods=253).strftime('%Y-%m-%d');old.close=np.exp(np.arange(253)*.01)*100
            pd.concat([source,old]).to_csv(p,index=False);diagnostic=root/'d.json'
            with patch('sys.stdout',new=io.StringIO()):generate(p,f,'2026-10-29',root/'out.csv',diagnostic)
            d=json.loads(diagnostic.read_text())
        self.assertEqual(d['rs_cohort'],1);self.assertEqual(d['stale_price'],1)

    def test_parallel_quotes_return_complete_deterministic_identity(self):
        from quote_collector_v4a import collect_candidate_quotes
        fixture=IntradayCandidateTests();fixture.setUp()
        class Client:
            def daily_candidate_snapshot(self,code,**kwargs):return fixture.quote(code)
        codes=['234690','005930','000660']
        result=collect_candidate_quotes(codes,'2026-10-29',client=Client(),clock=lambda:fixture.now,pause=lambda n:None,workers=3)
        self.assertEqual(list(result),codes)
        self.assertEqual([result[c]['ticker'] for c in codes],codes)

    def test_parallel_failure_never_publishes_partial_universe(self):
        from quote_collector_v4a import collect_candidate_quotes
        fixture=IntradayCandidateTests();fixture.setUp()
        class Client:
            def daily_candidate_snapshot(self,code,**kwargs):
                if code=='005930':raise ValueError('HTTP_FAILURE')
                return fixture.quote(code)
        with self.assertRaises(ValueError):collect_candidate_quotes(['234690','005930'],'2026-10-29',client=Client(),clock=lambda:fixture.now,pause=lambda n:None,workers=2)
        for workers in [0,9,True,'8']:
            with self.assertRaises(ValueError):collect_candidate_quotes(['234690'],'2026-10-29',client=Client(),clock=lambda:fixture.now,workers=workers)

    def test_shared_data_workflows_are_read_only_and_release_covered(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        for path in ['.github/workflows/update.yml','.github/workflows/v10_collect_auto.yml']:
            text=(root/path).read_text()
            self.assertIn('python collect_shared_token.py',text)
            self.assertIn("LIVE_ENABLED: 'false'",text);self.assertIn("KIS_ALLOW_ORDER: 'false'",text)
            self.assertIn("KIS_DAILY_REFRESH_LIMIT: '3000'",text);self.assertNotIn('sleep 90',text)
        for path in ['build_data.py','build_investor.py','collect_shared_token.py','.github/workflows/update.yml','.github/workflows/v10_collect_auto.yml','requirements.txt']:
            self.assertIn(path,RELEASE_FILES)

class KisReadRetryTests(unittest.TestCase):
    path='/uapi/domestic-stock/v1/trading/inquire-balance'
    def broker(self):
        broker=object.__new__(KisBroker)
        broker.access=broker.key=broker.secret='PRIVATE_FIXTURE'
        return broker

    def response(self,body=None,raw=None):
        import io
        response=io.BytesIO(raw if raw is not None else json.dumps(body or {'rt_cd':'0','output1':[]}).encode())
        response.headers={'tr_cont':'F'}
        return response

    def error(self,status=500,body=None):
        import io
        from urllib.error import HTTPError
        return HTTPError('https://broker.test/?account=PRIVATE_FIXTURE',status,'PRIVATE_FIXTURE',
            {'authorization':'PRIVATE_FIXTURE'},io.BytesIO(json.dumps(body or {'msg_cd':'EGW00201','msg1':'PRIVATE_FIXTURE'}).encode()))

    def test_balance_500_then_success_has_bounded_read_retry(self):
        import io
        out=io.StringIO()
        with patch('urllib.request.urlopen',side_effect=[self.error(),self.response()]) as wire,patch('time.sleep') as pause,patch('sys.stdout',new=out):
            continuation,body=self.broker().request(self.path,'TTTC8434R',{})
        self.assertEqual(wire.call_count,2);pause.assert_called_once_with(.5)
        self.assertEqual(continuation,'F');self.assertEqual(body['rt_cd'],'0')
        self.assertIn('KIS_READ_RECOVERED',out.getvalue());self.assertNotIn('PRIVATE_FIXTURE',out.getvalue())
        self.assertTrue(all(c.args[0].get_method()=='GET' for c in wire.call_args_list))

    def test_exhausted_reads_still_raise_and_report_only_safe_metadata(self):
        import io
        from urllib.error import HTTPError
        out=io.StringIO()
        with patch('urllib.request.urlopen',side_effect=[self.error(503) for _ in range(3)]) as wire,patch('time.sleep') as pause,patch('sys.stdout',new=out):
            with self.assertRaises(HTTPError) as caught:self.broker().request(self.path,'TTTC8434R',{})
        self.assertEqual(wire.call_count,3);self.assertEqual(pause.call_args_list,[unittest.mock.call(.5),unittest.mock.call(1.)])
        details=failure_details(caught.exception)
        self.assertEqual(details['http_status'],503);self.assertEqual(details['broker_attempts'],3)
        self.assertEqual(details['broker_api'],'BALANCE');self.assertEqual(details['broker_code'],'EGW00201')
        self.assertNotIn('PRIVATE_FIXTURE',json.dumps(details)+out.getvalue())

    def test_auth_or_permanent_http_failure_is_not_retried(self):
        from urllib.error import HTTPError
        for status in (400,401,403,404):
            with patch('urllib.request.urlopen',side_effect=self.error(status,{'msg_cd':'EGW00123','msg1':'PRIVATE_FIXTURE'})) as wire,patch('time.sleep') as pause:
                with self.assertRaises(HTTPError):self.broker().request(self.path,'TTTC8434R',{})
            self.assertEqual(wire.call_count,1);pause.assert_not_called()

    def test_timeout_can_recover_only_on_allowlisted_read(self):
        import io
        with patch('urllib.request.urlopen',side_effect=[TimeoutError('PRIVATE_FIXTURE'),self.response()]) as wire,patch('time.sleep'),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(self.broker().request(self.path,'TTTC8434R',{})[1]['rt_cd'],'0')
        self.assertEqual(wire.call_count,2)

    def test_exhausted_read_timeout_returns_no_cached_account(self):
        import io
        with patch('urllib.request.urlopen',side_effect=TimeoutError('PRIVATE_FIXTURE')) as wire,patch('time.sleep'),patch('sys.stdout',new=io.StringIO()):
            with self.assertRaises(TimeoutError) as caught:self.broker().request(self.path,'TTTC8434R',{})
        self.assertEqual(wire.call_count,3);self.assertEqual(failure_details(caught.exception)['broker_attempts'],3)

    def test_cash_order_500_is_sent_once_and_remains_uncertain(self):
        from urllib.error import HTTPError
        with patch('urllib.request.urlopen',side_effect=self.error()) as wire,patch('time.sleep') as pause:
            with self.assertRaises(HTTPError) as caught:self.broker().request('/uapi/domestic-stock/v1/trading/order-cash','TTTC0012U',{},post=True)
        self.assertEqual(wire.call_count,1);pause.assert_not_called()
        self.assertEqual(wire.call_args.args[0].get_method(),'POST')
        self.assertEqual(failure_details(caught.exception)['broker_api'],'CASH_ORDER')
        self.assertEqual(failure_details(caught.exception)['broker_attempts'],1)

    def test_unknown_get_or_wrong_transaction_id_has_no_retry(self):
        from urllib.error import HTTPError
        for path,tr in [(self.path,'UNKNOWN'),('/uapi/domestic-stock/v1/trading/order-cash','TTTC0012U')]:
            with patch('urllib.request.urlopen',side_effect=self.error()) as wire,patch('time.sleep') as pause:
                with self.assertRaises(HTTPError):self.broker().request(path,tr,{})
            self.assertEqual(wire.call_count,1);pause.assert_not_called()

    def test_success_status_with_explicit_rate_limit_code_can_retry_read(self):
        import io
        with patch('urllib.request.urlopen',side_effect=[self.response({'rt_cd':'1','msg_cd':'EGW00201','msg1':'PRIVATE_FIXTURE'}),self.response()]) as wire,patch('time.sleep'),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(self.broker().request(self.path,'TTTC8434R',{})[1]['rt_cd'],'0')
        self.assertEqual(wire.call_count,2)

    def test_business_rejection_does_not_retry_or_expose_broker_message(self):
        with patch('urllib.request.urlopen',return_value=self.response({'rt_cd':'1','msg_cd':'APBK1234','msg1':'PRIVATE_FIXTURE'})) as wire,patch('time.sleep') as pause:
            with self.assertRaises(TradingBlocked) as caught:self.broker().request(self.path,'TTTC8434R',{})
        self.assertEqual(wire.call_count,1);pause.assert_not_called()
        self.assertEqual(str(caught.exception),'BROKER_RESPONSE_REJECTED')
        self.assertEqual(failure_details(caught.exception)['broker_code'],'APBK1234')
        self.assertNotIn('PRIVATE_FIXTURE',json.dumps(failure_details(caught.exception)))

    def test_invalid_success_json_is_not_retried(self):
        with patch('urllib.request.urlopen',return_value=self.response(raw=b'PRIVATE_FIXTURE')) as wire,patch('time.sleep') as pause:
            with self.assertRaises(ValueError):self.broker().request(self.path,'TTTC8434R',{})
        self.assertEqual(wire.call_count,1);pause.assert_not_called()

    def test_pagination_retry_reuses_identical_get_context_and_continuation(self):
        import io
        with patch('urllib.request.urlopen',side_effect=[self.error(),self.response()]) as wire,patch('time.sleep'),patch('sys.stdout',new=io.StringIO()):
            self.broker().request(self.path,'TTTC8434R',{'CTX_AREA_FK100':'cursor','CTX_AREA_NK100':'next'},continuing=True)
        first,second=[c.args[0] for c in wire.call_args_list]
        self.assertEqual(first.full_url,second.full_url);self.assertEqual(second.get_header('Tr_cont'),'N')

    def test_failure_metadata_rejects_unvalidated_codes_and_values(self):
        exc=ValueError('PRIVATE_FIXTURE');exc.broker_api='PRIVATE_FIXTURE';exc.broker_attempts=True;exc.broker_code='12345678'
        d=failure_details(exc)
        for key in ['broker_api','broker_attempts','broker_code']:self.assertNotIn(key,d)
        self.assertNotIn('PRIVATE_FIXTURE',json.dumps(d))

if __name__=='__main__':unittest.main()
