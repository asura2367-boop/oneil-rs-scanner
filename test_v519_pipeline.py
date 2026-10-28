import unittest,datetime as dt
from zoneinfo import ZoneInfo
from v519_pipeline import pipeline
class PipelineTests(unittest.TestCase):
 def test_candidate_to_broker_and_db(self):
  now=dt.datetime(2026,10,27,12,15,tzinfo=ZoneInfo('Asia/Seoul'))
  report={'candidate_input_valid':True,'candidate_source':'V4B_CSV_UNVERIFIED','mode':'READ_ONLY_CANDIDATE_AND_ACCOUNT_EVIDENCE','generated_at_kst':'2026-10-27T12:12:36+09:00','candidate_count':1,'candidates':[{'ticker':'085910','rs':98}]}
  result=pipeline(report,now=now,broker_reader=lambda t:{'ticker':t,'quote':25000},ledger_reader=lambda:{'halted':True,'unresolved':0})
  self.assertEqual(result['candidates'][0]['theoretical_max_qty'],7)
  self.assertEqual(result['orders_sent'],0)
  with self.assertRaises(ValueError):pipeline(report,now=now+dt.timedelta(days=2),broker_reader=lambda t:None,ledger_reader=lambda:None)
if __name__=='__main__':unittest.main()
