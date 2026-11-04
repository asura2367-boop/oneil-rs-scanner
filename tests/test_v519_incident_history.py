import datetime as dt
import hashlib
import unittest
from unittest.mock import Mock, MagicMock, patch
from urllib.parse import parse_qs, urlparse
import oneil_v519_incident_history as m
from oneil_v519_history_observe import fetch_page

class IncidentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.account, self.product = "12345678", "01"
        self.reference = hashlib.sha256(b"12345678|01").hexdigest()
        self.now = dt.datetime(2026, 11, 5, tzinfo=m.KST)
        self.provider = Mock(return_value=("D", {"rt_cd":"0", "output1":[]}))

    def run_observe(self, **kw):
        args = dict(day="20261104", now=self.now, account=self.account,
                    product=self.product, reference=self.reference, provider=self.provider, env={})
        args.update(kw)
        return m.observe(**args)

    def test_closed_day_empty_is_not_reconciliation(self):
        r = self.run_observe()
        self.assertTrue(r["account_matches_incident"])
        self.assertTrue(r["pagination_complete"])
        self.assertEqual(r["rows_observed"], 0)
        self.assertFalse(r["unknown_order_released"])
        self.assertFalse(r["independent_reconciliation_verified"])
        self.assertEqual(r["gate"], "DENY")
        self.assertNotIn(self.reference, str(r))
        self.assertNotIn(self.account, str(r))

    def test_today_future_old_invalid_and_naive_never_call_broker(self):
        for kw in ({"day":"20261105"}, {"day":"20261106"}, {"day":"20260101"},
                   {"day":"20260230"}, {"now":dt.datetime(2026,11,5)}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.run_observe(**kw)
        self.provider.assert_not_called()

    def test_account_mismatch_and_enabled_flags_fail_before_broker(self):
        for kw in ({"reference":"0"*64}, {"reference":None}, {"product":"02"},
                   {"env":{"LIVE_ENABLED":"true"}}, {"env":{"KIS_ALLOW_ORDER":"true"}}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.run_observe(**kw)
        self.provider.assert_not_called()

    def test_midnight_kst_boundary(self):
        with self.assertRaises(ValueError):
            self.run_observe(now=dt.datetime(2026,11,4,14,59,59,tzinfo=dt.timezone.utc))
        self.assertTrue(self.run_observe(now=dt.datetime(2026,11,4,15,tzinfo=dt.timezone.utc))["day_closed"])

    def test_complete_pagination_beyond_old_ten_page_limit(self):
        pages = [("M", {"rt_cd":"0","output1":[{}],"ctx_area_fk100":str(i),"ctx_area_nk100":str(i)}) for i in range(11)]
        pages.append(("D", {"rt_cd":"0","output1":[]}))
        self.provider.side_effect = pages
        r = self.run_observe()
        self.assertEqual((r["pages_observed"], r["rows_observed"]), (12, 11))
        self.assertEqual(self.provider.call_args_list[-1].args, ("20261104","10","10",True))

    def test_bad_response_cursor_loop_and_page_limit(self):
        cases = [("D", {"rt_cd":"1","output1":[]}), ("?",{"rt_cd":"0","output1":[]}),
                 ("D",{"rt_cd":"0","output1":{}}), ("M",{"rt_cd":"0","output1":[]}),
                 ("M",{"rt_cd":"0","output1":[],"ctx_area_fk100":"x","ctx_area_nk100":"y"})]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                m.collect(Mock(return_value=item),"20261104",2)
        pages=[("M",{"rt_cd":"0","output1":[],"ctx_area_fk100":str(i),"ctx_area_nk100":str(i)}) for i in range(2)]
        with self.assertRaisesRegex(ValueError,"PAGE_LIMIT"):
            m.collect(Mock(side_effect=pages),"20261104",2)

    def test_read_reference_requires_read_only_and_unique_incident(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ("on",)
        cur.fetchall.return_value = [(self.reference,)]
        self.assertEqual(m.read_reference(conn,dt.date(2026,11,4)), self.reference)
        self.assertTrue(all(c.args[0].startswith(("SHOW","SELECT")) for c in cur.execute.call_args_list))
        cur.fetchall.return_value = [(self.reference,), (self.reference,)]
        with self.assertRaises(ValueError): m.read_reference(conn,dt.date(2026,11,4))
        cur.fetchone.return_value = ("off",)
        with self.assertRaises(ValueError): m.read_reference(conn,dt.date(2026,11,4))

    def test_transport_is_get_all_statuses_all_venues(self):
        response = Mock()
        response.headers.get.return_value = "D"
        response.read.return_value = b'{"rt_cd":"0","output1":[]}'
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            fetch_page("key","secret",self.account,self.product,"token","20261104","","",False)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method,"GET")
        self.assertTrue(urlparse(request.full_url).path.endswith("/inquire-daily-ccld"))
        params = parse_qs(urlparse(request.full_url).query)
        for key,value in (("SLL_BUY_DVSN_CD","00"),("CCLD_DVSN","00"),("EXCG_ID_DVSN_CD","ALL")):
            self.assertEqual(params[key],[value])

if __name__ == "__main__": unittest.main()
