import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_history_observe import observe
N=dt.datetime(2026,10,28,13,0,tzinfo=ZoneInfo('Asia/Seoul'))
class Tests(unittest.TestCase):
 def call(self,provider,enabled=True,env=None):return observe(N,enabled=enabled,provider=provider,env=env or {'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
 def test_no_broker_requested(self):self.assertEqual(self.call(None,False)['status'],'NOT_REQUESTED')
 def test_empty_complete(self):
  r=self.call(lambda *args: ('D',{'rt_cd':'0','output1':[]}));self.assertEqual(r['status'],'OBSERVED_PAGES_COMPLETE_NOT_RECONCILED');self.assertFalse(r['full_order_history_verified'])
 def test_one_page_rows(self):self.assertEqual(self.call(lambda *args: ('',{'rt_cd':'0','output1':[{}]}))['rows_observed'],1)
 def test_broken_response(self):self.assertEqual(self.call(lambda *args: ('D',{'rt_cd':'1','output1':[]}))['status'],'BLOCKED')
 def test_missing_header(self):self.assertEqual(self.call(lambda *args: (None,{'rt_cd':'0','output1':[]}))['status'],'BLOCKED')
 def test_wrong_type(self):self.assertEqual(self.call(lambda *args: ('D',{'rt_cd':'0','output1':{}}))['status'],'BLOCKED')
 def test_missing_cursor(self):self.assertEqual(self.call(lambda *args: ('F',{'rt_cd':'0','output1':[]}))['status'],'BLOCKED')
 def test_two_pages(self):
  def provider(day,fk,nk,again):
   return ('D',{'rt_cd':'0','output1':[{}]}) if again else ('F',{'rt_cd':'0','output1':[{}],'ctx_area_fk100':'A','ctx_area_nk100':'B'})
  r=self.call(provider);self.assertEqual((r['pages_observed'],r['rows_observed']),(2,2))
 def test_repeated_cursor(self):
  r=self.call(lambda *args:('F',{'rt_cd':'0','output1':[],'ctx_area_fk100':'A','ctx_area_nk100':'B'}));self.assertEqual(r['status'],'BLOCKED')
 def test_transport_failure(self):
  def f(*args):raise RuntimeError('secret!')
  self.assertNotIn('secret',str(self.call(f)))
 def test_switch(self):
  with self.assertRaises(ValueError):self.call(None,env={'LIVE_ENABLED':'true'})
 def test_never_authorizes(self):
  r=self.call(lambda *args: ('D',{'rt_cd':'0','output1':[{'odno':'private'}]}));self.assertEqual(r['orders_sent'],0);self.assertNotIn('private',str(r))
if __name__=='__main__':unittest.main()
