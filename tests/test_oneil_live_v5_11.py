import sys,unittest,datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oneil_live_v5_11 import assemble
NOW=dt.datetime(2026,10,27,12,tzinfo=ZoneInfo('Asia/Seoul'))
BASE={'status':'DENY','source':'V4B_CSV_UNVERIFIED','orders_sent':0,'review_candidates':[]}
class Tests(unittest.TestCase):
    def test_empty(self):
        x=assemble(BASE,{},NOW);self.assertIn('NO_CANDIDATES',x['blocking_reasons']);self.assertEqual(x['gate'],'DENY')
    def test_live_separated_from_paper(self):
        c={**BASE,'review_candidates':[{'ticker':'085910','date':'2026-10-27','flag':'REVIEW_ONLY_NOT_LIVE_QUOTE','rs':98,'flow_category':'INSTITUTION_ONLY'}]}
        o=assemble(c,{'broker_report_checks':{'preflight':'NO_INTENTS_DENY'}},NOW)
        self.assertEqual(len(o['intents']),1); self.assertIsNone(o['intents'][0]['quantity']);self.assertFalse(o['paper_state_used']);self.assertEqual(o['orders_sent'],0)
    def test_duplicate_blocked(self):
        row={'ticker':'085910','date':'2026-10-27','flag':'REVIEW_ONLY_NOT_LIVE_QUOTE'}
        with self.assertRaises(ValueError): assemble({**BASE,'review_candidates':[row,row]}, {}, NOW)
    def test_future_blocked(self):
        with self.assertRaises(ValueError): assemble({**BASE,'review_candidates':[{'ticker':'085910','date':'2026-10-28','flag':'REVIEW_ONLY_NOT_LIVE_QUOTE'}]}, {}, NOW)
    def test_invalid_source_blocked(self):
        with self.assertRaises(ValueError): assemble({**BASE,'source':'PAPER'}, {}, NOW)
if __name__=='__main__':unittest.main()
