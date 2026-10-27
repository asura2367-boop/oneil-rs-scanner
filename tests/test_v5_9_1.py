import unittest, tempfile, sys, os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oneil_live_v5_9 import analyze
HEADER='date,ticker,rs,foreign_5d,institution_5d,volume_ratio,channel_position,channel_up,signal_date,name\n'
NOW=datetime(2026,10,27,10,30,tzinfo=ZoneInfo('Asia/Seoul'))
class CandidateTests(unittest.TestCase):
 def read(self, csv):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'candidates.csv';p.write_text(csv)
   with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):return analyze(p,NOW)
 def test_empty_header_only(self):
  r=self.read(HEADER);self.assertEqual(r['reason_codes'],['NO_CANDIDATES']);self.assertEqual(r['status'],'DENY')
 def test_missing_optional_close(self):
  r=self.read(HEADER+'2026-10-27,052460,96,10,20,1.5,0.4,True,2026-10-26,example\n')
  self.assertEqual(len(r['review_candidates']),1);self.assertNotIn('reference_close_krw',r['review_candidates'][0]);self.assertEqual(r['orders_sent'],0)
 def test_missing_actual_required(self):
  r=self.read('date,ticker,rs\n');self.assertIn('REQUIRED_COLUMNS_MISSING',r['reason_codes'])
 def test_optional_close_if_present(self):
  r=self.read(HEADER.rstrip('\n')+',reference_close\n'+'2026-10-27,052460,95,10,20,1.4,0.4,True,2026-10-26,example,6000\n');self.assertEqual(r['review_candidates'][0]['reference_close_krw'],6000)
 def test_reject_live(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.csv';p.write_text(HEADER)
   with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
    with self.assertRaises(RuntimeError):analyze(p,NOW)
if __name__=='__main__':unittest.main()
