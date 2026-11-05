import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.operator_no_order_review import load_review, execute_review, validate_connection_url
from v519_pg_ledger import LedgerBlocked
import test_v519_no_order_review as base


class SourceTests(unittest.TestCase):
    def test_libpq_endpoint_override_rejected(self):
        for url in ('postgresql://host:5432/db?port=6543',
                    'postgresql://host/db?port=6543',
                    'postgresql://host:5432/db?host=other',
                    'postgresql://host:5432/db?service=pooled',
                    'postgresql://host:6543/db',
                    'postgresql://host:5432/db?sslmode=disable'):
            with self.subTest(url=url), self.assertRaises(LedgerBlocked):
                validate_connection_url(url)
        self.assertEqual(validate_connection_url('postgresql://host:5432/db?sslmode=require'),
                         'postgresql://host:5432/db?sslmode=require')

    def test_source_bytes_bound_without_fabricating_review(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / 'evidence'
            review = Path(folder) / 'review'
            evidence.write_bytes(b'user-reported broker confirmation, not direct broker record')
            expected = {'source_evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest(),
                        'broker_finality_verified': False, 'operator_approved': False}
            review.write_text(json.dumps(expected))
            self.assertEqual(load_review(review, evidence), expected)
            evidence.write_bytes(b'changed')
            with self.assertRaisesRegex(LedgerBlocked, 'SOURCE_EVIDENCE_DIGEST_MISMATCH'):
                load_review(review, evidence)


@unittest.skipUnless(os.getenv('V519_TEST_DB_URL'), 'Local PostgreSQL URL not supplied')
class OperatorPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.PostgresReviewTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):
        base.PostgresReviewTests.tearDownClass.__func__(cls)
    def setUp(self):
        base.PostgresReviewTests.setUp(self)
        self.now = self.conn.execute('SELECT clock_timestamp()').fetchone()[0]
        self.history['reviewed_at'] = self.now.isoformat()
        self.env = dict(LIVE_ENABLED='false', KIS_ALLOW_ORDER='false')
    def unchanged(self):
        base.PostgresReviewTests.assert_unresolved_and_unaudited(self)
    def run_review(self, review=None, commit_digest=None, env=None):
        return execute_review(self.conn, self.history if review is None else review,
                              env=self.env if env is None else env, commit_digest=commit_digest)
    def test_preview_has_no_writes(self):
        before = self.ledger.rows('SELECT * FROM v519_control')
        result = self.run_review()
        self.assertEqual(result['mode'], 'READ_ONLY_PREVIEW')
        self.unchanged()
        self.assertEqual(before, self.ledger.rows('SELECT * FROM v519_control'))
    def test_explicit_digest_commit_uses_audit_and_preserves_controls(self):
        before = self.ledger.rows('SELECT * FROM v519_control')
        activation = self.ledger.rows('SELECT * FROM v519_live_activation')
        old = self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        digest = self.run_review()['evidence_sha256']
        result = self.run_review(commit_digest=digest)
        self.assertEqual(result['mode'], 'COMMITTED')
        new = self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        self.assertEqual(new['status'], 'REJECTED')
        for key in old.keys() - {'status', 'updated_at'}:
            self.assertEqual(old[key], new[key])
        self.assertEqual(before, self.ledger.rows('SELECT * FROM v519_control'))
        self.assertEqual(activation, self.ledger.rows('SELECT * FROM v519_live_activation'))
        audit = self.ledger.rows('SELECT * FROM v519_no_order_reviews')
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['evidence_sha256'], digest)
        self.assertEqual(self.ledger.bought_today(self.intent['trading_day']), {'000500'})
        with self.assertRaises(LedgerBlocked): self.run_review(commit_digest=digest)
    def test_missing_flags_and_wrong_digest_do_not_write(self):
        for env in ({}, {'LIVE_ENABLED':'false'}, dict(self.env, KIS_ALLOW_ORDER='true')):
            with self.assertRaises(LedgerBlocked): self.run_review(env=env)
        with self.assertRaisesRegex(LedgerBlocked, 'EXPLICIT_REVIEW_DIGEST_REQUIRED'):
            self.run_review(commit_digest='f'*64)
        self.unchanged()
    def test_changed_review_invalidates_preview_digest(self):
        digest = self.run_review()['evidence_sha256']
        revised = dict(self.history, reviewer='different-operator')
        with self.assertRaisesRegex(LedgerBlocked, 'EXPLICIT_REVIEW_DIGEST_REQUIRED'):
            self.run_review(review=revised, commit_digest=digest)
        self.unchanged()
    def test_false_finality_and_stale_review_remain_blocked(self):
        for revised in (dict(self.history, broker_finality_verified=False),
                        dict(self.history, reviewed_at='2026-11-04T00:00:00+09:00')):
            with self.assertRaises(LedgerBlocked): self.run_review(review=revised)
        self.unchanged()

if __name__ == '__main__': unittest.main()
