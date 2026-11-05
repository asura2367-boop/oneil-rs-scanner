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

class RegularSessionClock(dt.datetime):
    @classmethod
    def now(cls,tz=None):
        value=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)

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
        now=RegularSessionClock.now(ZoneInfo('Asia/Seoul'))
        broker.quote_evidence={('144960','SELL'):{'price':10000,'size':1,'at':now,'venue':'KRX','session':execution_session(now),'order_type':limit_order_code(execution_session(now))}}
        calls=[];broker.request=lambda *a,**k:(calls.append((a,k)) or ({},{'rt_cd':'0','output':{'ODNO':'000123'}}))
        plan=OrderPlan('144960','SELL',1,10000,'exit','TAKE_PROFIT')
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES'}
        with patch('oneil_v519_live_engine.dt.datetime',RegularSessionClock),patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags):
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
if [ "$1" = '-c' ]; then exec "$TEST_PYTHON" "$@"; fi
if [ "$2" = '--release-info' ]; then
  if [ "$APPROVED_RELEASE" = 'test' ]; then echo '{"release_configured":true}'; else echo '{"release_configured":false}'; fi
fi
printf '%s|%s|%s|%s\\n' "$*" "$LIVE_ENABLED" "$KIS_ALLOW_ORDER" "$I_APPROVE_LIVE_KIS_ORDERS" >> "$TEST_CALLS"
''');python.chmod(0o755)
            log=root/'calls'
            for session,approved,expected in [('4 0859','test',False),('4 0900','test',True),
                    ('4 1514','test',True),('4 1515','test',True),('4 1519','test',True),
                    ('4 1520','test',False),('4 1540','test',False),('4 1559','test',False),
                    ('4 1600','test',True),('4 1910','test',True),('4 1959','test',True),('4 2000','test',False),
                    ('6 1000','test',False),('4 1000','',False),('4 1000','stale-but-nonempty',False)]:
                log.write_text('')
                environment={**os.environ,'PATH':str(commands)+os.pathsep+os.environ['PATH'],
                    'TEST_PYTHON':__import__('sys').executable,'OPERATION':'auto','APPROVED_RELEASE':approved,'TEST_SESSION':session,'TEST_CALLS':str(log),
                    'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','I_APPROVE_LIVE_KIS_ORDERS':''}
                subprocess.run(['bash','-e','-c',script],cwd=root,env=environment,check=True,capture_output=True)
                calls=log.read_text().splitlines();last=calls[-1].split('|')
                self.assertEqual('--execute' in last[0],expected,(session,calls))
                self.assertEqual(last[1:3],['true','true'] if expected else ['false','false'])
                self.assertEqual(last[3],'YES' if expected else '')
                self.assertIn('--run',last[0]);self.assertEqual(len(calls),2)  # source review publication moved to source-proof

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
                self.assertEqual([r['ticker'] for r in expected],['144960'])
                with self.assertRaisesRegex(RuntimeError,'LIVE_FLAGS_NOT_DISABLED'):
                    analyze(path,now)
                path.write_text('date,ticker,rs\n2026-10-29,144960,NaN\n')
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
        with patch('kis_live_v4_5_2.required_cached_token') as auth:
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
                patch('kis_live_v4_5_2.required_cached_token',return_value='test-token'), \
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
        now=RegularSessionClock.now(ZoneInfo('Asia/Seoul'))
        broker=object.__new__(KisBroker)
        broker.execution=True;broker.account='12345678';broker.product='01'
        broker.access='fixture';broker.key='fixture';broker.secret='fixture'
        broker.buy_control=BrokerFixture().buy_control
        broker.quote_evidence={}
        for side,tr in (('BUY','TTTC0012U'),('SELL','TTTC0011U')):
            broker.quote_evidence[('144960',side)]={'price':10000,'size':10,'at':now,
                'venue':'KRX','session':'REGULAR','order_type':'00'}
            response=io.BytesIO(b'{"rt_cd":"0","output":{"ODNO":"000123"}}')
            response.headers={}
            with patch('oneil_v519_live_engine.RELEASE_LIVE_APPROVED',True),patch.dict(os.environ,flags), \
                    patch('oneil_v519_live_engine.dt.datetime',RegularSessionClock),patch('urllib.request.urlopen',return_value=response) as wire:
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

    def test_evening_real_ledger_commits_and_recovers_without_resubmit(self):
        self.now=self.now.replace(hour=17)
        result=self.cycle()
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual(result['batch_stop_reason'],'PENDING_FILL_CONFIRMATION')
        self.assertEqual(len(self.ledger.pending()),1)
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)

    def test_regular_unknown_order_remains_blocking_in_evening(self):
        self.seed()
        self.now=self.now.replace(hour=17)
        self.assertEqual(self.cycle()['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(len(self.ledger.pending()),1)

    def test_manual_diagnostics_checkpoint_survives_final_failure_record(self):
        self.ledger.start_run('diagnostic-fixture')
        checks=[{'id':'fixture','ticker':'234690','status':'BLOCKED','reason':'MANUAL_BUY_LIMIT_EXCEEDED',
                 'limit_reasons':['PER_STOCK_EXPOSURE_LIMIT']}]
        self.ledger.save_manual_buy_checks('diagnostic-fixture',checks)
        row=self.ledger.rows("SELECT result,finished_at FROM v519_runs WHERE run_id=%s",('diagnostic-fixture',))[0]
        self.assertEqual(row['result']['manual_buy_checks'],checks);self.assertIsNone(row['finished_at'])
        self.ledger.finish_run('diagnostic-fixture',{'status':'MANUAL_QUEUE_BLOCKED'})
        self.ledger.finish_run('diagnostic-fixture',{'status':'BLOCKED','failure_kind':'NETWORK_ERROR'})
        row=self.ledger.rows("SELECT result FROM v519_runs WHERE run_id=%s",('diagnostic-fixture',))[0]
        self.assertEqual(row['result']['manual_buy_checks'],checks)
        self.assertEqual(row['result']['status'],'BLOCKED')
        self.assertNotIn('account_snapshot',row['result'])

    def test_manual_diagnostic_checkpoint_is_account_scoped(self):
        from v519_pg_ledger import PgRuntimeLedger,LedgerBlocked
        self.ledger.start_run('diagnostic-fixture')
        other=PgRuntimeLedger(self.conn,'c'*64)
        with self.assertRaises(LedgerBlocked):other.save_manual_buy_checks('diagnostic-fixture',[])
        with self.assertRaises(LedgerBlocked):self.ledger.save_manual_buy_checks('absent',[])

    def manual_queue(self, requests):
        control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'manual_buys':requests,'release':{'approved_release':release_fingerprint()}}
    def owner_request(self,ticker='144960',ident='11111111-1111-4111-8111-111111111111'):
        self.broker.positions[ticker]={'quantity':10,'sellable':10,'average':'10000','cost':100000,'valuation':100000}
        self.broker.invested=sum(p['cost'] for p in self.broker.positions.values())
        return {'id':ident,'ticker':ticker,'quantity':5,'limit_price':10000,'holding_quantity':10,
            'buy_revision':1,'risk_revision':0,'account_ref':self.ledger.fingerprint,'approved_release':release_fingerprint(),
            'trading_day':self.now.date().isoformat(),'created_at':self.now.isoformat(),'expires_at':(self.now+dt.timedelta(minutes=40)).isoformat()}
    def manual_dispatch_fixture(self,requests):
        control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':owner_manual_review(release_fingerprint()),'manual_buys':requests}
    def selected_cycle(self,request):
        with patch.dict(os.environ,owner_manual_env(request['id'],release_fingerprint())),patch('oneil_v519_live_engine.require_live_release'):
            return manual_buy_cycle(self.ledger,self.broker,request['id'],now=self.now,clock=lambda:self.now)
    def test_manual_only_exact_selection_commits_before_wire_and_other_connection_cannot_race(self):
        import psycopg
        first=self.owner_request();second=self.owner_request('000002','22222222-2222-4222-8222-222222222222')
        self.manual_dispatch_fixture([first,second]);original=self.broker.submit
        def wire(plan):
            rows=self.ledger.pending()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['signal'],'owner:'+second['id'])
            self.assertEqual(rows[0]['status'],'SUBMISSION_UNKNOWN')
            with psycopg.connect(self.url,autocommit=True) as other:
                self.assertFalse(other.execute('SELECT pg_try_advisory_lock(%s)',(self.ledger.lock_key,)).fetchone()[0])
            return original(plan)
        self.broker.submit=wire
        result=self.selected_cycle(second)
        self.assertEqual(result['orders_sent'],1);self.assertEqual([p.ticker for p in self.broker.calls],['000002'])
        self.assertEqual(self.selected_cycle(second)['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.conn.execute("UPDATE v519_order_intents SET status='CANCELLED'")
        self.assertEqual(self.selected_cycle(second)['status'],'MANUAL_ALREADY_RECORDED')
        self.assertEqual(len(self.broker.calls),1)
    def test_manual_only_competing_account_lock_denies_before_broker_reads(self):
        import psycopg
        from v519_pg_ledger import LedgerBlocked
        request=self.owner_request();self.manual_dispatch_fixture([request])
        self.broker.history=lambda day:(_ for _ in ()).throw(AssertionError('history before lock'))
        with psycopg.connect(self.url,autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(%s)',(self.ledger.lock_key,))
            with self.assertRaisesRegex(LedgerBlocked,'ACCOUNT_RUNTIME_ALREADY_RUNNING'):self.selected_cycle(request)
            other.execute('SELECT pg_advisory_unlock(%s)',(self.ledger.lock_key,))
        self.assertEqual(self.broker.calls,[])
    def test_manual_only_typed_rejection_preserves_daily_key_against_new_uuid(self):
        request=self.owner_request();self.manual_dispatch_fixture([request])
        self.broker.submit=lambda plan:(_ for _ in ()).throw(OrderNotSent('MANUAL_FINAL_PRICE_CHANGED'))
        self.assertEqual(self.selected_cycle(request)['status'],'MANUAL_PRE_SUBMIT_REJECTED')
        self.assertFalse(self.ledger.halted());self.assertEqual(self.ledger.pending(),[])
        new={**request,'id':'22222222-2222-4222-8222-222222222222'};self.manual_dispatch_fixture([new])
        result=self.selected_cycle(new)
        self.assertEqual(result['manual_buy_reason'],'ALREADY_BOUGHT_TODAY')
        rows=self.ledger.rows('SELECT status,verified_not_sent FROM v519_order_intents')
        self.assertEqual(rows,[{'status':'REJECTED','verified_not_sent':True}])
    def test_manual_only_unknown_wire_halts_retains_pending_and_never_replays(self):
        request=self.owner_request();self.manual_dispatch_fixture([request]);self.broker.failure=TimeoutError('PRIVATE')
        with self.assertRaises(TimeoutError) as error:self.selected_cycle(request)
        self.assertIsNone(error.exception.execution_progress['orders_sent']);self.assertTrue(self.ledger.halted())
        self.assertEqual(len(self.ledger.pending()),1)
        self.assertEqual(self.selected_cycle(request)['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(len(self.broker.calls),1)

    def test_manual_queue_real_ledger_skips_ineligible_and_commits_before_send(self):
        first=self.owner_request();second=self.owner_request('000002','22222222-2222-4222-8222-222222222222')
        first['limit_price']=9999;self.manual_queue([first,second]);self.candidates=[{'ticker':'000003','rs':98}]
        original=self.broker.submit
        def submit(plan):
            rows=self.ledger.pending()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'SUBMISSION_UNKNOWN')
            self.assertEqual(rows[0]['signal'],'owner:'+second['id'])
            return original(plan)
        self.broker.submit=submit
        with patch('oneil_v519_live_engine.require_live_release'):result=self.cycle()
        self.assertEqual(result['orders_sent'],1);self.assertEqual([p.ticker for p in self.broker.calls],['000002'])
        with patch('oneil_v519_live_engine.require_live_release'):result=self.cycle()
        self.assertEqual(result['status'],'BLOCKED_UNRESOLVED_ORDERS');self.assertEqual(len(self.broker.calls),1)
    def test_fresh_owner_id_cannot_bypass_durable_daily_buy_key(self):
        request=self.owner_request();self.manual_queue([request]);self.candidates=[]
        ident=self.ledger.reserve_plan(OrderPlan('144960','BUY',5,10000,'owner:old-request','OWNER_AVERAGING'),self.now.date())
        self.conn.execute("UPDATE v519_order_intents SET status='REJECTED',verified_not_sent=true WHERE intent_id=%s",(ident,))
        with patch('oneil_v519_live_engine.require_live_release'):result=self.cycle()
        self.assertEqual(self.broker.calls,[])
        self.assertEqual(self.ledger.dashboard_state['manual_buy_checks'][0]['reason'],'ALREADY_BOUGHT_TODAY')

    def test_proven_prepost_buy_rejection_keeps_daily_key_without_halt(self):
        broker=object.__new__(KisBroker);broker.execution=True
        broker.quote_evidence={}
        self.broker.submit=broker.submit
        with patch('oneil_v519_live_engine.require_live_release'):
            result=self.cycle()
        self.assertEqual(result['status'],'PRE_SUBMIT_REJECTED');self.assertEqual(result['orders_sent'],0)
        self.assertFalse(self.ledger.halted());self.assertEqual(self.ledger.pending(),[])
        row=self.ledger.rows('SELECT status,verified_not_sent FROM v519_order_intents',())[0]
        self.assertEqual(row,{'status':'REJECTED','verified_not_sent':True})
        from v519_pg_ledger import LedgerBlocked
        with self.assertRaises(LedgerBlocked):self.seed()
        self.assertIn('144960',self.ledger.bought_today(self.now.date()))

    def test_manual_real_adapter_prepost_rejection_is_not_unknown(self):
        request=self.owner_request();self.manual_queue([request]);self.candidates=[]
        broker=object.__new__(KisBroker);broker.execution=True;broker.quote_evidence={}
        self.broker.submit=broker.submit
        with patch('oneil_v519_live_engine.require_live_release'):result=self.cycle()
        self.assertEqual(result['status'],'MANUAL_PRE_SUBMIT_REJECTED')
        self.assertFalse(self.ledger.halted());self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(result['manual_buy_reason'],'FRESH_EXECUTABLE_BOOK_REQUIRED')
        self.assertEqual(len(self.ledger.rows('SELECT intent_id FROM v519_order_intents',())),1)

    def test_unsent_void_refuses_acknowledged_filled_or_foreign_intent(self):
        from v519_pg_ledger import LedgerBlocked
        ident=self.seed();self.ledger.ack(ident,'000123')
        with self.assertRaises(LedgerBlocked):self.ledger.void_unsent_order(ident)
        self.assertEqual(self.ledger.pending()[0]['status'],'ACKNOWLEDGED')
        with self.assertRaises(LedgerBlocked):self.ledger.void_unsent_order('0'*64)
        self.conn.execute("UPDATE v519_order_intents SET status='FILLED',filled_quantity=quantity WHERE intent_id=%s",(ident,))
        with self.assertRaises(LedgerBlocked):self.ledger.void_unsent_order(ident)
        self.conn.execute("UPDATE v519_order_intents SET status='SUBMISSION_UNKNOWN',broker_order_id=NULL,filled_quantity=1 WHERE intent_id=%s",(ident,))
        with self.assertRaises(LedgerBlocked):self.ledger.void_unsent_order(ident)
        self.conn.execute("UPDATE v519_order_intents SET filled_quantity=0,account_fingerprint=%s WHERE intent_id=%s",('c'*64,ident))
        with self.assertRaises(LedgerBlocked):self.ledger.void_unsent_order(ident)

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
        diagnostic=failure_details(caught.exception)
        self.assertEqual(diagnostic['submission_stage'],'SUBMIT')
        self.assertEqual(diagnostic['submission_failure']['failure_kind'],'TIMEOUT')
        self.assertNotIn('not logged',json.dumps(diagnostic))
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
        clock=lambda:self.now if not self.broker.calls else self.now.replace(hour=15,minute=20)
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
    def prepare_managed_exit_batch(self):
        self.prepare_exit()
        ident=self.ledger.reserve_plan(OrderPlan('244960','BUY',3,10000,'rs','TEST'),self.now.date())
        self.ledger.ack(ident,'000121');self.ledger.reconcile(self.ledger.pending()[0],'FILLED',3)
        self.broker.positions['244960']={'quantity':10,'sellable':9,'average':'12000','cost':120000,'valuation':100000}
        self.candidates=[]
    def test_multiple_managed_exits_commit_reconcile_and_refresh_before_next(self):
        self.prepare_managed_exit_batch();ManagedExitBatchTests.fill_exits(self)
        original=self.broker.submit
        def submit(plan):
            pending=self.ledger.pending();self.assertEqual(len(pending),1)
            self.assertEqual(pending[0]['status'],'SUBMISSION_UNKNOWN')
            if self.broker.calls:
                previous=self.conn.execute("SELECT status,filled_quantity FROM v519_order_intents WHERE side='SELL' AND ticker='144960'").fetchone()
                self.assertEqual(previous,('FILLED',6))
            return original(plan)
        self.broker.submit=submit;result=self.cycle()
        self.assertEqual([(p.ticker,p.quantity) for p in self.broker.calls],[('144960',6),('244960',3)])
        self.assertEqual(result['orders_sent'],2);self.assertEqual(self.ledger.pending(),[])
        self.assertEqual(self.ledger.managed(),{'144960':4})
    def test_exit_partial_or_cancelled_fill_never_unlocks_next_ticker(self):
        for cancel in (False,True):
            self.setUp();self.prepare_managed_exit_batch();ManagedExitBatchTests.fill_exits(self,fill=2,cancel=cancel)
            result=self.cycle()
            self.assertEqual(len(self.broker.calls),1)
            self.assertEqual(result['batch_stop_reason'],'EXIT_FILL_NOT_CONFIRMED' if cancel else 'PENDING_FILL_CONFIRMATION')
            self.assertEqual(self.ledger.managed(),{'144960':8,'244960':3})
    def test_exit_fill_without_reduced_balance_never_continues(self):
        self.prepare_managed_exit_batch();ManagedExitBatchTests.fill_exits(self,balance=False)
        result=self.cycle();self.assertEqual(result['batch_stop_reason'],'BALANCE_UPDATE_PENDING')
        self.assertEqual(len(self.broker.calls),1);self.assertEqual(self.ledger.managed(),{'144960':4,'244960':3})

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
        self.assertEqual(result['planned_buys'],1);self.assertEqual(result['planned_sells'],0)
        self.assertEqual(self.ledger.dashboard_state['candidates'],self.candidates)
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

    def resume_fixture(self):
        review=self.dashboard_owner_review();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':review}
        self.conn.execute('TRUNCATE v519_live_activation');self.ledger.halt()
        flags={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'true','I_APPROVE_LIVE_KIS_ORDERS':'YES',
            'V519_APPROVED_RELEASE':release_fingerprint(),'GITHUB_REPOSITORY_OWNER':'asura2367-boop',
            'GITHUB_ACTIONS':'true','GITHUB_ACTOR':'asura2367-boop','GITHUB_EVENT_NAME':'workflow_dispatch',
            'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':'asura2367-boop/oneil-rs-scanner',
            'OPERATION':'apply-approval','OPERATOR_CONFIRMED':'true',
            'REQUESTED_RELEASE':review['approved_release'],'REQUESTED_APPROVAL_REVISION':'1'}
        return review,flags,self.ledger.halt_state()['halt_revision']

    def resume(self,review,revision):
        return activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
            operator=review['operator'],acknowledged=True,dashboard_review=review,
            resume_halt=True,expected_halt_revision=revision)

    def test_explicit_owner_resume_is_atomic_and_sends_no_orders(self):
        review,flags,revision=self.resume_fixture()
        with patch.dict(os.environ,flags):result=self.resume(review,revision)
        self.assertEqual(result['status'],'LIVE_ACCOUNT_RESUMED')
        self.assertTrue(result['activation_committed']);self.assertTrue(result['halt_resume_committed'])
        self.assertEqual(result['orders_sent'],0);self.assertFalse(result['execution_requested'])
        self.assertEqual(self.broker.calls,[]);self.assertFalse(self.ledger.halted())
        self.assertTrue(self.ledger.release_authorized(release_fingerprint()))
        self.assertNotEqual(self.ledger.halt_state()['halt_revision'],revision)
        self.assertEqual(dashboard_snapshot(self.ledger,result)['activation']['halt_revision'],self.ledger.halt_state()['halt_revision'])

    def test_resume_rejects_auto_nonowner_unconfirmed_and_changed_approval(self):
        review,flags,revision=self.resume_fixture()
        for update in ({'OPERATION':'auto'},{'GITHUB_EVENT_NAME':'schedule'},
                {'GITHUB_ACTOR':'someone-else'},{'GITHUB_REPOSITORY_OWNER':'someone-else'},
                {'OPERATOR_CONFIRMED':'false'},{'REQUESTED_RELEASE':'c'*64},
                {'REQUESTED_APPROVAL_REVISION':'2'},{'GITHUB_REF':'refs/heads/other'}):
            with self.subTest(update=update),patch.dict(os.environ,{**flags,**update}),self.assertRaisesRegex(TradingBlocked,'OWNER_HALT_RESUME_CONFIRMATION_REQUIRED|ACCOUNT_OWNER_ACTIVATION_REQUIRED'):
                self.resume(review,revision)
            self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.calls,[])

    def test_resume_new_halt_during_fresh_account_check_rejects_stale_click(self):
        review,flags,revision=self.resume_fixture();original=self.broker.snapshot
        def snapshot():
            self.ledger.halt()
            return original()
        self.broker.snapshot=snapshot
        with patch.dict(os.environ,flags),self.assertRaisesRegex(Exception,'HALT_REVISION_CHANGED'):
            self.resume(review,revision)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.calls,[])

    def test_resume_fresh_review_change_blocks_without_clearing_halt(self):
        review,flags,revision=self.resume_fixture();control=self.broker.buy_control()
        self.broker.buy_control=lambda:{**control,'release':{**review,'revision':2}}
        with patch.dict(os.environ,flags),self.assertRaisesRegex(TradingBlocked,'OWNER_RELEASE_REVIEW_CHANGED'):
            self.resume(review,revision)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))

    def test_resume_requires_successful_fresh_account_read(self):
        review,flags,revision=self.resume_fixture()
        with patch.object(self.broker,'snapshot',side_effect=TradingBlocked('ACCOUNT_UNAVAILABLE')),patch.dict(os.environ,flags),self.assertRaisesRegex(TradingBlocked,'ACCOUNT_UNAVAILABLE'):
            self.resume(review,revision)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))

    def test_resume_unresolved_orders_block_and_transaction_rechecks_pending(self):
        self.seed();review,flags,revision=self.resume_fixture()
        with patch.dict(os.environ,flags),self.assertRaisesRegex(TradingBlocked,'ACTIVATION_REQUIRES_RECONCILED_ORDERS'):
            self.resume(review,revision)
        with self.assertRaisesRegex(Exception,'ACTIVATION_NOT_RECONCILED'):
            self.ledger.activate_release(release_fingerprint(),'fixture',preserve_halt=True,resume_halt=True,expected_halt_revision=revision)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
        self.assertEqual(self.broker.calls,[])

    def test_resume_database_failure_rolls_back_activation_and_halt(self):
        review,flags,revision=self.resume_fixture()
        self.conn.execute("CREATE FUNCTION reject_resume() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure'; END $$")
        self.conn.execute('CREATE TRIGGER reject_resume BEFORE UPDATE ON v519_control FOR EACH ROW EXECUTE FUNCTION reject_resume()')
        try:
            with self.assertRaisesRegex(Exception,'fixture failure'):
                self.ledger.activate_release(release_fingerprint(),'fixture',preserve_halt=True,resume_halt=True,expected_halt_revision=revision)
            self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
            self.assertEqual(self.ledger.halt_state()['halt_revision'],revision)
        finally:
            self.conn.execute('DROP TRIGGER reject_resume ON v519_control');self.conn.execute('DROP FUNCTION reject_resume()')

    def test_concurrent_halt_wins_against_waiting_resume(self):
        import psycopg,threading
        from concurrent.futures import ThreadPoolExecutor
        from v519_pg_ledger import PgRuntimeLedger
        review,flags,revision=self.resume_fixture();started=threading.Event()
        def resume():
            with psycopg.connect(self.url,autocommit=True) as conn:
                conn.execute('SET search_path TO '+self.schema)
                ledger=PgRuntimeLedger(conn,self.ledger.fingerprint)
                started.set()
                ledger.activate_release(release_fingerprint(),'fixture',preserve_halt=True,resume_halt=True,expected_halt_revision=revision)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.conn.transaction():
                self.conn.execute('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
                future=pool.submit(resume)
                self.assertTrue(started.wait(5));self.ledger.halt()
            with self.assertRaisesRegex(Exception,'HALT_REVISION_CHANGED'):future.result(timeout=5)
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))

    def test_every_halt_write_changes_revision_even_in_one_transaction(self):
        self.ledger.halt();before=self.ledger.halt_state()['halt_revision']
        with self.conn.transaction():
            self.ledger.halt();first=self.ledger.halt_state()['halt_revision']
            self.ledger.halt();second=self.ledger.halt_state()['halt_revision']
        self.assertEqual(len({before,first,second}),3)

    def test_default_ledger_activation_cannot_clear_halt(self):
        review,flags,revision=self.resume_fixture()
        with self.assertRaisesRegex(Exception,'EMERGENCY_HALT_ACTIVE'):
            self.ledger.activate_release(release_fingerprint(),'fixture')
        self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))

    def test_ledger_resume_rejects_missing_token_and_legacy_mode(self):
        review,flags,revision=self.resume_fixture()
        for values in ({'preserve_halt':True,'resume_halt':True},
                {'preserve_halt':False,'resume_halt':True,'expected_halt_revision':revision},
                {'preserve_halt':True,'expected_halt_revision':revision},
                {'preserve_halt':True,'resume_halt':True,'expected_halt_revision':'invalid'}):
            with self.assertRaisesRegex(Exception,'HALT_RESUME_REQUEST_INVALID'):
                self.ledger.activate_release(release_fingerprint(),'fixture',**values)
            self.assertTrue(self.ledger.halted());self.assertFalse(self.ledger.release_authorized(release_fingerprint()))

    def test_repeated_click_cannot_resume_a_newer_halt(self):
        review,flags,revision=self.resume_fixture()
        with patch.dict(os.environ,flags):self.resume(review,revision)
        self.ledger.halt()
        with patch.dict(os.environ,flags),self.assertRaisesRegex(Exception,'HALT_REVISION_CHANGED'):self.resume(review,revision)
        self.assertTrue(self.ledger.halted());self.assertEqual(self.broker.calls,[])

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
            with self.assertRaisesRegex(TradingBlocked,'EMERGENCY_HALT_ACTIVE'):
                activate_live_account(self.ledger,self.broker,self.candidates,now=self.now,
                    operator='fixture',acknowledged=True)
            self.assertTrue(self.ledger.halted())
            self.assertFalse(self.ledger.release_authorized(release_fingerprint()))
            # A legacy activation may arm an already unhalted fixture, never clear halt.
            self.conn.execute('UPDATE v519_control SET halted=false')
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
        # These integration fixtures start after source/indicator qualification.
        for c in self.candidates:c['technical']={'bb_upper':9000,'cloud_a':8800,'cloud_b':8900}
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

    def test_rs_only_fresh_buy_does_not_require_technical_breakout(self):
        fixture,live=self.intercepted_live_broker(8000,2)
        result=self.cycle()
        self.assertEqual(len(fixture.calls),1)
        self.assertEqual(fixture.calls[0].price,8000)

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
            'received_at_kst':self.now.isoformat(),'high':12800,'low':12500,**overrides}

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

    def test_rs_only_accepts_valid_closes_without_technical_ohlc(self):
        from build_v4b_candidates import generate
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);p,f=self.history(root);out=root/'candidates.csv';diag=root/'diagnostic.json'
            self.assertEqual(len(generate(p,f,'2026-10-29',out)),1)
            rows=generate(p,f,'2026-10-29',out,diag,intraday={'234690':self.quote()},now=self.now)
            self.assertEqual(len(rows),1)
            self.assertEqual(json.loads(diag.read_text())['technical_data_missing'],0)
            self.assertEqual(len(scan_candidates(out,self.now)),1)

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
            self.assertEqual(len(generate(p,f,'2026-10-29',out)),1)
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
            'output2':[{'stck_bsop_date':'20261029','stck_clpr':'12760','acml_vol':'2000','stck_hgpr':'12800','stck_lwpr':'12500'}]}

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
                patch('kis_live_v4_5_2.required_cached_token',return_value='test') as acquire,\
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
            self.assertEqual(len(plans),1);self.assertLessEqual(plans[0].price*plans[0].quantity,90000)
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
            [('000004','d'),('000003','c'),('000002','b'),('000001','a')]) # current close-only history also needs OHLC backfill

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
        old['high']=old.close+1;old['low']=old.close-1
        with patch.object(b,'fetch_year',return_value=delta) as fetch:
            result,replaced=b.fetch_incremental(None,'k','s','memory-token','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertFalse(replaced);self.assertEqual(result.close.tolist(),[101,102])
        self.assertEqual(fetch.call_count,1);self.assertEqual(fetch.call_args.kwargs,{'min_rows':1})
        self.assertLess(fetch.call_args.args[-2],dt.date(2026,10,28))

    def test_adjusted_overlap_requires_full_refetch(self):
        import build_data as b,pandas as pd
        old=pd.DataFrame({'date':pd.to_datetime(['2026-10-28']),'close':[100],'volume':[10]})
        revised=pd.DataFrame({'date':pd.to_datetime(['2026-10-28','2026-10-29']),'close':[50,51],'volume':[20,21]})
        old['high']=old.close+1;old['low']=old.close-1
        with patch.object(b,'fetch_year',side_effect=[revised,revised]) as fetch:
            _,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
        self.assertTrue(replaced);self.assertEqual(fetch.call_count,2)
        self.assertEqual(fetch.call_args.args[-2],dt.date(2025,9,1))

    def test_missing_overlap_requires_full_refetch(self):
        import build_data as b,pandas as pd
        old=pd.DataFrame({'date':pd.to_datetime(['2026-10-20']),'close':[100]})
        delta=pd.DataFrame({'date':pd.to_datetime(['2026-10-29']),'close':[50]})
        old['high']=old.close+1;old['low']=old.close-1
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

class WeekendClosureTests(unittest.TestCase):
 def test_kst_boundaries(self):
  from zoneinfo import ZoneInfo
  z=ZoneInfo('Asia/Seoul')
  self.assertFalse(weekend_closed(dt.datetime(2026,10,30,23,59,tzinfo=z)))
  self.assertTrue(weekend_closed(dt.datetime(2026,10,30,15,0,tzinfo=dt.timezone.utc)))
  self.assertTrue(weekend_closed(dt.datetime(2026,11,1,23,59,tzinfo=z)))
  self.assertFalse(weekend_closed(dt.datetime(2026,11,2,0,0,tzinfo=z)))
  self.assertFalse(monitoring_open(dt.datetime(2026,10,31,10,0,tzinfo=z)))
 def test_weekend_cycle_has_no_external_calls(self):
  from unittest.mock import Mock
  from zoneinfo import ZoneInfo
  for day in (31,):
   for execute in (False,True):
    ledger=Mock();broker=Mock()
    result=integrated_cycle(ledger,broker,[],execute=execute,now=dt.datetime(2026,10,day,10,tzinfo=ZoneInfo('Asia/Seoul')))
    self.assertEqual(result['status'],'MARKET_CLOSED')
    self.assertFalse(result['recovery_checked'])
    self.assertEqual(result['orders_sent'],0)
    self.assertEqual(ledger.mock_calls,[]);self.assertEqual(broker.mock_calls,[])
 def test_cli_weekend_skips_database(self):
  import io,sys
  from unittest.mock import patch,Mock
  class Clock(dt.datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,11,1,10,tzinfo=ZoneInfo('Asia/Seoul'))
  with tempfile.TemporaryDirectory() as d:
   out=str(pathlib.Path(d)/'result.json')
   with patch('oneil_v519_live_engine.dt.datetime',Clock),patch('v519_pg_ledger.connect') as connect,patch.object(sys,'argv',['engine','--run','--output',out]),patch('sys.stdout',new_callable=io.StringIO):
    runtime_main()
   connect.assert_not_called()
   self.assertEqual(json.loads(pathlib.Path(out).read_text())['status'],'MARKET_CLOSED')

class TechnicalTimingTests(unittest.TestCase):
 def bars(self):
  return [{'high':101,'low':99,'close':100} for _ in range(79)]+[{'high':111,'low':99,'close':110}]
 def test_bb_and_shifted_cloud_exact(self):
  from oneil_live_v5_9 import technical_timing
  t=technical_timing(self.bars())
  self.assertTrue(t['entry']);self.assertFalse(t['exit'])
  self.assertEqual(t['cloud_a'],100);self.assertEqual(t['cloud_b'],100)
  self.assertAlmostEqual(t['bb_middle'],100.5)
  self.assertAlmostEqual(t['bb_upper'],100.5+2*(4.75**.5))
  # Recent high must not leak into today's cloud, computed 26 bars ago.
  b=self.bars();b[-1]['high']=1000
  self.assertEqual(technical_timing(b)['cloud_b'],100)
 def test_missing_short_nonfinite_or_fake_high_low_rejected(self):
  from oneil_live_v5_9 import technical_timing
  with self.assertRaises(ValueError):technical_timing(self.bars()[:77])
  for v in (float('nan'),float('inf'),0,98):
   b=self.bars();b[-1]['high']=v
   with self.assertRaises(ValueError):technical_timing(b)
 def test_proof_and_scanner_require_both_indicators(self):
  import csv
  from oneil_live_v5_9 import technical_timing,read_technical_proof
  t=technical_timing(self.bars());row={'reference_close':110,'technical_policy':'BB_ICHI_TREND_V1','technical_details':json.dumps(t)}
  self.assertIsNotNone(read_technical_proof(row))
  t['ichimoku_bullish']=False;row['technical_details']=json.dumps(t)
  self.assertIsNone(read_technical_proof(row));self.assertIsNone(read_technical_proof({}))
  t=technical_timing(self.bars())
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'candidate.csv'
   r={'date':'2026-10-29','ticker':'234690','rs':95,'foreign_5d':1,'institution_5d':0,'volume_ratio':2,'channel_up':True,'channel_position':.3,'reference_close':110,'technical_policy':t['policy'],'technical_details':json.dumps(t)}
   with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(r));w.writeheader();w.writerow(r)
   self.assertEqual(len(scan_candidates(p,dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul')))),1)
 def test_technical_exit_honors_stop_lock_and_keeps_percentage_exits(self):
  from oneil_live_v5_9 import technical_timing
  b=self.bars();b[-1]={'high':101,'low':89,'close':90};t=technical_timing(b)
  self.assertTrue(t['exit'])
  snap={'cash':100000,'invested':10000,'pending':set(),'managed':{'234690':1},'positions':{'234690':{'quantity':1,'sellable':1,'average':100,'technical':t}}}
  control={'sell':{'connected':True,'enabled':False,'revision':1}}
  for price,reason,allowed in ((103,'TAKE_PROFIT_SIGNAL',True),(98,'STOP_LOSS_SIGNAL',False),(94,'STOP_LOSS',False),(111,'TAKE_PROFIT',True)):
   plans,_=plan_orders([],snap,lambda *_:price,lambda *_:(0,0))
   self.assertEqual(plans[0].reason,reason);self.assertEqual(sell_plan_allowed(plans[0],control),allowed)
  snap['positions']['234690']['technical']=None
  self.assertEqual(plan_orders([],snap,lambda *_:98,lambda *_:(0,0))[0],[])
  self.assertEqual(plan_orders([],snap,lambda *_:94,lambda *_:(0,0))[0][0].reason,'STOP_LOSS')
 def test_close_only_histories_force_full_backfill(self):
  import build_data as b,pandas as pd
  old=pd.DataFrame({'date':pd.to_datetime(['2026-10-29']),'close':[100],'volume':[10]})
  with patch.object(b,'fetch_year',return_value=old) as fetch:
   _,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,10,29))
  self.assertTrue(replaced);self.assertEqual(fetch.call_args.args[-2],dt.date(2025,9,1))


class CoverageRepairTests(unittest.TestCase):
 def history(self):
  import pandas as pd
  return pd.DataFrame({'date':pd.bdate_range(end='2026-10-30',periods=253),'close':100,'volume':100,
                       'high':float('nan'),'low':float('nan')})

 def test_technical_result_uses_exact_real_78_bar_window(self):
  from oneil_live_v5_9 import technical_timing
  bars=TechnicalTimingTests().bars()
  expected=technical_timing(bars[-78:])
  prefix=[{'close':100,'high':float('nan'),'low':float('nan')} for _ in range(200)]
  self.assertEqual(technical_timing(prefix+bars[-78:]),expected)
  broken=prefix+bars[-78:];broken[-78]={'close':100,'high':float('nan'),'low':99}
  with self.assertRaises(ValueError):technical_timing(broken)

 def test_backfill_requests_recent_window_and_preserves_real_rs_closes(self):
  import build_data as b,pandas as pd
  old=self.history();delta=old.tail(78).copy();delta['high']=101;delta['low']=99
  delta.loc[len(delta)]={'date':pd.Timestamp('2026-11-02'),'close':101,'volume':100,'high':102,'low':100}
  with patch.object(b,'fetch_year',return_value=delta) as fetch:
   rows,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,11,2))
  self.assertFalse(replaced);self.assertEqual(fetch.call_count,1)
  self.assertEqual(fetch.call_args.args[-2],old.date.iloc[-78].date())
  merged=pd.concat([old,rows]).drop_duplicates('date',keep='last').sort_values('date')
  self.assertEqual(len(merged),254);self.assertEqual(merged.close.iloc[0],100)
  self.assertTrue(b.recent_ohlc_ready(merged))
  ready=merged.assign(ticker='000001')
  self.assertEqual(b.collection_plan([('000001','a')],ready,dt.date(2026,11,2)),[])

 def test_recent_window_still_requires_full_adjustment_refetch(self):
  import build_data as b
  old=self.history();delta=old.tail(78).copy();delta['close']=50;delta['high']=51;delta['low']=49
  with patch.object(b,'fetch_year',side_effect=[delta,delta]) as fetch:
   _,replaced=b.fetch_incremental(None,'k','s','t','000001',old,dt.date(2025,9,1),dt.date(2026,11,2))
  self.assertTrue(replaced);self.assertEqual(fetch.call_count,2)
  self.assertEqual(fetch.call_args.args[-2],dt.date(2025,9,1))

 def test_complete_registered_universe_is_planned_without_100_symbol_ceiling(self):
  import build_data as b,pandas as pd
  universe=[(str(n).zfill(6),str(n)) for n in range(1,2676)]
  old=pd.DataFrame([{'ticker':str(n).zfill(6),'date':pd.Timestamp('2026-10-30')} for n in range(1,1729)])
  plan=b.collection_plan(universe,old,dt.date(2026,11,2))
  self.assertEqual(len(plan),2675);self.assertEqual(len(set(c for c,_ in plan)),2675)
  self.assertEqual(plan[100],universe[0]);self.assertEqual(set(plan),set(universe))

 def test_price_repair_uses_shared_token_without_extra_investor_queries(self):
  import collect_shared_token as collect,io
  with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}),\
       patch('kis_live_v4_5_2.required_cached_token',return_value='MEMORY_FIXTURE'),\
       patch.object(collect,'acquire_token') as issue,patch.object(collect,'_persist') as persist,\
       patch('build_data.main') as prices,patch('build_investor.main') as flows,patch('sys.stdout',new=io.StringIO()):
   collect.main(prices_only=True)
  prices.assert_called_once_with(shared_token='MEMORY_FIXTURE');flows.assert_not_called()
  issue.assert_not_called();persist.assert_not_called()

 def test_empty_cache_and_failed_bootstrap_preserve_header_and_diagnostics(self):
  import build_data as b,io,pandas as pd
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);p=root/'kis_daily_universe.csv'
   pd.DataFrame(columns=['ticker','name','date','close','volume']).to_csv(p,index=False)
   with patch.object(b,'OUT',root),patch.object(b,'load_universe',return_value=[('000001','a')]),\
        patch.object(b,'confirmed_session',return_value=dt.date(2026,11,2)),\
        patch.object(b,'fetch_incremental',side_effect=RuntimeError('fixture failure')),patch.object(b.time,'sleep'),\
        patch.dict(os.environ,{'KIS_APP_KEY':'fixture','KIS_APP_SECRET':'fixture'}),patch('sys.stdout',new=io.StringIO()):
    with self.assertRaisesRegex(RuntimeError,'성공 종목 0개'):b.main(shared_token='MEMORY_FIXTURE')
   self.assertTrue(pd.read_csv(p).empty)
   self.assertEqual(json.loads((root/'collection_status.json').read_text())['last_run_failures'],1)

class NoInvestorFlowSelectionTests(unittest.TestCase):
 def test_builder_does_not_read_missing_invalid_or_negative_flow_files(self):
  from build_v4b_candidates import generate
  fixture=IntradayCandidateTests();fixture.setUp()
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);p,f=fixture.history(root);out=root/'c.csv';diag=root/'d.json'
   # Isolate investor flow from technical calculation (covered above).
   with patch('oneil_live_v5_9.technical_timing',return_value={'entry':True}):
    results=[]
    for flow_text in (None,'not,a,flow,csv\n','ticker,date,foreign_net_qty,institution_net_qty\n234690,2020-01-01,-100,-200\n'):
     f.unlink(missing_ok=True)
     if flow_text is not None:f.write_text(flow_text)
     rows=generate(p,f,'2026-10-29',out,diag,intraday={'234690':fixture.quote()},now=fixture.now)
     self.assertEqual([r['ticker'] for r in rows],['234690'])
     self.assertIsNone(rows[0]['foreign_5d']);self.assertIsNone(rows[0]['institution_5d'])
     self.assertFalse(json.loads(diag.read_text())['flow_filter_enabled']);results.append(rows)
    self.assertEqual(results[0],results[1]);self.assertEqual(results[1],results[2])
   with patch('oneil_live_v5_9.technical_timing',return_value={'entry':False}):
    self.assertEqual(len(generate(p,f,'2026-10-29',out,intraday={'234690':fixture.quote()},now=fixture.now)),1)

 def test_scanner_ignores_all_flow_values_and_keeps_other_entry_conditions(self):
  import csv
  from oneil_live_v5_9 import technical_timing
  t=technical_timing(TechnicalTimingTests().bars())
  now=dt.datetime(2026,10,29,11,5,tzinfo=ZoneInfo('Asia/Seoul'))
  row={'date':'2026-10-29','ticker':'234690','rs':95,'volume_ratio':2,'channel_up':True,'channel_position':.3,
       'reference_close':110,'technical_policy':t['policy'],'technical_details':json.dumps(t),
       'signal_basis':'INTRADAY_KIS_SNAPSHOT','signal_date':'2026-10-29','observed_at_kst':now.isoformat()}
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'c.csv'
   def check(extra):
    r={**row,**extra}
    with p.open('w',newline='') as f:
     w=csv.DictWriter(f,fieldnames=list(r));w.writeheader();w.writerow(r)
    return scan_candidates(p,now)
   for f,i in ((None,None),(-10,-20),(0,0),('bad','NaN')):
    self.assertEqual(len(check({'foreign_5d':f,'institution_5d':i,'flow_signal_date':'invalid'})),1)
   self.assertEqual(len(check({})),1)
   self.assertEqual(check({'rs':89}),[])
   for extra in ({'rs':90},{'volume_ratio':.9},{'channel_up':False},{'channel_position':.76},{'technical_details':''},{'volume_ratio':'bad','channel_position':'bad'}):
    self.assertEqual(len(check(extra)),1)

 def test_equal_rs_priority_no_longer_prefers_positive_flow(self):
  snap={'cash':100000,'invested':0,'pending':set(),'managed':{},'positions':{}}
  c=[{'ticker':'000002','rs':95,'flow_category':'BOTH'},{'ticker':'000001','rs':95,'flow_category':'NEITHER'}]
  plans,_=plan_orders(c,snap,lambda *_:1000,lambda *_:(100000,100),max_buys=1)
  self.assertEqual([p.ticker for p in plans],['000001'])

class SharedSessionOnlyTests(unittest.TestCase):
 def test_consumers_reuse_cache_even_in_authorized_live_profile(self):
  import base64,kis_live_v4_5_2 as cache
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   key=os.urandom(32);path=pathlib.Path(d)/'cache.json'
   with patch.object(cache,'CACHE',path),patch.dict(os.environ,{'KIS_TOKEN_CACHE_KEY':base64.urlsafe_b64encode(key).decode(),'LIVE_ENABLED':'true'}),patch.object(cache,'request') as auth:
    cache._save(key,'SYNTHETIC_PRIVATE_SESSION',dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=5))
    self.assertEqual(cache.required_cached_token(),'SYNTHETIC_PRIVATE_SESSION')
    self.assertEqual(cache.required_cached_token(),'SYNTHETIC_PRIVATE_SESSION')
    auth.assert_not_called()
 def test_missing_expired_or_corrupt_cache_never_triggers_auth_fallback(self):
  import base64,kis_live_v4_5_2 as cache
  with tempfile.TemporaryDirectory() as d:
   key=os.urandom(32);path=pathlib.Path(d)/'cache.json'
   with patch.object(cache,'CACHE',path),patch.dict(os.environ,{'KIS_TOKEN_CACHE_KEY':base64.urlsafe_b64encode(key).decode()}),patch.object(cache,'request') as auth:
    with self.assertRaisesRegex(cache.CacheBlocked,'REFRESH_REQUIRED'):cache.required_cached_token()
    cache._save(key,'SYNTHETIC_PRIVATE_SESSION',dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1))
    with self.assertRaisesRegex(cache.CacheBlocked,'REFRESH_REQUIRED'):cache.required_cached_token()
    path.write_text('{}')
    with self.assertRaisesRegex(cache.CacheBlocked,'DECRYPT_OR_FORMAT'):cache.required_cached_token()
    auth.assert_not_called()
 def test_preparation_never_reads_account_or_market_and_outputs_only_ready(self):
  import collect_shared_token as collect,io
  class Friday(dt.datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,10,30,9,tzinfo=dt.timezone.utc)
  with tempfile.TemporaryDirectory() as d:
   out=pathlib.Path(d)/'output.txt'
   flags={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','KIS_APP_KEY':'fixture-key','KIS_APP_SECRET':'fixture-secret','GITHUB_OUTPUT':str(out)}
   with patch.dict(os.environ,flags),patch.object(collect.dt,'datetime',Friday),patch.object(collect,'_key',return_value=b'k'*32),patch.object(collect,'acquire_token',return_value=('DO_NOT_PRINT',True)) as acquire,patch.object(collect,'_persist') as persist,patch('build_data.main') as prices,patch('build_investor.main') as flows,patch('sys.stdout',new_callable=io.StringIO) as log:
    collect.main(prepare_only=True)
   self.assertEqual(out.read_text(),'ready=true\n');self.assertNotIn('DO_NOT_PRINT',log.getvalue())
   self.assertEqual(acquire.call_args.kwargs['min_validity'],dt.timedelta(minutes=100));persist.assert_called_once();prices.assert_not_called();flows.assert_not_called()
 def test_weekend_preparation_never_issues_or_reads_cache_key(self):
  import collect_shared_token as collect,io
  class Saturday(dt.datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,10,31,9,tzinfo=dt.timezone.utc)
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','GITHUB_OUTPUT':d+'/out'}),patch.object(collect.dt,'datetime',Saturday),patch.object(collect,'_key') as key,patch.object(collect,'acquire_token') as auth,patch('sys.stdout',new_callable=io.StringIO):
   collect.main(prepare_only=True);key.assert_not_called();auth.assert_not_called();self.assertEqual(pathlib.Path(d+'/out').read_text(),'ready=false\n')
 def test_collectors_pass_same_cached_session_without_auth_or_persist(self):
  import collect_shared_token as collect
  with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}),patch('kis_live_v4_5_2.required_cached_token',return_value='MEMORY_FIXTURE'),patch.object(collect,'acquire_token') as auth,patch.object(collect,'_persist') as persist,patch('build_data.main') as prices,patch('build_investor.main') as flows,patch('sys.stdout',new_callable=__import__('io').StringIO):
   collect.main();auth.assert_not_called();persist.assert_not_called();prices.assert_called_once_with(shared_token='MEMORY_FIXTURE');flows.assert_called_once_with(shared_token='MEMORY_FIXTURE')
 def test_unnecessary_automatic_consumers_retired_and_refresh_serialized(self):
  root=pathlib.Path(__file__).resolve().parents[1]
  for name in ('quote_intraday_v4a.yml','quote_v4e_hourly.yml','v10_collect_auto.yml'):
   self.assertNotIn('  schedule:',(root/'.github/workflows'/name).read_text())
  issuer=(root/'.github/workflows/kis_live_v4_5_2.yml').read_text()
  self.assertIn('group: kis-shared-token-cache',issuer);self.assertIn('python collect_shared_token.py --prepare-only',issuer)
  self.assertNotIn('KIS_ACCOUNT_NO',issuer);self.assertNotIn('run_checks',issuer)
  self.assertIn('.github/workflows/kis_live_v4_5_2.yml',RELEASE_FILES)
  engine=(root/'.github/workflows/oneil_v519_live_engine_review.yml').read_text()
  self.assertIn("inputs.operation != 'halt'",engine);self.assertIn("if: needs.session.outputs.ready == 'true'",engine)

class ManualAveragingTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,11,2,10,tzinfo=ZoneInfo('Asia/Seoul'))
        self.req={'id':'11111111-1111-4111-8111-111111111111','ticker':'000001','quantity':5,'limit_price':8000,'holding_quantity':10,
            'buy_revision':1,'risk_revision':0,'account_ref':'a'*64,'approved_release':'b'*64,'trading_day':'2026-11-02',
            'created_at':self.now.isoformat(),'expires_at':(self.now+dt.timedelta(minutes=20)).isoformat()}
        self.snap={'cash':1000000,'invested':100000,'pending':set(),'managed':{'000001':10},'positions':{'000001':{'quantity':10,'sellable':10,'cost':100000,'valuation':80000,'average':'10000'}}}
        self.alloc={'remaining':500000,'per_stock':180000}
    def test_bound_identity_expiration_and_schema(self):
        self.assertEqual(manual_buy_request(self.req,now=self.now,fingerprint='a'*64,release='b'*64),self.req)
        for update in ({'quantity':True},{'limit_price':0},{'holding_quantity':0},{'account_ref':'c'*64},{'approved_release':'c'*64},{'id':'bad'},{'expires_at':(self.now+dt.timedelta(minutes=41)).isoformat()},{'trading_day':'2026-11-03'}):
            with self.assertRaises(TradingBlocked):manual_buy_request({**self.req,**update},now=self.now,fingerprint='a'*64,release='b'*64)
        with self.assertRaises(TradingBlocked):manual_buy_request(self.req,now=self.now+dt.timedelta(minutes=20),fingerprint='a'*64,release='b'*64)
    def test_exact_quantity_price_exposure_and_cash(self):
        plan=manual_buy_plan(self.req,self.snap,self.alloc,8000,lambda *a:(100000,100),set())
        self.assertEqual(plan.quantity,5);self.assertEqual(plan.signal,'owner:'+self.req['id']);self.assertEqual(plan.reason,'OWNER_AVERAGING')
        for price,power,allocation,bought in [(8001,(100000,100),self.alloc,set()),(8000,(100000,4),self.alloc,set()),(8000,(40000,100),self.alloc,set()),(8000,(100000,100),{'remaining':40000,'per_stock':180000},set()),(8000,(100000,100),{'remaining':500000,'per_stock':140000},set()),(8000,(100000,100),self.alloc,{'000001'})]:
            with self.assertRaises(TradingBlocked):manual_buy_plan(self.req,self.snap,allocation,price,lambda *a:power,bought)
        self.snap['positions']['000001']['quantity']=11
        with self.assertRaises(TradingBlocked):manual_buy_plan(self.req,self.snap,self.alloc,8000,lambda *a:(100000,100),set())
    def fixtures(self):
        test=self
        class Ledger:
            fingerprint='a'*64
            dashboard_state={}
            def __init__(self):self.records=[];self.stopped=False
            @contextmanager
            def lock(self):yield
            @contextmanager
            def send_guard(self):
                if self.stopped:raise TradingBlocked('EMERGENCY_HALT_ACTIVE')
                yield
            def halted(self):return self.stopped
            def halt(self):self.stopped=True
            def release_authorized(self,r):return r=='b'*64
            def pending(self):return []
            def managed(self):return {'000001':10}
            def bought_today(self,d):return set()
            def rows(self,*a):return self.records
            def reserve_plan(self,p,d):self.records.append({'signal':p.signal});return 'intent'
            def ack(self,*a):pass
            def void_unsent_order(self,ident):self.records[-1].update(status='REJECTED',verified_not_sent=True)
        class Broker(BrokerFixture):
            def snapshot(self):return test.snap.copy()
            def quote(self,*a):return 8000
            def buy_control(self):return {'connected':True,'enabled':True,'revision':1,'risk':{'connected':True,'reserve_pct':40,'per_stock_pct':30,'revision':0},'sell':{'connected':True,'enabled':False,'revision':1},'manual_buys':[test.req],'release':{'approved_release':'b'*64}}
        return Ledger(),Broker()
    def run_request(self,l,b):
        with patch('oneil_v519_live_engine.require_live_release'),patch('oneil_v519_live_engine.release_fingerprint',return_value='b'*64):return process_manual_buy(l,b,[self.req],now=self.now,clock=lambda:self.now)
    def test_ack_is_not_fill_and_duplicate_id_never_resubmits(self):
        l,b=self.fixtures();r=self.run_request(l,b);self.assertEqual(r['status'],'ACKNOWLEDGED');self.assertEqual(len(b.calls),1)
        self.assertEqual(self.run_request(l,b)['status'],'MANUAL_ALREADY_RECORDED');self.assertEqual(len(b.calls),1)
    def test_unknown_wire_halts_and_never_retries(self):
        l,b=self.fixtures();b.failure=TimeoutError('fixture')
        with self.assertRaises(TimeoutError) as ctx:self.run_request(l,b)
        self.assertTrue(l.stopped);self.assertEqual(len(b.calls),1);self.assertIsNone(ctx.exception.execution_progress['orders_sent'])
        with self.assertRaises(TradingBlocked):self.run_request(l,b)
        self.assertEqual(len(b.calls),1)
    def test_halt_release_control_changes_and_exit_priority(self):
        for condition in ('halt','activation','buy_off','risk_change','take_profit','holding_change'):
            l,b=self.fixtures();original=b.buy_control
            if condition=='halt':l.stopped=True
            if condition=='activation':l.release_authorized=lambda r:False
            if condition=='buy_off':b.buy_control=lambda:{**original(),'enabled':False}
            if condition=='risk_change':b.buy_control=lambda:{**original(),'risk':{**original()['risk'],'revision':2}}
            if condition=='take_profit':b.quote=lambda *a:12000
            if condition=='holding_change':self.snap['positions']['000001']['quantity']=11
            with self.assertRaises(TradingBlocked):self.run_request(l,b)
            self.assertEqual(b.calls,[])
            self.snap['positions']['000001']['quantity']=10

class ManualQueueIntegrationTests(ManualAveragingTests):
    def fixtures(self):
        ledger,broker=super().fixtures()
        self.snap['positions']['000001']['average']='8000'
        ledger.rows=lambda query,args: [r for r in ledger.records if r['signal']==args[1]]
        return ledger,broker
    def cycle(self,ledger,broker,execute=True):
        with patch('oneil_v519_live_engine.require_live_release'),patch('oneil_v519_live_engine.release_fingerprint',return_value='b'*64):
            return integrated_cycle(ledger,broker,[{'ticker':'000003','rs':98}],execute=execute,now=self.now,clock=lambda:self.now)
    def queue(self,broker,requests):
        control=broker.buy_control()
        broker.buy_control=lambda:{**control,'manual_buys':requests}
    def second(self):
        return {**self.req,'id':'22222222-2222-4222-8222-222222222222','ticker':'000002'}
    def add_second(self):
        self.snap['positions']['000002']=self.snap['positions']['000001'].copy()
    def test_owner_queue_precedes_automatic_buy_and_stops_after_one_send(self):
        l,b=self.fixtures();self.add_second();self.queue(b,[self.req,self.second()])
        result=self.cycle(l,b)
        self.assertEqual(result['orders_sent'],1)
        self.assertEqual([p.ticker for p in b.calls],['000001'])
        self.assertEqual(b.calls[0].quantity,5)
    def test_blocked_first_request_does_not_starve_second(self):
        for update in ({'limit_price':7999},{'holding_quantity':11},{'buy_revision':0}):
            l,b=self.fixtures();self.add_second();self.queue(b,[{**self.req,**update},self.second()])
            result=self.cycle(l,b)
            self.assertEqual(result['manual_buy_status'],'ACKNOWLEDGED');self.assertNotIn('manual_buy_reason',result)
            self.assertEqual([p.ticker for p in b.calls],['000002'])
            self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['status'],'BLOCKED')
    def test_recorded_first_request_does_not_starve_second(self):
        l,b=self.fixtures();self.add_second();l.records.append({'signal':'owner:'+self.req['id']})
        self.queue(b,[self.req,self.second()]);self.cycle(l,b)
        self.assertEqual([p.ticker for p in b.calls],['000002'])
    def test_account_halt_stops_queue_without_automatic_fallback(self):
        l,b=self.fixtures();l.stopped=True;self.add_second();self.queue(b,[self.req,self.second()])
        result=self.cycle(l,b)
        self.assertEqual(b.calls,[]);self.assertEqual(result['manual_buy_reason'],'MANUAL_ACCOUNT_NOT_READY')
    def test_unknown_first_submission_stops_everything(self):
        l,b=self.fixtures();self.add_second();self.queue(b,[self.req,self.second()]);b.failure=TimeoutError('unknown')
        with self.assertRaises(TimeoutError):self.cycle(l,b)
        self.assertTrue(l.stopped);self.assertEqual(len(b.calls),1)
    def test_all_request_local_blocks_stop_unrelated_automatic_buy(self):
        l,b=self.fixtures();self.queue(b,[{**self.req,'limit_price':7999}])
        result=self.cycle(l,b)
        self.assertEqual(b.calls,[])
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['reason'],'OWNER_LIMIT_PRICE_EXCEEDED')
    def test_unrelated_automatic_quote_failure_does_not_starve_owner(self):
        l,b=self.fixtures();quote=b.quote
        def strict_quote(ticker,side):
            if ticker=='000003':raise TradingBlocked('UNRELATED_QUOTE_UNAVAILABLE')
            return quote(ticker,side)
        b.quote=strict_quote;self.cycle(l,b)
        self.assertEqual([p.ticker for p in b.calls],['000001'])
    def test_exit_switched_off_preserves_owner_queue_priority(self):
        l,b=self.fixtures();l.record_sell_alerts=lambda *args:None
        self.snap['positions']['000001']['average']='10000'
        original=b.buy_control();count=0
        def control():
            nonlocal count
            count+=1
            return {**original,'sell':{'connected':True,'enabled':count==1,'revision':count}}
        b.buy_control=control
        self.cycle(l,b)
        self.assertEqual([p.ticker for p in b.calls],['000001'])
    def test_exit_switched_off_and_blocked_owner_stops_unrelated_auto_buy(self):
        l,b=self.fixtures();l.record_sell_alerts=lambda *args:None
        self.snap['positions']['000001']['average']='10000'
        original=b.buy_control();count=0
        def control():
            nonlocal count
            count+=1
            return {**original,'manual_buys':[{**self.req,'limit_price':7999}],
                'sell':{'connected':True,'enabled':count==1,'revision':count}}
        b.buy_control=control
        self.cycle(l,b)
        self.assertEqual(b.calls,[])
    def test_control_or_approval_change_during_send_guard_blocks_wire(self):
        for change in ('withdraw','buy_off','risk','release','activation'):
            l,b=self.fixtures();control=b.buy_control()
            @contextmanager
            def guard():
                if change=='activation':l.release_authorized=lambda r:False
                else:
                    update={'withdraw':{'manual_buys':[]},'buy_off':{'enabled':False},
                        'risk':{'risk':{**control['risk'],'revision':2}},'release':{'release':{'approved_release':'c'*64}}}[change]
                    b.buy_control=lambda:{**control,**update}
                yield
            l.send_guard=guard
            with self.assertRaises(TradingBlocked):self.run_request(l,b)
            self.assertEqual(b.calls,[]);self.assertTrue(l.stopped)
    def test_expiry_while_waiting_for_send_guard_does_not_submit(self):
        l,b=self.fixtures()
        @contextmanager
        def guard():
            self.now+=dt.timedelta(minutes=20)
            yield
        l.send_guard=guard
        with self.assertRaises(TradingBlocked):self.run_request(l,b)
        self.assertEqual(b.calls,[]);self.assertTrue(l.stopped)
    def test_observe_never_consumes_queue(self):
        l,b=self.fixtures();self.cycle(l,b,execute=False)
        self.assertEqual(b.calls,[]);self.assertEqual(l.records,[])
    def test_extended_new_request_survives_one_schedule_but_old_request_expires(self):
        new={**self.req,'expires_at':(self.now+dt.timedelta(minutes=40)).isoformat()}
        self.assertEqual(manual_buy_request(new,now=self.now+dt.timedelta(minutes=21),fingerprint='a'*64,release='b'*64),new)
        for request,when in ((self.req,self.now+dt.timedelta(minutes=20)),(new,self.now+dt.timedelta(minutes=40))):
            with self.assertRaises(TradingBlocked):manual_buy_request(request,now=when,fingerprint='a'*64,release='b'*64)


class PureRsSelectionTests(unittest.TestCase):
 def test_rs_boundary_sort_and_no_top_ten_truncation(self):
  import csv
  now=dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul'))
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'c.csv'
   with p.open('w',newline='') as f:
    w=csv.writer(f);w.writerow(['date','ticker','rs'])
    for i in range(20):w.writerow(['2026-10-29',f'{i+1:06}',90+i%10])
    w.writerow(['2026-10-29','999999',89])
   found=scan_candidates(p,now)
   self.assertEqual(len(found),20)
   self.assertEqual([(r['rs'],r['ticker']) for r in found],sorted([(r['rs'],r['ticker']) for r in found],key=lambda r:(-r[0],r[1])))
   for value in ('NaN','inf','100','0'):
    p.write_text('date,ticker,rs\n2026-10-29,000001,'+value+'\n')
    with self.assertRaises(TradingBlocked):scan_candidates(p,now)
 def test_builder_uses_only_real_close_rs_and_keeps_all_qualified(self):
  import pandas as pd,numpy as np
  from build_v4b_candidates import generate
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);p=root/'p.csv';out=root/'c.csv';diag=root/'d.json'
   days=pd.bdate_range(end='2026-10-28',periods=253)
   frames=[pd.DataFrame({'ticker':f'{i+1:06}','name':str(i),'date':days,'close':100*np.exp(i*.0001*np.arange(253)),'volume':([100]*252+[0] if i>=18 else [100]*253)}) for i in range(20)]
   pd.concat(frames).to_csv(p,index=False)
   with patch('oneil_live_v5_9.technical_timing',side_effect=AssertionError('must not compute indicator')):
    rows=generate(p,root/'absent.csv','2026-10-29',out,diag)
   self.assertEqual([r['ticker'] for r in rows],['000018','000019','000020']) # ceil percentile includes RS 90 boundary
   found=scan_candidates(out,dt.datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Seoul')))
   self.assertEqual([r['ticker'] for r in found],['000020','000019','000018'])
   self.assertEqual(json.loads(diag.read_text())['selection_policy'],'RS_ONLY_V1')


class ImmediateApprovalTests(unittest.TestCase):
    def test_dispatch_is_bound_to_exact_saved_approval(self):
        review=dict(schema_version=1,operator='asura2367-boop',approved_release=release_fingerprint(),
                    source_commit='a'*40,review_run_id=123,revision=10,approved_at='2026-11-04T00:00:00+00:00')
        flags={'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main',
               'GITHUB_REPOSITORY':'asura2367-boop/oneil-rs-scanner','OPERATION':'apply-approval',
               'REQUESTED_RELEASE':review['approved_release'],'REQUESTED_APPROVAL_REVISION':'10'}
        with patch.dict(os.environ,flags):
            self.assertEqual(dashboard_release_env({'release':review}),review)
            for changes in ({'REQUESTED_RELEASE':'b'*64},{'REQUESTED_APPROVAL_REVISION':'9'},
                            {'GITHUB_REF':'refs/heads/other'},{'OPERATION':'activate'}):
                with patch.dict(os.environ,changes):self.assertIsNone(dashboard_release_env({'release':review}))

    def test_owner_apply_cannot_request_execution_or_fall_back_to_legacy_activation(self):
        import sys,io
        with tempfile.TemporaryDirectory() as directory:
            for flags in (['--execute'],[]):
                out=str(pathlib.Path(directory)/'result.json')
                with patch.object(sys,'argv',['engine','--apply-owner-approval','--output',out]+flags),patch('oneil_v519_live_engine.dashboard_release_env',return_value=None),patch('sys.stdout',new_callable=io.StringIO):
                    with self.assertRaises(SystemExit):runtime_main()
                result=__import__('json').loads(pathlib.Path(out).read_text())
                self.assertIn(result['reason'],['ACTIVATION_CANNOT_SUBMIT','OWNER_RELEASE_REVIEW_CHANGED'])
                self.assertEqual(result['orders_sent'],0)

    def test_halt_resume_cli_cannot_be_used_for_any_other_mode(self):
        import sys,io
        for mode in ('--run','--activate','--halt','--recover','--release-info','--dashboard-release-env','--close'):
            with tempfile.TemporaryDirectory() as directory:
                out=str(pathlib.Path(directory)/'result.json')
                with patch.object(sys,'argv',['engine',mode,'--resume-halt','--expected-halt-revision','a'*64,'--output',out]),patch('v519_pg_ledger.connect') as connect,patch('sys.stdout',new_callable=io.StringIO):
                    with self.assertRaises(SystemExit):runtime_main()
                self.assertEqual(json.loads(pathlib.Path(out).read_text())['reason'],'HALT_RESUME_REQUEST_INVALID')
                connect.assert_not_called()

    def test_halt_revision_is_opaque_stable_and_state_bound(self):
        from v519_pg_ledger import halt_revision
        stamp=dt.datetime(2026,11,5,0,0,tzinfo=dt.timezone.utc)
        token=halt_revision({'halted':True,'updated_at':stamp})
        self.assertRegex(token,'^[0-9a-f]{64}$')
        self.assertEqual(token,halt_revision({'halted':True,'updated_at':stamp.astimezone(ZoneInfo('Asia/Seoul'))}))
        self.assertNotEqual(token,halt_revision({'halted':False,'updated_at':stamp}))
        self.assertNotEqual(token,halt_revision({'halted':True,'updated_at':stamp+dt.timedelta(microseconds=1)}))

    def test_postcommit_failure_context_cannot_imply_activation_rollback(self):
        expected={'activation_committed':True,'halt_resume_committed':True}
        self.assertEqual(run_failure_context({**expected,'account_snapshot':'PRIVATE'}),expected)
        self.assertEqual(run_failure_context({'activation_committed':'PRIVATE'}),{})

    def test_workflow_apply_path_only_activates_with_bound_review(self):
        workflow=(pathlib.Path(__file__).resolve().parents[1]/'.github/workflows/oneil_v519_live_engine_review.yml').read_text()
        body=workflow.split('            apply-approval)')[1].split('            activate)')[0]
        self.assertIn('--apply-owner-approval --acknowledge-live-risk',body)
        self.assertNotIn('--execute',body)
        self.assertNotIn('--activate ',body)
        self.assertIn('REQUESTED_RELEASE: ${{ inputs.approved_release }}',workflow)
        self.assertIn('REQUESTED_APPROVAL_REVISION: ${{ inputs.approval_revision }}',workflow)


class IncompleteRuntimeStatusTests(unittest.TestCase):
    def test_cancelled_runtime_cannot_publish_migration_as_healthy(self):
        import json,subprocess
        workflow=(pathlib.Path(__file__).resolve().parents[1]/'.github/workflows/oneil_v519_live_engine_review.yml').read_text()
        step=workflow.split('      - name: Publish encrypted private-dashboard snapshot')[1].split('      - name: Save sanitized result')[0]
        script='\n'.join(line[12:] for line in step.split('          script: |\n')[1].splitlines())
        js="""
        const assert=require('node:assert/strict');
        const script=SCRIPT;
        const execute=new (Object.getPrototypeOf(async function(){}).constructor)('github','context','process','require',script);
        (async()=>{
          for(const outcome of ['cancelled','failure','']){
            const sent=[];
            const github={rest:{repos:{getContent:async()=>({data:{sha:'a'}}),createOrUpdateFileContents:async p=>sent.push(p)}}};
            const fs={existsSync:p=>p==='v5_19_report.json',readFileSync:()=>JSON.stringify({status:'MIGRATED_HALTED_BY_DEFAULT',orders_sent:0})};
            await execute(github,{repo:{owner:'fixture',repo:'fixture'},runId:1,eventName:'push'},{env:{OPERATION:'observe',ENGINE_OUTCOME:outcome}},n=>{assert.equal(n,'fs');return fs;});
            const status=JSON.parse(Buffer.from(sent.find(p=>p.path.endsWith('dashboard_status.json')).content,'base64'));
            assert.equal(status.conclusion,'failure');assert.equal(status.status,'RUNTIME_INCOMPLETE');
          }
        })().catch(e=>{console.error(e);process.exitCode=1});
        """.replace('SCRIPT',json.dumps(script))
        subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)
        runtime=workflow.split('      - name: Run existing integrated engine')[1].split('      - name: Publish encrypted')[0]
        self.assertIn('rm -f data/live/dashboard.enc.json v5_19_report.json',runtime)
        self.assertIn('ENGINE_OUTCOME: ${{ steps.engine_runtime.outcome }}',step)
        self.assertIn("'orders_sent':None,'order_outcome':'CHECK_LEDGER'",workflow)


class ExhaustedPlanningBudgetTests(unittest.TestCase):
    def test_zero_cap_never_requests_candidate_quotes_or_buying_power(self):
        from unittest.mock import Mock
        snap={'cash':0,'invested':0,'pending':False,'positions':{},'managed':{}}
        quote=Mock(side_effect=AssertionError('unnecessary quote'))
        power=Mock(side_effect=AssertionError('unnecessary buying power'))
        plans,_=plan_orders([{'ticker':str(i).zfill(6),'rs':99} for i in range(1,250)],snap,quote,power)
        self.assertEqual(plans,[]);quote.assert_not_called();power.assert_not_called()


class EfficientReadPlanningTests(unittest.TestCase):
    def test_unaffordable_share_skips_power_but_keeps_cheaper_candidate(self):
        snapshot=dict(cash=100000,invested=0,pending=set(),positions={},managed={})
        calls=[]
        plans,_=plan_orders([{'ticker':'000001','rs':99},{'ticker':'000002','rs':98}],snapshot,
            lambda ticker,side:20000 if ticker=='000001' else 1000,
            lambda ticker,price:(calls.append(ticker) or (100000,100)),max_buys=1)
        self.assertEqual(calls,['000002'])
        self.assertEqual([(p.ticker,p.quantity) for p in plans],[('000002',17)])
    def test_stage_exception_is_not_suppressed_or_printed(self):
        import io,contextlib,json
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream),self.assertRaisesRegex(ValueError,'private payload'):
            with read_stage('UNFILLED_ORDERS'):
                raise ValueError('private payload')
        events=[json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([e['event'] for e in events],['READ_STAGE_STARTED','READ_STAGE_FINISHED'])
        self.assertNotIn('private payload',stream.getvalue())


class SubmissionDiagnosticsTests(unittest.TestCase):
    def test_original_transport_metadata_is_safe_and_does_not_grant_replay(self):
        from urllib.error import HTTPError
        original=HTTPError('https://private.test/?token=SECRET',503,'account SECRET',{'authorization':'SECRET'},None)
        original.broker_api='CASH_ORDER';original.broker_attempts=1
        blocked=TradingBlocked('SUBMISSION_UNCERTAIN_NO_RETRY')
        blocked._submission_exception=original;blocked.submission_stage='SUBMIT'
        result=failure_details(blocked)
        self.assertEqual(str(blocked),'SUBMISSION_UNCERTAIN_NO_RETRY')
        self.assertEqual(result['submission_stage'],'SUBMIT')
        self.assertEqual(result['submission_failure']['http_status'],503)
        self.assertEqual(result['submission_failure']['broker_api'],'CASH_ORDER')
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertNotIn('orders_sent',result)
    def test_pre_wire_code_is_allowlisted_without_claiming_not_sent(self):
        blocked=TradingBlocked('SUBMISSION_UNCERTAIN_NO_RETRY')
        blocked._submission_exception=TradingBlocked('FRESH_EXECUTABLE_BOOK_REQUIRED')
        result=failure_details(blocked)
        self.assertEqual(result['submission_failure']['failure_code'],'FRESH_EXECUTABLE_BOOK_REQUIRED')
        self.assertNotIn('verified_not_sent',result)
        blocked._submission_exception=ValueError('account SECRET');blocked.submission_stage='SECRET'
        self.assertNotIn('SECRET',json.dumps(failure_details(blocked)))
    def test_cyclic_diagnostic_exceptions_do_not_recurse(self):
        a=TradingBlocked('a');b=ValueError('b');a._submission_exception=b;b._submission_exception=a
        self.assertNotIn('submission_failure',failure_details(a)['submission_failure'])

class BlockedDashboardPublicationTests(unittest.TestCase):
    def test_unresolved_orders_keep_safety_result_without_creating_fresh_account_data(self):
        from types import SimpleNamespace
        result={'status':'BLOCKED_UNRESOLVED_ORDERS','gate':'DENY','orders_sent':0,'recovery':{'unresolved':1}}
        with patch('oneil_v519_live_engine.dashboard_snapshot') as read,patch('oneil_v519_live_engine.encrypt_dashboard') as write:
            self.assertFalse(publish_dashboard_if_collected(SimpleNamespace(),result,'unused-key','unused-path'))
            read.assert_not_called();write.assert_not_called()
        self.assertEqual(result['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(result['recovery']['unresolved'],1)
    def test_unexpected_missing_account_data_still_fails_closed(self):
        from types import SimpleNamespace
        with patch('oneil_v519_live_engine.encrypt_dashboard') as write:
            with self.assertRaisesRegex(TradingBlocked,'DASHBOARD_ACCOUNT_SNAPSHOT_MISSING'):
                publish_dashboard_if_collected(SimpleNamespace(),{'status':'OBSERVED'},'unused-key','unused-path')
            write.assert_not_called()

class ManagedExitBatchTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,11,4,10,tzinfo=ZoneInfo('Asia/Seoul'))
        self.broker=BrokerFixture();self.candidates=[]
        self.broker.positions={t:{'quantity':10,'sellable':6,'average':'12000','cost':120000,'valuation':100000} for t in ('000001','000002')}
        class Ledger:
            fingerprint='b'*64
            def __init__(self):self.records={};self.owned={'000001':8,'000002':8};self.stopped=False
            @contextmanager
            def lock(self):yield
            @contextmanager
            def send_guard(self):
                if self.stopped:raise TradingBlocked('EMERGENCY_HALT_ACTIVE')
                yield
            def halted(self):return self.stopped
            def halt(self):self.stopped=True
            def release_authorized(self,r):return True
            def managed(self):return self.owned.copy()
            def bought_today(self,d):return set()
            def pending(self):return [r for r in self.records.values() if r['status'] not in ('FILLED','CANCELLED','REJECTED')]
            def reserve_plan(self,p,d):
                if self.pending():raise TradingBlocked('UNRESOLVED_ORDERS')
                ident=str(len(self.records)+1)
                self.records[ident]={'intent_id':ident,'ticker':p.ticker,'side':p.side,'quantity':p.quantity,'trading_day':d,'status':'SUBMISSION_UNKNOWN','broker_order_id':None,'filled_quantity':0}
                return ident
            def ack(self,i,n):self.records[i].update(status='ACKNOWLEDGED',broker_order_id=n)
            def reconcile(self,r,state,filled):
                self.owned[r['ticker']]-=filled-r['filled_quantity']
                r.update(status=state,filled_quantity=filled)
            def rows(self,sql,args):return [self.records[args[1]]] if args[1] in self.records else []
            def record_sell_alerts(self,*args):pass
            def void_unsent_sell(self,i):self.records[i]['status']='REJECTED'
            def void_unsent_order(self,i):self.records[i].update(status='REJECTED',verified_not_sent=True)
        self.ledger=Ledger()
    def cycle(self):
        return integrated_cycle(self.ledger,self.broker,self.candidates,execute=True,now=self.now,clock=lambda:self.now)
    def fill_exits(self,*,fill=None,balance=True,cancel=False,after=None):
        original=self.broker.submit
        def submit(plan):
            response=original(plan);qty=plan.quantity if fill is None else fill
            self.broker.rows.setdefault(self.now.strftime('%Y%m%d'),[]).append(dict(odno=response['output']['ODNO'],ord_dt=self.now.strftime('%Y%m%d'),pdno=plan.ticker,sll_buy_dvsn_cd='01',ord_qty=str(plan.quantity),tot_ccld_qty=str(qty),rmn_qty=str(0 if cancel else plan.quantity-qty),cncl_yn='Y' if cancel else 'N'))
            if balance:
                pos=self.broker.positions[plan.ticker];pos['quantity']-=qty;pos['sellable']=min(pos['sellable'],pos['quantity'])
            if after:after(plan)
            return response
        self.broker.submit=submit
    def test_two_distinct_exits_with_no_buy_candidates_and_residual_not_retried(self):
        self.fill_exits();result=self.cycle()
        self.assertEqual([(p.ticker,p.quantity) for p in self.broker.calls],[('000001',6),('000002',6)])
        self.assertEqual(result['orders_sent'],2);self.assertEqual(result['batch_stop_reason'],'EXIT_BATCH_COMPLETED')
        self.assertEqual(self.ledger.managed(),{'000001':2,'000002':2})
    def test_filled_exits_never_spend_proceeds_on_buys(self):
        self.candidates=[{'ticker':'000003','rs':99}];self.fill_exits();self.cycle()
        self.assertEqual([p.side for p in self.broker.calls],['SELL','SELL'])
    def test_partial_pending_cancelled_or_stale_balance_stops_second(self):
        for kwargs,reason in (({'fill':2},'PENDING_FILL_CONFIRMATION'),({'fill':0},'PENDING_FILL_CONFIRMATION'),({'fill':2,'cancel':True},'EXIT_FILL_NOT_CONFIRMED'),({'balance':False},'BALANCE_UPDATE_PENDING')):
            self.setUp();self.fill_exits(**kwargs);result=self.cycle()
            self.assertEqual(len(self.broker.calls),1);self.assertEqual(result['batch_stop_reason'],reason)
    def test_unknown_send_halts_and_does_not_try_later_exit(self):
        self.broker.failure=TimeoutError('fixture')
        with self.assertRaises(TradingBlocked) as caught:self.cycle()
        self.assertTrue(self.ledger.stopped);self.assertEqual(len(self.broker.calls),1)
        self.assertIsNone(caught.exception.execution_progress['orders_sent'])
    def test_second_order_unknown_retains_first_confirmed_count(self):
        self.fill_exits();original=self.broker.submit
        def submit(p):
            if self.broker.calls:self.broker.failure=TimeoutError('fixture')
            return original(p)
        self.broker.submit=submit
        with self.assertRaises(TradingBlocked) as caught:self.cycle()
        self.assertEqual(caught.exception.execution_progress['confirmed_orders_sent'],1)
        self.assertIsNone(caught.exception.execution_progress['orders_sent']);self.assertTrue(self.ledger.stopped)
    def test_next_exit_uses_refreshed_sellable_holdings_and_quote(self):
        def after(p):
            if p.ticker=='000001':
                self.broker.positions['000002']['quantity']=3;self.broker.positions['000002']['sellable']=2
                self.broker.prices[('000002','SELL')]=9000
        self.fill_exits(after=after);self.cycle()
        self.assertEqual((self.broker.calls[1].quantity,self.broker.calls[1].price),(2,9000))
    def test_halt_session_close_or_unknown_control_stops_remaining(self):
        for change in ('halt','close','control'):
            self.setUp();control=self.broker.buy_control()
            def after(p):
                if change=='halt':self.ledger.halt()
                if change=='close':self.now=self.now.replace(hour=15,minute=20)
                if change=='control':self.broker.buy_control=lambda:{**control,'sell':None}
            self.fill_exits(after=after)
            if change in ('halt','close'):
                with self.assertRaises(TradingBlocked):self.cycle()
            else:self.assertEqual(self.cycle()['batch_stop_reason'],'SELL_CONTROL_UNAVAILABLE')
            self.assertEqual(len(self.broker.calls),1)
    def test_sell_off_after_first_fill_never_falls_back_to_buy(self):
        self.candidates=[{'ticker':'000003','rs':99}];control=self.broker.buy_control()
        def after(p):self.broker.buy_control=lambda:{**control,'sell':{'enabled':False,'connected':True,'revision':2}}
        self.fill_exits(after=after);result=self.cycle()
        self.assertEqual([p.side for p in self.broker.calls],['SELL'])
        self.assertEqual(result['batch_stop_reason'],'EXIT_BATCH_NO_MORE_ELIGIBLE')
    def test_second_pre_send_cancel_preserves_first_acknowledgement(self):
        self.fill_exits();control=self.broker.buy_control()
        @contextmanager
        def guard():
            if len(self.ledger.records)==2:self.broker.buy_control=lambda:{**control,'sell':{'connected':True,'enabled':False,'revision':2}}
            yield
        self.ledger.send_guard=guard;result=self.cycle()
        self.assertEqual(len(self.broker.calls),1);self.assertEqual(result['orders_sent'],1)
        self.assertEqual(result['status'],'ACKNOWLEDGED');self.assertEqual(result['batch_stop_reason'],'SELL_CONTROL_OFF_OR_UNAVAILABLE')
        self.assertEqual(self.ledger.records['2']['status'],'REJECTED');self.assertFalse(self.ledger.stopped)
    def test_control_replan_before_first_send_cannot_expand_initial_exit_scope(self):
        self.broker.positions['000002']['average']='10000'
        original=self.broker.buy_control();count=0
        def control():
            nonlocal count
            count+=1
            if count>1:self.broker.prices[('000002','SELL')]=12000
            return {**original,'sell':{'connected':True,'enabled':count==1,'revision':count}}
        self.broker.buy_control=control;self.fill_exits();self.cycle()
        self.assertEqual(self.broker.calls,[])
    def test_new_exit_not_in_initial_set_waits_until_next_cycle(self):
        self.broker.positions['000002']['average']='10000'
        self.fill_exits(after=lambda p:self.broker.positions['000002'].update(average='12000'))
        self.cycle();self.assertEqual([p.ticker for p in self.broker.calls],['000001'])
    def test_unmanaged_positions_are_never_added_to_exit_scope(self):
        self.ledger.owned.pop('000002');self.fill_exits();self.cycle()
        self.assertEqual([p.ticker for p in self.broker.calls],['000001'])

class CloseRuntimeRegressionTests(unittest.TestCase):
    def run_close(self, observation, *, collected=False, scanner_failure=False, publication_failure=False, storage_failure=False, manual_checks=None, cycle_failure=False):
        import copy, io, sys
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):
                return cls(2026,11,4,20,51,tzinfo=ZoneInfo('Asia/Seoul'))
        ledger=MagicMock()
        if storage_failure:ledger.finish_run.side_effect=OSError('PRIVATE_STORAGE_FIXTURE')
        ledger.dashboard_state={'snapshot':{'cash':100,'equity':100,'valuation':0}} if collected else None
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            out=pathlib.Path(directory)/'result.json'
            stack.enter_context(patch.object(sys,'argv',['engine','--close','--output',str(out),
                '--dashboard-public-key','fixture-key','--dashboard-output',str(pathlib.Path(directory)/'encrypted.json')]))
            stack.enter_context(patch.dict(os.environ,{'SUPABASE_DB_URL':'','LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}))
            stack.enter_context(patch('oneil_v519_live_engine.dt.datetime',Clock))
            stack.enter_context(patch('v519_pg_ledger.connect'))
            stack.enter_context(patch('v519_pg_ledger.PgRuntimeLedger',return_value=ledger))
            credentials=stack.enter_context(patch('kis_buying_power_readonly.credentials',return_value=('key','secret','account','01')))
            broker=stack.enter_context(patch('oneil_v519_live_engine.KisBroker'))
            def run_observation(*args,**kwargs):
                if manual_checks is not None:
                    ledger.manual_check_checkpoint(manual_checks)
                if cycle_failure:raise TimeoutError('PRIVATE_AFTER_CHECKS')
                return copy.deepcopy(observation)
            cycle=stack.enter_context(patch('oneil_v519_live_engine.integrated_cycle',side_effect=run_observation))
            stack.enter_context(patch('oneil_v519_live_engine.scan_candidates',side_effect=ValueError('fixture') if scanner_failure else None,return_value=[]))
            wire=stack.enter_context(patch('urllib.request.urlopen',side_effect=AssertionError('no network allowed')))
            encrypt=stack.enter_context(patch('oneil_v519_live_engine.encrypt_dashboard',side_effect=OSError('PRIVATE_FIXTURE') if publication_failure else None))
            if collected:
                stack.enter_context(patch('oneil_v519_live_engine.dashboard_snapshot',return_value={'fresh_fixture':True}))
            stack.enter_context(patch('sys.stdout',new_callable=io.StringIO))
            with self.assertRaises(SystemExit) as stopped:runtime_main()
            result=json.loads(out.read_text())
            credentials.assert_called_once_with(read_only=True)
            broker.assert_called_once_with(execution=False,operator_activation=False)
            self.assertFalse(cycle.call_args.kwargs['execute'])
            broker.return_value.submit.assert_not_called();wire.assert_not_called()
            ledger.halt.assert_not_called();ledger.activate_release.assert_not_called()
            ledger.reserve_plan.assert_not_called()
            self.assertEqual(result['orders_sent'],0)
            return result,ledger,encrypt,stopped.exception.code

    def observed(self,status='OBSERVED'):
        return {'status':status,'gate':'DENY','orders_sent':0,'order_attempts':0,
                'recovery':{'matched':0,'unresolved':0}}

    def test_private_manual_checkpoint_is_wired_before_cycle_and_not_public(self):
        checks=[{'id':'private-id','ticker':'234690','status':'BLOCKED','reason':'MANUAL_BUY_LIMIT_EXCEEDED'}]
        for cycle_failure,publication_failure in ((False,False),(True,False),(False,True)):
            result,ledger,_,_=self.run_close(self.observed(),collected=True,manual_checks=checks,
                cycle_failure=cycle_failure,publication_failure=publication_failure)
            ledger.save_manual_buy_checks.assert_called_once_with(ledger.start_run.call_args.args[0],checks)
            self.assertNotIn('manual_buy_checks',result)
            self.assertNotIn('private-id',json.dumps(result));self.assertNotIn('PRIVATE_AFTER_CHECKS',json.dumps(result))

    def test_close_preserves_unresolved_status_and_skips_missing_snapshot(self):
        observation=self.observed('BLOCKED_UNRESOLVED_ORDERS')
        observation['recovery']['unresolved']=1
        result,ledger,encrypt,code=self.run_close(observation)
        self.assertEqual(result['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(result['recovery'],observation['recovery'])
        self.assertTrue(result['session_closed'])
        self.assertEqual(result['dashboard_snapshot_status'],'NOT_COLLECTED_UNRESOLVED_ORDERS')
        self.assertNotIn('reason',result);encrypt.assert_not_called()
        stored=ledger.finish_run.call_args.args[1]
        self.assertEqual(stored['status'],'BLOCKED_UNRESOLVED_ORDERS')
        self.assertEqual(stored['recovery'],observation['recovery'])
        self.assertTrue(stored['session_closed'])
        self.assertEqual(code,0)  # existing observer status contract; public status remains blocked

    def test_successful_close_publishes_collected_snapshot(self):
        for status in ('OBSERVED','SELL_ALERT_ONLY'):
            with self.subTest(status=status):
                result,ledger,encrypt,code=self.run_close(self.observed(status),collected=True)
                self.assertEqual(result['status'],'DAILY_CLOSED');self.assertTrue(result['session_closed'])
                self.assertEqual(code,0);encrypt.assert_called_once()
                self.assertEqual(ledger.finish_run.call_args.args[1]['account_snapshot']['cash'],100)

    def test_close_preserves_unavailable_sell_control_status(self):
        result,_,_,_=self.run_close(self.observed('SELL_CONTROL_UNAVAILABLE'),collected=True)
        self.assertEqual(result['status'],'SELL_CONTROL_UNAVAILABLE');self.assertTrue(result['session_closed'])

    def test_close_does_not_mask_failed_scanner(self):
        result,_,_,code=self.run_close(self.observed(),collected=True,scanner_failure=True)
        self.assertEqual(result['status'],'OBSERVED');self.assertTrue(result['session_closed'])
        self.assertEqual(result['scanner_status'],'BLOCKED_INVALID_OR_STALE_SOURCE');self.assertEqual(code,2)

    def test_unexpected_missing_snapshot_still_fails_and_keeps_recovery(self):
        result,ledger,encrypt,code=self.run_close(self.observed())
        self.assertEqual(result['reason'],'DASHBOARD_ACCOUNT_SNAPSHOT_MISSING')
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(code,2)
        self.assertEqual(result['recovery'],self.observed()['recovery'])
        self.assertTrue(result['session_closed']);encrypt.assert_not_called()
        self.assertEqual(ledger.finish_run.call_args.args[1]['recovery'],result['recovery'])

    def test_publication_error_preserves_only_safe_context(self):
        result,ledger,_,code=self.run_close(self.observed(),collected=True,publication_failure=True)
        self.assertEqual(code,2);self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['recovery'],self.observed()['recovery']);self.assertTrue(result['session_closed'])
        stored=ledger.finish_run.call_args.args[1]
        self.assertEqual(stored['recovery'],result['recovery']);self.assertTrue(stored['session_closed'])
        self.assertNotIn('PRIVATE_FIXTURE',json.dumps(result));self.assertNotIn('account_snapshot',result)
        self.assertEqual(run_failure_context({'account_snapshot':'PRIVATE','orders_sent':8,'recovery':{'unresolved':1}}),{'recovery':{'unresolved':1}})

    def test_session_marker_closes_monitoring_without_changing_safety_status(self):
        from types import SimpleNamespace
        state={'at':dt.datetime(2026,11,4,10,tzinfo=ZoneInfo('Asia/Seoul')),
            'snapshot':{'positions':{},'managed':{},'cash':100,'equity':100,'valuation':0},
            'buy_control':{},'allocation':{},'candidates':[],'plans':[]}
        ledger=SimpleNamespace(fingerprint='b'*64,dashboard_state=state,rows=lambda *args:[],
            halted=lambda:True,halt_state=lambda:{'halted':True,'halt_revision':'a'*64},
            release_authorized=lambda _:False,sell_alerts=lambda:[])
        result={'status':'SELL_CONTROL_UNAVAILABLE','session_closed':True}
        snapshot=dashboard_snapshot(ledger,result)
        self.assertEqual(snapshot['poll_minutes'],5)
        self.assertTrue(snapshot['manual_immediate_supported'])
        self.assertFalse(snapshot['monitoring_open'])
        self.assertEqual(snapshot['result']['status'],'SELL_CONTROL_UNAVAILABLE')
        self.assertTrue(snapshot['halted']);self.assertFalse(snapshot['orders_enabled'])
        self.assertTrue(dashboard_snapshot(ledger,{'status':'OBSERVED'})['monitoring_open'])
        self.assertFalse(dashboard_snapshot(ledger,{'status':'DAILY_CLOSED'})['monitoring_open'])

    def test_storage_error_preserves_safe_recovery_context(self):
        observation=self.observed('BLOCKED_UNRESOLVED_ORDERS')
        observation['recovery']['unresolved']=1
        result,ledger,encrypt,code=self.run_close(observation,storage_failure=True)
        self.assertEqual(code,2);self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['recovery'],observation['recovery']);self.assertTrue(result['session_closed'])
        self.assertNotIn('PRIVATE_STORAGE_FIXTURE',json.dumps(result));encrypt.assert_not_called()
        self.assertEqual(ledger.finish_run.call_count,2)

class PreTransportProofTests(unittest.TestCase):
    def setUp(self):
        clock=patch('oneil_v519_live_engine.dt.datetime',RegularSessionClock)
        clock.start();self.addCleanup(clock.stop)
    def broker(self, stale=True):
        broker=object.__new__(KisBroker);broker.execution=True
        broker.account='12345678';broker.product='01'
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        broker.quote_evidence={('144960','BUY'):{'price':10000,'size':100,'at':now-dt.timedelta(seconds=10 if stale else 0),'venue':'KRX',
            'session':execution_session(now),'order_type':limit_order_code(execution_session(now))}}
        return broker
    def test_real_adapter_stale_and_oversized_never_enter_transport(self):
        from unittest.mock import Mock
        for stale,qty in ((True,1),(False,101)):
            broker=self.broker(stale);broker.request=Mock()
            with patch('oneil_v519_live_engine.require_live_release'),self.assertRaises(OrderNotSent):
                broker.submit(OrderPlan('144960','BUY',qty,10000,'rs','TEST'))
            broker.request.assert_not_called()
    def test_second_guard_and_transport_marker_are_distinguished(self):
        from unittest.mock import Mock
        broker=self.broker();broker.request=Mock()
        with patch('oneil_v519_live_engine.require_live_release'),self.assertRaises(OrderNotSent):
            broker._send_verified(OrderPlan('144960','BUY',1,10000,'rs','TEST'))
        broker.request.assert_not_called()
        for error in (TimeoutError('lost reply'),OrderNotSent('FRESH_EXECUTABLE_BOOK_REQUIRED')):
            broker=self.broker(False);broker.request=Mock(side_effect=error)
            with patch('oneil_v519_live_engine.require_live_release'),self.assertRaises(Exception) as caught:
                broker.submit(OrderPlan('144960','BUY',1,10000,'rs','TEST'))
            self.assertNotIsInstance(caught.exception,OrderNotSent)
            self.assertEqual(broker.request.call_count,1)

class ManualPreTransportTests(ManualQueueIntegrationTests):
    def test_known_unsent_manual_preserves_key_and_stops_without_halt(self):
        l,b=self.fixtures();self.queue(b,[self.req]);b.submit=lambda p:(_ for _ in ()).throw(OrderNotSent('FRESH_EXECUTABLE_BOOK_REQUIRED'))
        result=self.cycle(l,b)
        self.assertFalse(l.stopped);self.assertEqual(result['orders_sent'],0)
        self.assertEqual(result['manual_buy_reason'],'FRESH_EXECUTABLE_BOOK_REQUIRED')
        self.assertEqual(l.records[0]['status'],'REJECTED');self.assertTrue(l.records[0]['verified_not_sent'])
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['status'],'MANUAL_PRE_SUBMIT_REJECTED')
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['reason'],'FRESH_EXECUTABLE_BOOK_REQUIRED')
    def test_failed_unsent_persistence_halts_and_keeps_attempt_diagnostic(self):
        l,b=self.fixtures();b.submit=lambda p:(_ for _ in ()).throw(OrderNotSent('BOOK_SIZE_EXCEEDED'))
        l.void_unsent_order=lambda i:(_ for _ in ()).throw(RuntimeError('db failure'))
        with self.assertRaises(RuntimeError) as caught:self.cycle(l,b)
        self.assertTrue(l.stopped);self.assertEqual(caught.exception.execution_progress['order_attempts'],1)
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['status'],'SUBMISSION_CHECK_FAILED')
    def test_recorded_queue_diagnostics_survive_fallback_failure(self):
        l,b=self.fixtures();l.records.append({'signal':'owner:'+self.req['id']})
        from oneil_v519_live_engine import _automatic_cycle
        def automatic(*args,**kwargs):
            if kwargs.get('defer_for_manual') is False:
                l.dashboard_state={}
                raise TradingBlocked('FALLBACK_READ_FAILED')
            return _automatic_cycle(*args,**kwargs)
        with patch('oneil_v519_live_engine._automatic_cycle',side_effect=automatic),self.assertRaises(TradingBlocked):self.cycle(l,b)
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['status'],'MANUAL_ALREADY_RECORDED')
    def test_timeout_retains_manual_check_and_unknown_progress(self):
        l,b=self.fixtures();b.failure=TimeoutError('unknown')
        with self.assertRaises(TimeoutError) as caught:self.cycle(l,b)
        self.assertIsNone(caught.exception.execution_progress['orders_sent']);self.assertTrue(l.stopped)
        self.assertEqual(l.dashboard_state['manual_buy_checks'][0]['status'],'SUBMISSION_CHECK_FAILED')

class ExitPreTransportTests(ManagedExitBatchTests):
    def test_second_exit_provably_unsent_retains_first_ack(self):
        self.fill_exits();original=self.broker.submit
        def submit(plan):
            if self.broker.calls:raise OrderNotSent('EXECUTABLE_BOOK_REQUIRED')
            return original(plan)
        self.broker.submit=submit
        result=self.cycle()
        self.assertEqual(result['orders_sent'],1);self.assertEqual(result['status'],'ACKNOWLEDGED')
        self.assertEqual(result['order_attempts'],2);self.assertFalse(self.ledger.stopped)
        self.assertEqual(self.ledger.records['2']['status'],'REJECTED')

class ManualDiagnosticTests(ManualQueueIntegrationTests):
    def test_limit_reasons_are_distinct_without_changing_policy(self):
        cases=[({'remaining':40000,'per_stock':180000},(100000,100),'TOTAL_ALLOCATION_LIMIT'),
               ({'remaining':500000,'per_stock':140000},(100000,100),'PER_STOCK_EXPOSURE_LIMIT'),
               (self.alloc,(40000,100),'BROKER_BUYING_POWER_LIMIT'),
               (self.alloc,(100000,4),'BROKER_QUANTITY_LIMIT')]
        for allocation,power,reason in cases:
            with self.assertRaises(TradingBlocked) as caught:
                manual_buy_plan(self.req,self.snap,allocation,8000,lambda *args:power,set())
            self.assertEqual(str(caught.exception),'MANUAL_BUY_LIMIT_EXCEEDED')
            self.assertEqual(caught.exception.manual_limit_reasons,[reason])

    def test_checkpoint_precedes_next_request_and_keeps_first_when_second_fails(self):
        import copy
        l,b=self.fixtures();self.add_second();self.queue(b,[{**self.req,'limit_price':7999},self.second()])
        saved=[];l.manual_check_checkpoint=lambda checks:saved.append(copy.deepcopy(checks))
        def fail(plan):
            self.assertEqual(saved[0][0]['reason'],'OWNER_LIMIT_PRICE_EXCEEDED')
            raise TimeoutError('PRIVATE')
        b.submit=fail
        with self.assertRaises(TimeoutError):self.cycle(l,b)
        self.assertEqual(len(saved[-1]),2)
        self.assertEqual(saved[-1][0]['ticker'],'000001')
        self.assertEqual(saved[-1][1]['status'],'SUBMISSION_CHECK_FAILED')
        self.assertNotIn('PRIVATE',json.dumps(saved))

    def test_checkpoint_failure_prevents_later_order_and_is_not_retried(self):
        l,b=self.fixtures();self.add_second();self.queue(b,[{**self.req,'limit_price':7999},self.second()])
        from unittest.mock import Mock
        l.manual_check_checkpoint=Mock(side_effect=OSError('PRIVATE'))
        with self.assertRaises(TradingBlocked) as caught:self.cycle(l,b)
        self.assertEqual(str(caught.exception),'OWNER_LIMIT_PRICE_EXCEEDED')
        self.assertTrue(caught.exception.manual_diagnostic_checkpoint_failed)
        self.assertEqual(b.calls,[]);self.assertEqual(l.manual_check_checkpoint.call_count,1)

    def test_successful_block_has_durable_ticker_and_cap_diagnostic(self):
        import copy
        l,b=self.fixtures();self.queue(b,[{**self.req,'quantity':1000}])
        saved=[];l.manual_check_checkpoint=lambda checks:saved.append(copy.deepcopy(checks))
        result=self.cycle(l,b)
        self.assertEqual(result['status'],'MANUAL_QUEUE_BLOCKED');self.assertEqual(b.calls,[])
        check=saved[-1][0]
        self.assertEqual(check['ticker'],'000001');self.assertEqual(check['reason'],'MANUAL_BUY_LIMIT_EXCEEDED')
        self.assertIn('PER_STOCK_EXPOSURE_LIMIT',check['limit_reasons'])
        self.assertNotIn('manual_buy_checks',result)  # private DB/dashboard only

    def test_ack_checkpoint_failure_retains_known_order_progress(self):
        l,b=self.fixtures()
        l.manual_check_checkpoint=lambda checks:(_ for _ in ()).throw(OSError('PRIVATE'))
        with self.assertRaises(ManualDiagnosticCheckpointError) as caught:self.cycle(l,b)
        self.assertEqual(caught.exception.execution_progress['orders_sent'],1)
        self.assertEqual(caught.exception.execution_progress['order_attempts'],1)
        self.assertEqual(len(b.calls),1)
        self.assertTrue(failure_details(caught.exception)['manual_diagnostic_checkpoint_failed'])

    def test_timeout_checkpoint_failure_retains_original_uncertainty(self):
        l,b=self.fixtures();original=TimeoutError('PRIVATE_WIRE');b.failure=original
        l.manual_check_checkpoint=lambda checks:(_ for _ in ()).throw(OSError('PRIVATE_DB'))
        with self.assertRaises(TimeoutError) as caught:self.cycle(l,b)
        self.assertIs(caught.exception,original)
        self.assertIsNone(caught.exception.execution_progress['orders_sent'])
        self.assertEqual(caught.exception.execution_progress['order_attempts'],1)
        self.assertTrue(l.stopped);self.assertEqual(len(b.calls),1)
        self.assertNotIn('PRIVATE',json.dumps(failure_details(caught.exception)))


class FiveMinuteScheduleTests(unittest.TestCase):
    """Validate timing/storage policy without starting a workflow or account call."""
    def setUp(self):
        self.root=pathlib.Path(__file__).resolve().parents[1]
        self.workflow=(self.root/'.github/workflows/oneil_v519_live_engine_review.yml').read_text()
        schedule=self.workflow.split('  schedule:\n',1)[1].split('  workflow_dispatch:',1)[0]
        self.crons=re.findall(r"- cron: '([^']+)'",schedule)

    def test_disjoint_ticks_cover_every_five_minutes_then_close(self):
        self.assertEqual(self.crons,[
            '0,20,40 0-10 * * 1-5',
            '5,10,15,25,30,35,45,50,55 0-10 * * 1-5',
            '0 11 * * 1-5',
        ])
        ticks=[]
        for cron in self.crons[:2]:
            minute,hour,day,month,weekday=cron.split()
            self.assertEqual((hour,day,month,weekday),('0-10','*','*','1-5'))
            ticks.extend((h+9)*60+int(m) for h in range(11) for m in minute.split(','))
        self.assertEqual(len(ticks),len(set(ticks)))
        self.assertEqual(sorted(ticks),list(range(9*60,20*60,5)))
        self.assertEqual(len(ticks),132)
        self.assertIn("github.event.schedule == '0 11 * * 1-5' && 'close'",self.workflow)

    def test_extra_ticks_do_not_add_metered_artifact_uploads(self):
        step=self.workflow.split('      - name: Save sanitized result\n',1)[1].split('      - name:',1)[0]
        condition=re.search(r'^        if: (.+)$',step,re.M).group(1)
        self.assertEqual(condition,"always() && (github.event_name != 'schedule' || github.event.schedule == '0,20,40 0-10 * * 1-5' || github.event.schedule == '0 11 * * 1-5')")
        self.assertIn('retention-days: 14',step)
        self.assertNotIn(self.crons[1],condition)
        for step_name in ('Publish encrypted private-dashboard snapshot','Record workflow summary'):
            body=self.workflow.split('      - name: '+step_name+'\n',1)[1]
            self.assertTrue(body.startswith('        if: always()\n'))

    def test_serialized_account_and_emergency_halt_are_preserved(self):
        observe=self.workflow.split('  observe:\n',1)[1]
        self.assertIn("group: v519-integrated-${{ inputs.operation == 'halt' && 'emergency-halt' || (github.event_name == 'pull_request' && github.ref || 'account') }}",observe)
        self.assertIn('      cancel-in-progress: false',observe)
        self.assertIn("(needs.test.result == 'success' || inputs.operation == 'halt')",observe)
        self.assertIn("(needs.session.result == 'success' || needs.session.result == 'skipped')",observe)

    def test_extra_ticks_keep_exact_release_and_actual_time_guards(self):
        self.assertIn('.github/workflows/oneil_v519_live_engine_review.yml',RELEASE_FILES)
        self.assertFalse(RELEASE_LIVE_APPROVED)
        self.assertIn('python -m unittest discover -s tests -p \'test_v519_live_engine.py\' -v',self.workflow)
        auto=self.workflow.split('            auto)\n',1)[1].split('            apply-approval)',1)[0]
        for guard in ('[ "$release_configured" = true ]','[ "$weekday" -le 5 ]',
                      '[ "$session_hhmm" -ge 900 ]','[ "$session_hhmm" -lt 1520 ]',
                      '[ "$session_hhmm" -ge 1600 ]','[ "$session_hhmm" -lt 2000 ]'):
            self.assertIn(guard,auto)
        self.assertIn('V519_APPROVED_RELEASE="$APPROVED_RELEASE" python oneil_v519_live_engine.py --run --execute',auto)
        self.assertIn('python oneil_v519_live_engine.py --run --dashboard-public-key data/live/dashboard_public_key.pem',auto)
        self.assertNotIn('--resume-halt',auto)
        self.assertNotIn('--activate',auto)

    def test_only_standard_public_repository_runners_are_used(self):
        paths=('.github/workflows/oneil_v519_live_engine_review.yml',
               '.github/workflows/kis_live_v4_5_2.yml',
               '.github/workflows/oneil_v519_release_attest.yml',
               '.github/workflows/oneil_v519_review_evidence.yml')
        for path in paths:
            runners=re.findall(r'^\s+runs-on: (.+)$',(self.root/path).read_text(),re.M)
            self.assertTrue(runners,path)
            self.assertEqual(set(runners),{'ubuntu-latest'},path)

def owner_manual_env(request_id,release='b'*64,revision=1):
    return {'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
        'GITHUB_REPOSITORY':'asura2367-boop/oneil-rs-scanner','GITHUB_REPOSITORY_OWNER':'asura2367-boop',
        'GITHUB_ACTOR':'asura2367-boop','GITHUB_TRIGGERING_ACTOR':'asura2367-boop',
        'GITHUB_WORKFLOW_REF':'asura2367-boop/oneil-rs-scanner/.github/workflows/oneil_v519_live_engine_review.yml@refs/heads/main',
        'OPERATION':'manual-buy','REQUESTED_MANUAL_REQUEST_ID':request_id,
        'REQUESTED_RELEASE':release,'REQUESTED_APPROVAL_REVISION':str(revision)}

def owner_manual_review(release='b'*64,revision=1):
    return {'schema_version':1,'operator':'asura2367-boop','approved_release':release,
        'source_commit':'a'*40,'revision':revision,'review_run_id':123,'approved_at':'2026-11-02T10:00:00+09:00'}

class ManualOnlyDispatchTests(unittest.TestCase):
    setUp=ManualAveragingTests.setUp
    def fixtures(self,requests=None):
        ledger,broker=ManualAveragingTests.fixtures(self)
        self.snap['positions']['000001']['average']='8000'
        ledger.dashboard_state=None
        ledger.rows=lambda query,args:[r for r in ledger.records if r['signal']==args[1]]
        original=broker.buy_control()
        broker.buy_control=lambda:{**original,'release':owner_manual_review(),
            'manual_buys':requests if requests is not None else [self.req]}
        return ledger,broker
    def cycle(self,ledger,broker,request_id=None,env=None):
        from contextlib import ExitStack
        ident=request_id if request_id is not None else self.req['id']
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,env or owner_manual_env(ident)))
            stack.enter_context(patch('oneil_v519_live_engine.require_live_release'))
            stack.enter_context(patch('oneil_v519_live_engine.release_fingerprint',return_value='b'*64))
            for name in ('integrated_cycle','_automatic_cycle','scan_candidates','activate_live_account'):
                stack.enter_context(patch('oneil_v519_live_engine.'+name,side_effect=AssertionError(name+' forbidden')))
            return manual_buy_cycle(ledger,broker,ident,now=self.now,clock=lambda:self.now)
    def second(self):
        self.snap['positions']['000002']=self.snap['positions']['000001'].copy()
        return {**self.req,'id':'22222222-2222-4222-8222-222222222222','ticker':'000002'}
    def test_exact_uuid_only_among_other_requests_no_automatic_or_exit_cycle(self):
        second=self.second();l,b=self.fixtures([self.req,second])
        result=self.cycle(l,b,second['id'])
        self.assertEqual(result['status'],'ACKNOWLEDGED');self.assertEqual(result['orders_sent'],1)
        self.assertEqual([(p.ticker,p.signal) for p in b.calls],[('000002','owner:'+second['id'])])
        self.assertEqual(len(l.dashboard_state['manual_buy_checks']),1)
        self.assertEqual(l.dashboard_state['candidates'],[])
        self.assertNotIn('id',result);self.assertNotIn(second['id'],json.dumps(result))
    def test_missing_duplicate_malformed_expired_changed_account_release_fail_closed(self):
        variants=[[],[self.req,self.req],[{**self.req,'quantity':True}],
            [{**self.req,'expires_at':self.now.isoformat()}],[{**self.req,'account_ref':'c'*64}],
            [{**self.req,'approved_release':'c'*64}]]
        for requests in variants:
            with self.subTest(requests=requests):
                second=self.second();l,b=self.fixtures([*requests,second])
                result=self.cycle(l,b)
                self.assertEqual(result['manual_buy_status'],'BLOCKED');self.assertEqual(b.calls,[])
                self.assertEqual(l.records,[])
    def test_invalid_dispatch_context_never_sends_or_activates(self):
        fields={'GITHUB_ACTIONS':'false','GITHUB_EVENT_NAME':'schedule','GITHUB_REF':'refs/heads/other',
            'GITHUB_REPOSITORY':'other/repo','GITHUB_REPOSITORY_OWNER':'other','GITHUB_ACTOR':'other',
            'GITHUB_TRIGGERING_ACTOR':'other','GITHUB_WORKFLOW_REF':'other/workflow@refs/heads/main',
            'OPERATION':'auto','REQUESTED_MANUAL_REQUEST_ID':'other','REQUESTED_RELEASE':'c'*64,
            'REQUESTED_APPROVAL_REVISION':'2'}
        for key,value in fields.items():
            with self.subTest(field=key):
                l,b=self.fixtures();env={**owner_manual_env(self.req['id']),key:value}
                with self.assertRaises(TradingBlocked):self.cycle(l,b,env=env)
                self.assertEqual(b.calls,[]);self.assertEqual(l.records,[])
        for ident in ('','bad',self.req['id'].upper(),self.req['id'].replace('-4','-3',1)):
            l,b=self.fixtures()
            if ident==self.req['id']:continue
            with self.assertRaises(TradingBlocked):self.cycle(l,b,ident)
            self.assertEqual(b.calls,[])
    def test_unconditional_history_before_recovery_and_controls_inside_lock(self):
        l,b=self.fixtures();events=[];depth=0
        @contextmanager
        def lock():
            nonlocal depth
            depth+=1;events.append('lock')
            try:yield
            finally:depth-=1
        l.lock=lock
        b.history=lambda day:(self.assertGreater(depth,0),events.append('history'),[])[2]
        pending=l.pending;l.pending=lambda:(events.append('pending'),pending())[1]
        control=b.buy_control;b.buy_control=lambda:(self.assertGreater(depth,0),events.append('control'),control())[2]
        self.cycle(l,b)
        self.assertEqual(events[:3],['lock','history','pending'])
        self.assertLess(events.index('pending'),events.index('control'))
        l,b=self.fixtures();b.history=lambda day:None
        with self.assertRaisesRegex(TradingBlocked,'BROKER_HISTORY_INCOMPLETE'):self.cycle(l,b)
        self.assertEqual(b.calls,[])
    def test_unresolved_history_never_collects_account_or_sends(self):
        l,b=self.fixtures();l.pending=lambda:[{'broker_order_id':None,'trading_day':self.now.date()}]
        b.snapshot=lambda:(_ for _ in ()).throw(AssertionError('snapshot forbidden'))
        result=self.cycle(l,b)
        self.assertEqual(result['status'],'BLOCKED_UNRESOLVED_ORDERS');self.assertEqual(b.calls,[])
        self.assertIsNone(l.dashboard_state)
    def test_selected_recorded_or_daily_blocked_never_chooses_another(self):
        for daily in (False,True):
            second=self.second();l,b=self.fixtures([self.req,second])
            if daily:l.bought_today=lambda day:{'000001'}
            else:l.records=[{'signal':'owner:'+self.req['id']}]
            result=self.cycle(l,b)
            self.assertEqual(result['status'],'MANUAL_REQUEST_BLOCKED' if daily else 'MANUAL_ALREADY_RECORDED')
            self.assertEqual(b.calls,[])
    def test_exit_priority_blocks_without_selling_or_unrelated_buy(self):
        second=self.second();l,b=self.fixtures([self.req,second]);b.quote=lambda *args:12000
        result=self.cycle(l,b)
        self.assertEqual(result['manual_buy_reason'],'EXIT_PRIORITY');self.assertEqual(b.calls,[])
        self.assertEqual(l.records,[])
    def test_halt_missing_activation_and_settings_changes_stop_selected(self):
        for condition in ('halt','activation','buy','risk','holding','price','cash'):
            with self.subTest(condition=condition):
                self.setUp();l,b=self.fixtures();control=b.buy_control()
                if condition=='halt':l.stopped=True
                if condition=='activation':l.release_authorized=lambda r:False
                if condition=='buy':b.buy_control=lambda:{**control,'enabled':False}
                if condition=='risk':b.buy_control=lambda:{**control,'risk':{**control['risk'],'revision':2}}
                if condition=='holding':self.snap['positions']['000001']['quantity']=11
                if condition=='price':self.req['limit_price']=7999
                if condition=='cash':b.power=lambda *args:(0,0)
                result=self.cycle(l,b)
                self.assertEqual(result['manual_buy_status'],'BLOCKED');self.assertEqual(b.calls,[])
    def test_review_revision_and_immutable_request_rechecked_before_reservation_and_send(self):
        for stage in (2,3,4):
            for change in ('revision','source','request','withdraw'):
                with self.subTest(stage=stage,change=change):
                    l,b=self.fixtures();control=b.buy_control();calls=0
                    def current():
                        nonlocal calls
                        calls+=1
                        if calls<stage:return control
                        if change=='revision':return {**control,'release':{**control['release'],'revision':2}}
                        if change=='source':return {**control,'release':{**control['release'],'source_commit':'d'*40}}
                        return {**control,'manual_buys':[] if change=='withdraw' else [{**self.req,'limit_price':8001}]}
                    b.buy_control=current
                    if stage==4:
                        with self.assertRaises(TradingBlocked):self.cycle(l,b)
                        self.assertTrue(l.stopped)
                    else:
                        result=self.cycle(l,b);self.assertEqual(result['manual_buy_status'],'BLOCKED')
                    self.assertEqual(b.calls,[])
    def test_expiry_in_send_guard_and_uncertain_outcome_never_retry(self):
        l,b=self.fixtures()
        @contextmanager
        def guard():
            self.now+=dt.timedelta(minutes=20)
            yield
        l.send_guard=guard
        with self.assertRaises(TradingBlocked):self.cycle(l,b)
        self.assertEqual(b.calls,[]);self.assertTrue(l.stopped)
        self.setUp();l,b=self.fixtures();b.failure=TimeoutError('PRIVATE_WIRE')
        with self.assertRaises(TimeoutError) as error:self.cycle(l,b)
        self.assertIsNone(error.exception.execution_progress['orders_sent']);self.assertTrue(l.stopped)
        self.cycle(l,b);self.assertEqual(len(b.calls),1)
    def test_diagnostic_failure_preserves_ack_or_unknown_and_does_not_replay(self):
        for fail_wire in (False,True):
            l,b=self.fixtures();l.manual_check_checkpoint=lambda checks:(_ for _ in ()).throw(OSError('PRIVATE_DB'))
            if fail_wire:b.failure=TimeoutError('PRIVATE_WIRE')
            with self.assertRaises(TimeoutError if fail_wire else ManualDiagnosticCheckpointError) as error:self.cycle(l,b)
            self.assertTrue(error.exception.manual_diagnostic_checkpoint_failed)
            self.assertEqual(error.exception.execution_progress['orders_sent'],None if fail_wire else 1)
            self.assertEqual(len(b.calls),1)
            self.assertNotIn('PRIVATE',json.dumps(failure_details(error.exception)))
    def test_typed_not_sent_keeps_record_and_no_halt_or_other_request(self):
        second=self.second();l,b=self.fixtures([self.req,second])
        b.submit=lambda plan:(_ for _ in ()).throw(OrderNotSent('FRESH_EXECUTABLE_BOOK_REQUIRED'))
        result=self.cycle(l,b)
        self.assertEqual(result['status'],'MANUAL_PRE_SUBMIT_REJECTED');self.assertEqual(result['orders_sent'],0)
        self.assertEqual(len(l.records),1);self.assertTrue(l.records[0]['verified_not_sent']);self.assertFalse(l.stopped)
        self.assertEqual(self.cycle(l,b)['status'],'MANUAL_ALREADY_RECORDED')

    def test_submission_and_halt_double_failure_preserves_unknown_not_zero(self):
        for failure in ('submit','ack','unlock'):
            with self.subTest(failure=failure):
                l,b=self.fixtures()
                if failure=='submit':b.failure=TimeoutError('PRIVATE_SUBMIT')
                if failure=='ack':l.ack=lambda *args:(_ for _ in ()).throw(OSError('PRIVATE_ACK'))
                if failure=='unlock':
                    depth=0
                    @contextmanager
                    def lock():
                        nonlocal depth
                        depth+=1
                        try:yield
                        finally:
                            depth-=1
                            if depth==1:raise OSError('PRIVATE_UNLOCK')
                    l.lock=lock
                l.halt=lambda:(_ for _ in ()).throw(OSError('PRIVATE_HALT'))
                with self.assertRaises(OSError) as error:self.cycle(l,b)
                self.assertIsNone(error.exception.execution_progress['orders_sent'])
                self.assertEqual(error.exception.execution_progress['order_attempts'],1)
                self.assertEqual(len(b.calls),1)
                if failure!='unlock':self.assertIn('submission_failure',failure_details(error.exception))
                self.assertNotIn('PRIVATE',json.dumps(failure_details(error.exception)))

class ManualOnlyRuntimeTests(unittest.TestCase):
    def test_cli_routing_never_activates_scans_or_runs_automatic_cycle(self):
        import io,sys
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        ident='11111111-1111-4111-8111-111111111111';ledger=MagicMock();ledger.dashboard_state=None
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            out=pathlib.Path(directory)/'result.json'
            stack.enter_context(patch.object(sys,'argv',['engine','--manual-buy','--manual-request-id',ident,'--execute','--output',str(out)]))
            stack.enter_context(patch.dict(os.environ,{**owner_manual_env(ident),'SUPABASE_DB_URL':'','V519_DASHBOARD_REVIEW_REVISION':'1'}))
            stack.enter_context(patch('oneil_v519_live_engine.dt.datetime',RegularSessionClock))
            stack.enter_context(patch('oneil_v519_live_engine.read_buy_control',return_value={'connected':True,'release':owner_manual_review()}))
            stack.enter_context(patch('oneil_v519_live_engine.release_fingerprint',return_value='b'*64))
            stack.enter_context(patch('oneil_v519_live_engine.require_live_release'))
            stack.enter_context(patch('v519_pg_ledger.connect'))
            stack.enter_context(patch('v519_pg_ledger.PgRuntimeLedger',return_value=ledger))
            creds=stack.enter_context(patch('kis_buying_power_readonly.credentials',return_value=('key','secret','account','01')))
            broker=stack.enter_context(patch('oneil_v519_live_engine.KisBroker'))
            cycle=stack.enter_context(patch('oneil_v519_live_engine.manual_buy_cycle',return_value={'status':'ACKNOWLEDGED','gate':'ALLOW','orders_sent':1,'order_attempts':1}))
            for name in ('integrated_cycle','_automatic_cycle','scan_candidates','activate_live_account','activation_candidates'):
                stack.enter_context(patch('oneil_v519_live_engine.'+name,side_effect=AssertionError(name+' forbidden')))
            stack.enter_context(patch('sys.stdout',new_callable=io.StringIO))
            with self.assertRaises(SystemExit) as stopped:runtime_main()
            self.assertEqual(stopped.exception.code,0)
            cycle.assert_called_once_with(ledger,broker.return_value,ident,expected_review=owner_manual_review())
            broker.assert_called_once_with(execution=True,operator_activation=False);creds.assert_called_once_with(read_only=False)
            ledger.activate_release.assert_not_called();ledger.halt.assert_not_called()
            self.assertNotIn(ident,out.read_text())
    def test_cli_missing_or_invalid_manual_arguments_deny_before_database(self):
        import io,sys
        for argv in (['--manual-buy'],['--manual-buy','--execute'],['--run','--manual-request-id','bad'],
                     ['--manual-buy','--manual-request-id','bad','--execute']):
            with self.subTest(argv=argv),tempfile.TemporaryDirectory() as directory:
                out=pathlib.Path(directory)/'result.json'
                with patch.object(sys,'argv',['engine',*argv,'--output',str(out)]),patch('v519_pg_ledger.connect') as connect_db,patch('sys.stdout',new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as stopped:runtime_main()
                self.assertEqual(stopped.exception.code,2);connect_db.assert_not_called()
    def test_workflow_manual_only_inputs_and_shared_dependencies_remain(self):
        text=pathlib.Path('.github/workflows/oneil_v519_live_engine_review.yml').read_text()
        branch=text.split('            manual-buy)')[1].split('            trade)')[0]
        self.assertIn('--manual-buy --manual-request-id "$REQUESTED_MANUAL_REQUEST_ID" --execute',branch)
        self.assertNotIn('--run',branch);self.assertNotIn('--activate',branch);self.assertNotIn('--apply-owner-approval',branch)
        self.assertNotIn('REQUESTED_MANUAL_REQUEST_ID: ${{ inputs.manual_request_id }}',text)
        masking=text.split('      - name: Mask private manual request identifier')[1].split('      - name:')[0]
        self.assertIn('context.payload.inputs?.manual_request_id',masking)
        self.assertLess(masking.index('core.setSecret(requestId)'),masking.index("core.exportVariable('REQUESTED_MANUAL_REQUEST_ID'"))
        self.assertNotIn('${{ inputs.manual_request_id }}',text)
        self.assertIn("env.OPERATION == 'manual-buy'",text)
        self.assertIn('needs: [test, session]',text);self.assertIn('V519_TEST_DB_URL:',text)
        self.assertIn("cron: '5,10,15,25,30,35,45,50,55 0-10 * * 1-5'",text)
class ManualFinalQuoteTests(unittest.TestCase):
    setUp=ManualOnlyDispatchTests.setUp
    fixtures=ManualOnlyDispatchTests.fixtures
    cycle=ManualOnlyDispatchTests.cycle
    def real_broker(self,change=None):
        l,fixture=self.fixtures();b=object.__new__(KisBroker)
        b.execution=True;b.account='12345678';b.product='01';b.quote_evidence={};b.history_cache={}
        for name in ('buy_control','snapshot','history','quote'):setattr(b,name,getattr(fixture,name))
        b.calls=[];b.reads=0;b.refresh_after_controls=[]
        def power(*args):
            self.now+=dt.timedelta(seconds=5)
            return (100000,100)
        b.power=power
        def quote(ticker,side):
            b.reads+=1
            price=8000;size=100
            if b.reads==2:
                if change=='error':raise TimeoutError('PRIVATE_QUOTE')
                if change=='price_up':price=8001
                if change=='price_down':price=7999
                if change=='depth':size=4
                if change=='expiry':self.now+=dt.timedelta(minutes=20)
                if change=='session':self.now=self.now.replace(hour=15,minute=20)
            b.quote_evidence[(ticker,side)]={'price':price,'size':size,'at':self.now,'venue':'KRX',
                'session':execution_session(self.now),'order_type':limit_order_code(execution_session(self.now) or 'REGULAR')}
            return price
        b.execution_quote=quote
        def wire(path,tr,body,post=False):
            self.assertTrue(post);b.calls.append(body)
            if change=='wire':raise TimeoutError('PRIVATE_POST')
            return None,{'rt_cd':'0','output':{'ODNO':'000123'}}
        b.request=wire
        return l,b
    def clock_patch(self):
        test=self
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return test.now.astimezone(tz) if tz else test.now
        return patch('oneil_v519_live_engine.dt.datetime',Clock)
    def test_refresh_after_slow_checks_preserves_exact_reserved_price_quantity_and_post_once(self):
        l,b=self.real_broker()
        with self.clock_patch():result=self.cycle(l,b)
        self.assertEqual(result['status'],'ACKNOWLEDGED');self.assertEqual(b.reads,2);self.assertEqual(len(b.calls),1)
        self.assertEqual(b.calls[0]['ORD_UNPR'],'8000');self.assertEqual(b.calls[0]['ORD_QTY'],'5')
    def test_final_refresh_changed_price_read_failure_or_depth_blocks_before_post(self):
        for change,reason in [('error','MANUAL_FINAL_QUOTE_UNAVAILABLE'),('price_up','MANUAL_FINAL_PRICE_CHANGED'),
                ('price_down','MANUAL_FINAL_PRICE_CHANGED'),('depth','BOOK_SIZE_EXCEEDED')]:
            with self.subTest(change=change):
                self.setUp();l,b=self.real_broker(change)
                with self.clock_patch():result=self.cycle(l,b)
                self.assertEqual(result['status'],'MANUAL_PRE_SUBMIT_REJECTED');self.assertEqual(result['manual_buy_reason'],reason)
                self.assertEqual(b.calls,[]);self.assertEqual(b.reads,2);self.assertFalse(l.stopped)
                self.assertEqual(len(l.records),1);self.assertTrue(l.records[0]['verified_not_sent'])
    def test_refresh_cannot_extend_owner_expiry_or_execution_session(self):
        for change in ('expiry','session'):
            self.setUp();l,b=self.real_broker(change)
            with self.clock_patch():result=self.cycle(l,b)
            self.assertEqual(result['status'],'MANUAL_PRE_SUBMIT_REJECTED')
            self.assertEqual(b.calls,[]);self.assertEqual(len(l.records),1);self.assertFalse(l.stopped)
    def test_submit_timeout_after_refresh_is_unknown_not_typed_pre_send(self):
        l,b=self.real_broker('wire')
        with self.clock_patch(),self.assertRaises(TimeoutError) as error:self.cycle(l,b)
        self.assertIsNone(error.exception.execution_progress['orders_sent']);self.assertTrue(l.stopped)
        self.assertEqual(len(b.calls),1);self.assertEqual(b.reads,2)
        with self.clock_patch():self.cycle(l,b)
        self.assertEqual(len(b.calls),1);self.assertNotIn('verified_not_sent',l.records[0])
