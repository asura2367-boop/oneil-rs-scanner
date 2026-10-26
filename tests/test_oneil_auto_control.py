import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oneil_auto_control import switch, read
class Tests(unittest.TestCase):
    def test_default_off(self):
        with tempfile.TemporaryDirectory() as d:self.assertFalse(read(Path(d)/'absent.json')['analysis_enabled'])
    def test_on_no_order(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
            a=switch('on');self.assertTrue(a['analysis_enabled']);self.assertFalse(a['real_order_enabled']);self.assertFalse(a['orders_authorized'])
    def test_off(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):self.assertFalse(switch('off')['analysis_enabled'])
    def test_live_enabled_denied(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
            with self.assertRaises(RuntimeError):switch('on')
    def test_allow_order_denied(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'true'}):
            with self.assertRaises(RuntimeError):switch('on')
    def test_invalid_file_denied(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json';p.write_text('{"analysis_enabled":true,"real_order_enabled":true}')
            with self.assertRaises(RuntimeError):read(p)
if __name__=='__main__':unittest.main()
