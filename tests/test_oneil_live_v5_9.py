import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from oneil_live_v5_9 import analyze,REQUIRED
KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026,10,27,10,0,tzinfo=KST)
class Tests(unittest.TestCase):
 def test_missing(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertIn('SOURCE_FILE_MISSING',analyze(Path(d)/'x',NOW)['reason_codes'])
 def test_missing_columns(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'c.csv';p.write_text('date,ticker,rs\n')
   self.assertIn('REQUIRED_COLUMNS_MISSING',analyze(p,NOW)['reason_codes'])
 def test_review_only(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'c.csv'
   p.write_text(','.join(REQUIRED)+'\n2026-10-26,052460,98,200,200,1.4,true,0.5,5800\n')
   result=analyze(p,NOW)
   self.assertEqual(len(result['review_candidates']),1)
   self.assertEqual(result['approved_order_intents'],[])
   self.assertEqual(result['status'],'DENY')
 def test_stale(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'c.csv';p.write_text(','.join(REQUIRED)+'\n2026-10-15,052460,98,200,200,1.4,true,0.5,5800\n')
   self.assertIn('STALE_SIGNAL_ROWS',analyze(p,NOW)['reason_codes'])
 def test_hard_flags(self):
  with patch.dict(os.environ, {'LIVE_ENABLED':'true'}):
   with self.assertRaises(RuntimeError):analyze(Path('none'),NOW)
if __name__=='__main__':unittest.main()
