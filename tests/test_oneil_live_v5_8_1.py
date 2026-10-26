import json, tempfile, unittest, time
from pathlib import Path
from oneil_live_v5_8 import verify_broker_reports
class EvidenceTests(unittest.TestCase):
    def test_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            x=verify_broker_reports(Path(td),time.time())
            self.assertEqual(x['preflight'],'MISSING')
            self.assertEqual(x['reconciliation'],'MISSING')
    def test_rejects_insecure_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); p=root/'data/live/v4_5_preflight.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'orders_sent':1,'mode':'READ_ONLY_PREFLIGHT_V4_1','live_enabled':False,'decisions':[]}))
            self.assertEqual(verify_broker_reports(root,time.time())['preflight'],'INVALID')
if __name__=='__main__':unittest.main()
