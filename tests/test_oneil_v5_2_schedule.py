import unittest
import tempfile
import json
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from oneil_v5_2_schedule import run, session
KST=ZoneInfo("Asia/Seoul")
class TestSchedule(unittest.TestCase):
    def test_off(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"}):
                o=run(Path(d)/"missing",Path(d)/"missing.csv",datetime(2026,10,26,19,10,tzinfo=KST))
            self.assertEqual(o["status"],"SKIPPED_OFF")
            self.assertEqual(o["orders_sent"],0)
    def test_on_review_only(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            (d/"state.json").write_text(json.dumps({"schema_version":1,"analysis_enabled":True,"real_order_enabled":False,"orders_authorized":False}))
            (d/"c.csv").write_text("date,ticker,rs\n2026-10-26,052460,98\n")
            with patch.dict(os.environ,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"}):
                o=run(d/"state.json",d/"c.csv",datetime(2026,10,26,19,10,tzinfo=KST))
            self.assertEqual(o["candidates"],["052460"])
            self.assertFalse(o["market_data_live_verified"])
    def test_stale_no_trading(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            (d/"s.json").write_text(json.dumps({"schema_version":1,"analysis_enabled":True,"real_order_enabled":False,"orders_authorized":False}))
            (d/"c.csv").write_text("date,ticker,rs\n2026-10-23,052460,98\n")
            with patch.dict(os.environ,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"}):
                o=run(d/"s.json",d/"c.csv",datetime(2026,10,26,19,10,tzinfo=KST))
            self.assertEqual(o["reason"],"STALE_CANDIDATES")
    def test_fails_if_live_flag_on(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ,{"LIVE_ENABLED":"true","KIS_ALLOW_ORDER":"false"}):
                with self.assertRaises(RuntimeError):
                    run(Path(d)/"s",Path(d)/"c",datetime(2026,10,26,19,10,tzinfo=KST))
    def test_evening_session(self):
        self.assertEqual(session(datetime(2026,10,26,19,10,tzinfo=KST)),"EVENING_REVIEW")
        self.assertEqual(session(datetime(2026,10,26,20,1,tzinfo=KST)),"CLOSED")
if __name__=="__main__":unittest.main()
