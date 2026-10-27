import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from oneil_v519_power_bridge import build
class Tests(unittest.TestCase):
 def test_valid_five_intents_still_denied(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'i.json';f.write_text(json.dumps({'gate':'DENY','orders_sent':0,'mode':'LIVE_INTENTS_PREVIEW_ONLY','intents':[{'ticker':f'{i:06d}','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None} for i in range(1,6)]}))
   r=build(f);self.assertEqual(r['candidate_count'],5);self.assertFalse(r['broker_buying_power_verified']);self.assertEqual(r['orders_sent'],0)
 def test_missing_is_deny(self):
  r=build('/nonexistent/intent.json');self.assertEqual(r['gate'],'DENY');self.assertFalse(r.get('candidate_input_verified',True))
 def test_duplicate_denied(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'i.json';f.write_text(json.dumps({'gate':'DENY','orders_sent':0,'mode':'LIVE_INTENTS_PREVIEW_ONLY','intents':[{'ticker':'000001','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None}]*2}))
   r=build(f);self.assertEqual(r['gate'],'DENY');self.assertFalse(r.get('candidate_input_verified',True))
 def test_live_flags_refused(self):
  with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
   with self.assertRaises(RuntimeError):build('/nonexistent')
if __name__=='__main__': unittest.main()
