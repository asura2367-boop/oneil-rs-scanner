import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_phase234 import Blocked,verify_equity,allocation,event_fold,simulate
N=dt.datetime(2026,10,28,10,0,tzinfo=ZoneInfo('Asia/Seoul'))
def ev(idx,state,fill=0,ident=None):
 return {'event_id':str(idx),'at_kst':(N+dt.timedelta(seconds=idx)).isoformat(),'next_state':state,'filled_qty':fill,'broker_order_id':ident}
class Tests(unittest.TestCase):
 def setUp(self):
  self.e={'market_date':N.date().isoformat(),'scope':'WHOLE_ACCOUNT','baseline_time_kst':(N-dt.timedelta(hours=1)).isoformat(),'current_time_kst':(N-dt.timedelta(seconds=10)).isoformat(),'baseline_equity_krw':1000000,'current_equity_krw':999999,'net_external_cashflow_krw':0,'cashflows_reconciled':True,'fees_included':True,'holdings_reconciled':True,'source_complete':True,'independently_verified':True}
 def test_equity_pnl(self):self.assertFalse(verify_equity(self.e,N)['loss_limit_reached'])
 def test_loss_hit(self):self.e['current_equity_krw']=979000;self.assertTrue(verify_equity(self.e,N)['loss_limit_reached'])
 def test_equity_missing(self):self.e['cashflows_reconciled']=False;self.assertRaises(Blocked,verify_equity,self.e,N)
 def test_equity_stale(self):self.e['current_time_kst']=(N-dt.timedelta(minutes=6)).isoformat();self.assertRaises(Blocked,verify_equity,self.e,N)
 def test_zero_base(self):self.e['baseline_equity_krw']=0;self.assertRaises(Blocked,verify_equity,self.e,N)
 def test_allocation(self):self.assertEqual(allocation(1000000,600000,10000,risk_verified=True,price_certified=True,account_verified=True)['simulated_qty'],18)
 def test_allocation_insufficient_evidence(self):self.assertRaises(Blocked,allocation,1000000,600000,10000)
 def test_allocation_existing(self):self.assertRaises(Blocked,allocation,1000000,600000,10000,risk_verified=True,price_certified=True,account_verified=True,held=True)
 def test_allocation_exposure(self):self.assertEqual(allocation(1000000,600000,10000,current_trading_exposure=500000,risk_verified=True,price_certified=True,account_verified=True)['simulated_qty'],10)
 def test_unknown_no_retry(self):self.assertFalse(event_fold({'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN')])['automatic_resubmission_allowed'])
 def test_partial_then_complete(self):self.assertEqual(event_fold({'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(2,'ACKNOWLEDGED',0,'abc'),ev(3,'PART_FILLED',4,'abc'),ev(4,'FILLED',10,'abc')])['state'],'FILLED')
 def test_overfill(self):self.assertRaises(Blocked,event_fold,{'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(2,'FILLED',11,'abc')])
 def test_regression(self):self.assertRaises(Blocked,event_fold,{'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(2,'PART_FILLED',4,'abc'),ev(3,'PART_FILLED',2,'abc')])
 def test_duplicate(self):self.assertRaises(Blocked,event_fold,{'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(1,'ACKNOWLEDGED',0,'abc')])
 def test_conflicting_broker_id(self):self.assertRaises(Blocked,event_fold,{'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(2,'ACKNOWLEDGED',0,'a'),ev(3,'PART_FILLED',3,'b')])
 def test_terminal_reopen(self):self.assertRaises(Blocked,event_fold,{'ticker':'144960','requested_qty':10},[ev(1,'SUBMISSION_UNKNOWN'),ev(2,'FILLED',10,'a'),ev(3,'PART_FILLED',9,'a')])
 def test_simulation_always_deny(self):
  c={'gate':'DENY','orders_sent':0,'intents':[{'ticker':'144960'}]};a={'broker_status':'READ_ONLY_OBSERVED'};q={'version':'5.18'};h={'status':'OBSERVED_PAGES_COMPLETE_NOT_RECONCILED'}
  r=simulate(c,a,q,h,now=N);self.assertEqual(r['gate'],'DENY');self.assertEqual(r['orders_sent'],0)
 def test_simulation_missing(self):self.assertEqual(simulate(None,None,None,None,now=N)['candidate_count'],0)
if __name__=='__main__':unittest.main()
