import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from oneil_live_v5_8 import orchestrate
SAMPLE=Path('v5_7_synthetic.json').read_text(encoding='utf-8')
class V58Test(unittest.TestCase):
    def exercise(self,broker=False,result=0,env=None):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'v5_7_synthetic.json').write_text(SAMPLE,encoding='utf-8')
            def runner(*a,**kw):return SimpleNamespace(returncode=result)
            return orchestrate(p,'v5_7_synthetic.json',datetime(2026,10,27,10,tzinfo=ZoneInfo('Asia/Seoul')),
              broker_read=broker,env=env or {'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'},runner=runner)
    def test_offline(self):
        d=self.exercise();self.assertEqual(d['gate'],'DENY');self.assertEqual(d['broker_read_status'],'NOT_REQUESTED')
    def test_broker_success_still_denied(self):
        d=self.exercise(True,0);self.assertEqual(d['broker_read_status'],'CHILD_COMPLETED');self.assertFalse(d['actual_orderable_cash_verified'])
    def test_broker_failed(self):
        d=self.exercise(True,1);self.assertIn('BROKER_READ_FAILED_OR_BLOCKED',d['blocking_reasons'])
    def test_separate_synthetic(self):
        d=self.exercise();self.assertEqual(d['synthetic_preview']['source'],'SYNTHETIC_EXAMPLE');self.assertEqual(d['orders_sent'],0)
    def test_live_flag_denied(self):
        with self.assertRaises(RuntimeError):self.exercise(env={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'})
if __name__=='__main__':unittest.main()
