import unittest, tempfile
from pathlib import Path
from unittest.mock import patch
import oneil_v519_live_runner as runner
from oneil_v519_order_recovery import OrderJournal

class Tests(unittest.TestCase):
    def test_existing_journal_prevents_new_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            j=OrderJournal(d/"orders.sqlite")
            j.prepare("a"*20,"144960","BUY","KRX",3)
            j.close()
            with self.assertRaises(runner.Blocked):
                runner.open_persistent_journal(d)
    def test_recover_requires_live_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(runner.os.environ,{"LIVE_ENABLED":"true","KIS_ALLOW_ORDER":"false"}):
                with self.assertRaises(runner.Blocked):
                    runner.recover_orders_readonly(Path(tmp))
    def test_missing_broker_data_cannot_clear_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=OrderJournal(Path(tmp)/"orders.sqlite")
            j.prepare("b"*20,"144960","BUY","KRX",2)
            self.assertEqual(len(j.pending()),1)
            j.close()
    def test_order_transmission_still_locked(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(runner.Blocked):
                runner.live_submit('t','k','s','12345678','01','144960',1,10000,'BUY')
            network.assert_not_called()
