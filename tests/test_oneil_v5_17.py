import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_live_v5_17 import parse_frame, PREFIX, MIN_FIELD_COUNT
KST=ZoneInfo('Asia/Seoul')
NOW=dt.datetime(2026,10,27,14,50,3,tzinfo=KST)
def frame(tr='H0STASP0',time='145001',bid='1000',ask='1001',ticker='085910',extra=True):
    row=['0']*MIN_FIELD_COUNT
    for k,v in [('MKSC_SHRN_ISCD',ticker),('BSOP_HOUR',time),('ASKP1',ask),('BIDP1',bid),('ASKP_RSQN1','50'),('BIDP_RSQN1','40')]: row[PREFIX.index(k)]=v
    if not extra: row=row[:15]
    return '0|'+tr+'|1|'+'^'.join(row)
class Test(unittest.TestCase):
    def test_krx(self): self.assertEqual(parse_frame(frame(),{'085910'},NOW)['status'],'PLAUSIBLE_BOOK_UNATTESTED')
    def test_nxt(self): self.assertEqual(parse_frame(frame(tr='H0NXASP0'),{'085910'},NOW)['venue'],'NXT')
    def test_stale(self): self.assertEqual(parse_frame(frame(time='143000'),{'085910'},NOW)['status'],'STALE_OR_FUTURE_EXCHANGE_TIME')
    def test_cross(self): self.assertEqual(parse_frame(frame(bid='1100'),{'085910'},NOW)['status'],'CROSSED_BOOK')
    def test_unknown(self): self.assertEqual(parse_frame(frame(ticker='005930'),{'085910'},NOW)['status'],'UNKNOWN_SYMBOL')
    def test_short(self): self.assertEqual(parse_frame(frame(extra=False),{'085910'},NOW)['status'],'SCHEMA_COUNT_UNVERIFIED')
    def test_invalid(self): self.assertEqual(parse_frame(frame(ask='abc'),{'085910'},NOW)['status'],'INVALID_QUOTE_FIELD')
    def test_control(self): self.assertEqual(parse_frame('{}',{'085910'},NOW)['status'],'CONTROL_OR_UNKNOWN')
if __name__=='__main__': unittest.main()
