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


if __name__ == '__main__':
    unittest.main()
