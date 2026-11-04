import copy
import datetime as dt
import unittest
from contextlib import contextmanager
from unittest.mock import Mock

from v519_no_order_review import prepare_review, record_no_order_review, KST
from v519_pg_ledger import LedgerBlocked, PgRuntimeLedger, RUNTIME_SCHEMA


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 5, 10, tzinfo=KST)
        self.intent = dict(intent_id='a'*64, account_fingerprint='b'*64,
                           trading_day=dt.date(2026, 11, 4), ticker='000500',
                           status='SUBMISSION_UNKNOWN', side='BUY', broker_order_id=None,
                           filled_quantity=0, verified_not_sent=False)
        self.history = dict(intent_id='a'*64, account_fingerprint='b'*64,
                            trading_day='2026-11-04', reviewer='operator',
                            source_evidence_sha256='c'*64, reviewed_at=self.now.isoformat(), rows=[])
        for key in ('account_mapping_verified', 'all_statuses', 'all_sides', 'all_tickers',
                    'all_venues', 'all_pages', 'broker_finality_verified', 'operator_approved'):
            self.history[key] = True

    def test_valid_review_digest(self):
        result = prepare_review(self.intent, self.history, now=self.now)
        self.assertEqual(len(result['evidence_sha256']), 64)
        self.assertEqual(result, prepare_review(self.intent, copy.deepcopy(self.history), now=self.now))

    def test_each_attestation_required_and_strict(self):
        for key, value in self.history.items():
            if value is True:
                for invalid in (False, None, 1, 'true'):
                    with self.subTest(key=key, invalid=invalid), self.assertRaises(LedgerBlocked):
                        prepare_review(self.intent, {**self.history, key: invalid}, now=self.now)

    def test_identity_date_and_audit_fail_closed(self):
        for key, value in [('account_fingerprint', 'd'*64), ('intent_id', 'e'*64),
                           ('trading_day', '2026-11-03'), ('reviewer', ''),
                           ('source_evidence_sha256', ''), ('reviewed_at', '2026-11-05'),
                           ('reviewed_at', (self.now-dt.timedelta(minutes=16)).isoformat()),
                           ('reviewed_at', (self.now+dt.timedelta(seconds=1)).isoformat())]:
            with self.subTest(key=key), self.assertRaises(LedgerBlocked):
                prepare_review(self.intent, {**self.history, key: value}, now=self.now)

    def test_current_day_and_invalid_state_blocked(self):
        for key, value in [('trading_day', self.now.date()), ('status', 'ACKNOWLEDGED'),
                           ('status', 'REJECTED'), ('side', 'SELL'), ('broker_order_id', '123'),
                           ('broker_order_id', ''), ('filled_quantity', 1),
                           ('filled_quantity', False), ('verified_not_sent', True)]:
            with self.subTest(key=key), self.assertRaises(LedgerBlocked):
                prepare_review({**self.intent, key: value}, self.history, now=self.now)

    def test_any_ticker_order_blocks_even_rejected_sell(self):
        history = {**self.history, 'rows': [dict(pdno='000500', ord_dt='20261104',
                                                side='SELL', status='REJECTED')]}
        with self.assertRaisesRegex(LedgerBlocked, 'BROKER_ORDER_PRESENT'):
            prepare_review(self.intent, history, now=self.now)

    def test_malformed_and_wrong_day_rows_block(self):
        for rows in (None, [None], [{}], [dict(pdno='005930', ord_dt='20261103')]):
            with self.subTest(rows=rows), self.assertRaises(LedgerBlocked):
                prepare_review(self.intent, {**self.history, 'rows': rows}, now=self.now)

    def ledger(self, halted=True):
        ledger = Mock(fingerprint='b'*64)
        ledger.rows.side_effect = [[{'halted': halted}], [self.intent]]
        ledger.events = []
        ledger.conn.execute.return_value.fetchall.return_value = [('a'*64,)]
        @contextmanager
        def lock():
            ledger.events.append('locked')
            try: yield
            finally: ledger.events.append('unlocked')
        @contextmanager
        def transaction():
            ledger.events.append('begin')
            try: yield
            except Exception:
                ledger.events.append('rollback')
                raise
            else: ledger.events.append('commit')
        ledger.lock = lock
        ledger.conn.transaction = transaction
        return ledger

    def test_atomic_audit_and_resolution_keep_halt_and_guard(self):
        ledger = self.ledger()
        result = record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env={})
        self.assertEqual(ledger.events, ['locked', 'begin', 'commit', 'unlocked'])
        calls = ledger.conn.execute.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('INSERT INTO v519_no_order_reviews', calls[0].args[0])
        self.assertIn("SET status='REJECTED',updated_at=now()", calls[1].args[0])
        self.assertNotIn('verified_not_sent', calls[1].args[0])
        self.assertEqual(result['orders_sent'], 0)
        self.assertEqual(result['gate'], 'DENY')
        self.assertIn("WHERE side='BUY'", RUNTIME_SCHEMA)
        # Terminal resolution does not remove the ticker from bought_today.
        runtime = object.__new__(PgRuntimeLedger)
        runtime.fingerprint = 'b'*64
        runtime.rows = Mock(return_value=[{'ticker': '000500'}])
        self.assertEqual(runtime.bought_today(self.intent['trading_day']), {'000500'})
        self.assertNotIn('status', runtime.rows.call_args.args[0])

    def test_audit_failure_rolls_back_before_resolution(self):
        ledger = self.ledger()
        ledger.conn.execute.side_effect = RuntimeError('audit unavailable')
        with self.assertRaises(RuntimeError):
            record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env={})
        self.assertEqual(ledger.conn.execute.call_count, 1)
        self.assertIn('rollback', ledger.events)

    def test_filtered_update_rolls_back_audit(self):
        ledger = self.ledger()
        ledger.conn.execute.return_value.fetchall.return_value = []
        with self.assertRaisesRegex(LedgerBlocked, 'NO_ORDER_RESOLUTION_NOT_APPLIED'):
            record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env={})
        self.assertIn('rollback', ledger.events)

    def test_update_failure_rolls_back_audit(self):
        ledger = self.ledger()
        ledger.conn.execute.side_effect = [Mock(), RuntimeError('update failed')]
        with self.assertRaises(RuntimeError):
            record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env={})
        self.assertIn('rollback', ledger.events)

    def test_halt_and_disabled_flags_required(self):
        for halted, env in [(False, {}), (True, {'LIVE_ENABLED': 'true'}),
                            (True, {'KIS_ALLOW_ORDER': 'true'})]:
            ledger = self.ledger(halted)
            with self.assertRaises(LedgerBlocked):
                record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env=env)
            ledger.conn.execute.assert_not_called()

    def test_missing_intent_blocks(self):
        ledger = self.ledger()
        ledger.rows.side_effect = [[{'halted': True}], []]
        with self.assertRaisesRegex(LedgerBlocked, 'INTENT_NOT_FOUND'):
            record_no_order_review(ledger, 'a'*64, self.history, now=self.now, env={})
        ledger.conn.execute.assert_not_called()


# Use only the isolated local PostgreSQL service supplied by CI, never live DB.
import os


@unittest.skipUnless(os.getenv('V519_TEST_DB_URL'), 'Local PostgreSQL test URL not supplied')
class PostgresReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uuid
        import psycopg
        from urllib.parse import urlparse
        from v519_pg_ledger import migrate_runtime
        from v519_no_order_review import AUDIT_SCHEMA
        cls.url = os.environ['V519_TEST_DB_URL']
        if urlparse(cls.url).hostname not in ('localhost', '127.0.0.1'):
            raise RuntimeError('TEST_DATABASE_MUST_BE_LOCAL')
        cls.schema = 'no_order_test_' + uuid.uuid4().hex
        cls.conn = psycopg.connect(cls.url, autocommit=True)
        cls.conn.execute('CREATE SCHEMA ' + cls.schema)
        cls.conn.execute('SET search_path TO ' + cls.schema)
        migrate_runtime(cls.conn)
        cls.conn.execute(AUDIT_SCHEMA)

    @classmethod
    def tearDownClass(cls):
        cls.conn.execute('DROP SCHEMA ' + cls.schema + ' CASCADE')
        cls.conn.close()

    def setUp(self):
        ReviewTests.setUp(self)
        self.conn.execute('TRUNCATE v519_no_order_reviews, v519_order_intents')
        self.conn.execute('UPDATE v519_control SET halted=true')
        self.conn.execute('''INSERT INTO v519_order_intents
            (intent_id,account_fingerprint,trading_day,ticker,side,venue,quantity)
            VALUES (%s,%s,%s,%s,'BUY','KRX',1)''',
            (self.intent['intent_id'], self.intent['account_fingerprint'],
             self.intent['trading_day'], self.intent['ticker']))
        self.ledger = PgRuntimeLedger(self.conn, self.intent['account_fingerprint'])

    def resolve(self):
        return record_no_order_review(self.ledger, self.intent['intent_id'],
                                      self.history, now=self.now, env={})

    def assert_unresolved_and_unaudited(self):
        self.assertEqual(self.ledger.pending()[0]['status'], 'SUBMISSION_UNKNOWN')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0], 0)
        self.assertTrue(self.ledger.halted())

    def test_commit_preserves_halt_null_id_and_daily_unique_guard(self):
        import psycopg
        self.assertEqual(self.resolve()['orders_sent'], 0)
        row = self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        self.assertEqual(row['status'], 'REJECTED')
        self.assertIsNone(row['broker_order_id'])
        self.assertFalse(row['verified_not_sent'])
        self.assertTrue(self.ledger.halted())
        self.assertEqual(self.ledger.bought_today(self.intent['trading_day']), {'000500'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0], 1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute('''INSERT INTO v519_order_intents
                (intent_id,account_fingerprint,trading_day,ticker,side,venue,quantity)
                VALUES (%s,%s,%s,%s,'BUY','KRX',1)''',
                ('d'*64, self.ledger.fingerprint, self.intent['trading_day'], '000500'))

    def test_repeated_resolution_fails_without_duplicate_audit(self):
        self.resolve()
        with self.assertRaises(LedgerBlocked):
            self.resolve()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0], 1)

    def test_zero_row_update_rolls_back_inserted_audit(self):
        self.conn.execute('''CREATE FUNCTION skip_review_update() RETURNS trigger AS $$
            BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql''')
        self.conn.execute('''CREATE TRIGGER skip_review_update BEFORE UPDATE ON v519_order_intents
            FOR EACH ROW EXECUTE FUNCTION skip_review_update()''')
        try:
            with self.assertRaisesRegex(LedgerBlocked, 'NO_ORDER_RESOLUTION_NOT_APPLIED'):
                self.resolve()
            self.assert_unresolved_and_unaudited()
        finally:
            self.conn.execute('DROP TRIGGER skip_review_update ON v519_order_intents')
            self.conn.execute('DROP FUNCTION skip_review_update()')

    def test_audit_failure_preserves_unknown_intent(self):
        import psycopg
        self.conn.execute('''ALTER TABLE v519_no_order_reviews ADD CONSTRAINT deny_test_review
            CHECK (reviewer <> 'operator')''')
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.resolve()
            self.assert_unresolved_and_unaudited()
        finally:
            self.conn.execute('ALTER TABLE v519_no_order_reviews DROP CONSTRAINT deny_test_review')

    def test_other_runtime_session_blocks_review(self):
        import psycopg
        with psycopg.connect(self.url, autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(%s)', (self.ledger.lock_key,))
            try:
                with self.assertRaisesRegex(LedgerBlocked, 'ACCOUNT_RUNTIME_ALREADY_RUNNING'):
                    self.resolve()
                self.assert_unresolved_and_unaudited()
            finally:
                other.execute('SELECT pg_advisory_unlock(%s)', (self.ledger.lock_key,))

    def test_unhalted_account_cannot_be_resolved(self):
        self.conn.execute('UPDATE v519_control SET halted=false')
        with self.assertRaisesRegex(LedgerBlocked, 'EMERGENCY_HALT_REQUIRED'):
            self.resolve()
        self.assertFalse(self.ledger.halted())
        self.assertEqual(len(self.ledger.pending()), 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
