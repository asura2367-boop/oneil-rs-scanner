import unittest, datetime as dt
from zoneinfo import ZoneInfo
from oneil_v519_session_guard import phase, safety_status
KST=ZoneInfo("Asia/Seoul")
def at(h,m): return dt.datetime(2026,10,28,h,m,tzinfo=KST)
class TestSessions(unittest.TestCase):
 def test_krx_regular(self): self.assertEqual(phase("KRX",at(14,30)),"REGULAR_WINDOW_ONLY")
 def test_krx_gap(self): self.assertEqual(phase("KRX",at(15,45)),"CLOSED_OR_UNVERIFIED")
 def test_krx_evening(self): self.assertEqual(phase("KRX",at(16,40)),"EVENING_WINDOW_ONLY")
 def test_nxt_evening(self): self.assertEqual(phase("NXT",at(16,40)),"EVENING_WINDOW_ONLY")
 def test_nxt_after_1540(self): self.assertEqual(phase("NXT",at(15,40)),"EVENING_WINDOW_ONLY")
 def test_nxt_break(self): self.assertEqual(phase("NXT",at(15,25)),"CLOSED_OR_UNVERIFIED")
 def test_nxt_pre(self): self.assertEqual(phase("NXT",at(8,20)),"PREMARKET_WINDOW_ONLY")
 def test_closed_20(self): self.assertEqual(phase("KRX",at(20,0)),"CLOSED_OR_UNVERIFIED")
 def test_unknown_venue(self): self.assertEqual(phase("SOR",at(17,0)),"UNKNOWN_VENUE")
 def test_no_order_even_when_all_flags_verified(self):
  x=safety_status("KRX",at(17,10),business_day_confirmed=True,ticker_eligible=True,
   executable_book_attested=True,buying_power_venue_verified=True)
  self.assertEqual(x["gate"],"DENY");self.assertEqual(x["orders_sent"],0)
 def test_unverified_business_day(self):
  self.assertIn("EXCHANGE_BUSINESS_DAY_UNVERIFIED",safety_status("NXT",at(17,0))["block_codes"])
 def test_broker_power_unverified(self):
  self.assertIn("VENUE_SPECIFIC_BUYING_POWER_UNVERIFIED",safety_status("KRX",at(17,0))["block_codes"])
