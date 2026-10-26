import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from oneil_live_v5_6 import orchestrate

class TestV56(unittest.TestCase):
    def test_offline_denies(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            result=orchestrate(root,root/'out.json',env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'},
                               runner=lambda *a,**k: SimpleNamespace(returncode=0))
            assert result['status']=='DENY' and result['orders_sent']==0
            assert result['observations']==['OFFLINE_ONLY']
    def test_broker_failure_never_allows(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            result=orchestrate(root,root/'out.json',use_broker=True,
                               env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'},
                               runner=lambda *a,**k: SimpleNamespace(returncode=1))
            assert 'BROKER_READ_FAILED' in result['observations']
            assert result['actual_broker_trade_authorization'] is False
    def test_old_reports_deleted(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); (root/'v5_5_report.json').write_text('{"inspection_issues":[]}')
            result=orchestrate(root,root/'out.json',env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'},
                               runner=lambda *a,**k: SimpleNamespace(returncode=1))
            assert 'EVIDENCE_MISSING' in result['evidence_issues']
    def test_live_flag_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(RuntimeError,'LIVE_FLAGS_NOT_DISABLED'):
                orchestrate(t,Path(t)/'out',env={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'})
if __name__=='__main__':unittest.main()
