import datetime as dt
import hashlib
import io
import json
import os
import types
from pathlib import Path
import unittest
from contextlib import redirect_stdout
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

    def test_incident_date_help_preserves_closed_day_range(self):
        self.assertEqual(m.parse_incident_day("2026-11-04", self.now), dt.date(2026,11,4))
        # Keep the existing parser behavior; zero padding was not the failure.
        self.assertEqual(m.parse_incident_day("2026-11-4", self.now), dt.date(2026,11,4))
        for value in ("2026-11-5", "2026-11-06", "2026-08-07"):
            with self.subTest(value=value), self.assertRaisesRegex(m.IncidentDayError, "DAY_NOT_CLOSED_OR_OUT_OF_RANGE"):
                m.parse_incident_day(value, self.now)
        self.assertEqual(m.parse_incident_day("2026-08-08", self.now), dt.date(2026,8,8))
        for value in ("2026-02-30", "not-a-date"):
            with self.subTest(value=value), self.assertRaisesRegex(m.IncidentDayError, "INVALID_INCIDENT_DAY"):
                m.parse_incident_day(value, self.now)

    def run_main_with_date_error(self, error):
        output = io.StringIO()
        with patch("sys.argv", ["incident", "--day", "private-input"]), \
                patch.dict("os.environ", {"LIVE_ENABLED":"false", "KIS_ALLOW_ORDER":"false"}), \
                patch.object(m, "parse_incident_day", side_effect=error), \
                patch.object(m, "read_reference") as read_reference, \
                patch.object(m, "fetch_page") as fetch_page, redirect_stdout(output):
            code = m.main()
        read_reference.assert_not_called()
        fetch_page.assert_not_called()
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["gate"], "DENY")
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(result["ledger_writes"], 0)
        self.assertFalse(result["unknown_order_released"])
        self.assertNotIn("private-input", output.getvalue())
        return result

    def test_cli_only_discloses_locally_generated_allowlisted_date_errors(self):
        for reason in m.DAY_REASONS:
            result = self.run_main_with_date_error(m.IncidentDayError(reason))
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["help"], m.DAY_HELP)
        for error in (ValueError("DAY_NOT_CLOSED_OR_OUT_OF_RANGE"),
                      RuntimeError("credential-secret"), m.IncidentDayError("credential-secret")):
            result = self.run_main_with_date_error(error)
            self.assertNotIn("reason", result)
            self.assertNotIn("help", result)
            self.assertNotIn("credential-secret", json.dumps(result))

    def test_cli_failure_stages_are_fixed_and_never_expose_external_errors(self):
        for failed in ("READ_CREDENTIALS", "CONNECT_DATABASE", "READ_INCIDENT_REFERENCE",
                       "READ_TOKEN_CACHE", "READ_BROKER_HISTORY"):
            with self.subTest(failed=failed):
                credentials = Mock(return_value=("key", "secret", self.account, self.product))
                connection = MagicMock()
                connect = Mock(return_value=connection)
                reference = Mock(return_value=self.reference)
                cached = Mock(return_value=("token", self.now))
                broker = Mock(return_value=("D", {"rt_cd":"0", "output1":[]}))
                targets = {"READ_CREDENTIALS":credentials, "CONNECT_DATABASE":connect,
                           "READ_INCIDENT_REFERENCE":reference, "READ_TOKEN_CACHE":cached,
                           "READ_BROKER_HISTORY":broker}
                targets[failed].side_effect = RuntimeError("credential-secret-private-account")
                modules = {
                    "psycopg":types.SimpleNamespace(connect=connect),
                    "kis_buying_power_readonly":types.SimpleNamespace(credentials=credentials),
                    "kis_live_v4_5_2":types.SimpleNamespace(_read=cached, _key=lambda: "cache-key", UTC=dt.timezone.utc),
                }
                output = io.StringIO()
                with patch.dict("sys.modules", modules), \
                        patch.dict("os.environ", {"LIVE_ENABLED":"false", "KIS_ALLOW_ORDER":"false", "SUPABASE_DB_URL":"private-url"}), \
                        patch("sys.argv", ["incident", "--day", "2026-11-04"]), \
                        patch.object(m.dt, "datetime", wraps=dt.datetime) as clock, \
                        patch.object(m, "read_reference", reference), \
                        patch.object(m, "fetch_page", broker), redirect_stdout(output):
                    clock.now.return_value = self.now
                    self.assertEqual(m.main(), 2)
                if failed not in ("READ_CREDENTIALS", "CONNECT_DATABASE"):
                    self.assertIs(connection.__enter__.return_value.read_only, True)
                result = json.loads(output.getvalue())
                self.assertEqual(result["failure_stage"], failed)
                self.assertNotIn("reason", result)
                self.assertEqual(result["ledger_writes"], 0)
                self.assertEqual(result["orders_sent"], 0)
                self.assertFalse(result["unknown_order_released"])
                for secret in ("credential-secret", self.account, self.reference, "private-url", "cache-key"):
                    self.assertNotIn(secret, output.getvalue())
                if failed != "READ_BROKER_HISTORY":
                    broker.assert_not_called()

    def test_cli_allowlisted_evidence_error_has_no_date_hint(self):
        result = self.run_main_with_date_error(m.IncidentEvidenceError("TOKEN_CACHE_MISSING"))
        self.assertEqual(result["reason"], "TOKEN_CACHE_MISSING")
        self.assertNotIn("help", result)

    def test_observation_requires_explicit_dispatch_and_is_read_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/oneil_v519_incident_history.yml").read_text()
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("[incident-ro-20261104-once-44]", workflow)
        for guard in ("github.ref == 'refs/heads/main'",
                      "INCIDENT_DAY: ${{ github.event_name == 'push' && '2026-11-04' || inputs.day }}",
                      "contents: read", "LIVE_ENABLED: 'false'", "KIS_ALLOW_ORDER: 'false'",
                      'python oneil_v519_incident_history.py --day "$INCIDENT_DAY"'):
            self.assertIn(guard, workflow)
        for forbidden in ("--execute", "--activate", "--recover", "--migrate", "token_bootstrap", "contents: write"):
            self.assertNotIn(forbidden, workflow)


@unittest.skipUnless(os.environ.get("INCIDENT_TEST_DB_URL"), "isolated PostgreSQL integration service only")
class ReadOnlyTransactionIntegrationTests(unittest.TestCase):
    def test_explicit_transaction_overrides_writable_server_default(self):
        import psycopg
        with psycopg.connect(os.environ["INCIDENT_TEST_DB_URL"]) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SHOW transaction_read_only")
                self.assertEqual(cur.fetchone(), ("on",))
            with self.assertRaises(psycopg.errors.UndefinedTable):
                m.read_reference(conn, dt.date(2026, 11, 4))

    def test_unconfigured_transaction_is_rejected_before_table_read(self):
        import psycopg
        with psycopg.connect(os.environ["INCIDENT_TEST_DB_URL"]) as conn:
            with self.assertRaisesRegex(m.IncidentEvidenceError, "READ_ONLY_REQUIRED"):
                m.read_reference(conn, dt.date(2026, 11, 4))

    def test_postgres_rejects_write_in_explicit_read_only_transaction(self):
        import psycopg
        with psycopg.connect(os.environ["INCIDENT_TEST_DB_URL"]) as conn:
            conn.read_only = True
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("CREATE TABLE forbidden_write_probe (id integer)")

if __name__ == "__main__": unittest.main()
