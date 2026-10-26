import unittest
import tempfile
import datetime as dt
from pathlib import Path
from unittest.mock import patch
import kis_live_v4_6 as mod

class RiskTests(unittest.TestCase):
    def test_unknown_pnl_denied(self):
        self.assertEqual(mod.daily_loss_check(verified=False,realized=0,unrealized=0),'DAILY_LOSS_NOT_VERIFIED')
    def test_loss_limit(self):
        self.assertEqual(mod.daily_loss_check(verified=True,realized=-11000,unrealized=-9000),'DAILY_LOSS_LIMIT_REACHED')
    def test_below_loss_limit(self):
        self.assertEqual(mod.daily_loss_check(verified=True,realized=-12000,unrealized=-1000),'DAILY_LOSS_LIMIT_NOT_REACHED')
    def test_bad_loss_values(self):
        self.assertEqual(mod.daily_loss_check(verified=True,realized=float('nan'),unrealized=0),'DAILY_LOSS_INVALID')
    def test_gate_always_denies(self):
        self.assertFalse(mod.build_report({'halted':False})['approved'])
    def test_halt_sticks(self):
        now=dt.datetime(2026,10,26,tzinfo=dt.timezone.utc)
        x=mod.latch_halt({'version':1,'halted':False,'halted_at_kst':None},now)
        self.assertIs(x,mod.latch_halt(x,now))
        self.assertIn('EMERGENCY_HALT_ACTIVE',mod.build_report(x)['block_codes'])
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'halt.json'; data={'version':1,'halted':False,'halted_at_kst':None}
            mod.atomic_write(p,data)
            self.assertEqual(mod.read_halt(p),data)
    def test_corrupt_halt_denied(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'halt.json';p.write_text('bad')
            with self.assertRaises(mod.RiskError):mod.read_halt(p)
    def test_switches_off(self):
        with patch.dict('os.environ',{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):mod.require_off()
        with patch.dict('os.environ',{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
            with self.assertRaises(mod.RiskError):mod.require_off()
