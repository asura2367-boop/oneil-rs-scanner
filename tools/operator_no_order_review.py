"""Operator-only local entry point. No scheduler, broker transport or activation.

Review and retain private evidence before using this. Default is read-only.
An explicit commit digest is required after preview and separate authorization.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v519_no_order_review import prepare_review, record_no_order_review
from v519_pg_ledger import LedgerBlocked, PgRuntimeLedger


def load_review(review_path, evidence_path):
    # Evidence is retained by the operator, never copied into logs or the repo.
    source = Path(evidence_path).read_bytes()
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    if not isinstance(review, dict) or review.get('source_evidence_sha256') != hashlib.sha256(source).hexdigest():
        raise LedgerBlocked('SOURCE_EVIDENCE_DIGEST_MISMATCH')
    return review


def execute_review(conn, review, *, env, commit_digest=None):
    # Require explicit switches; do not silently supply disabled values for caller.
    if any(env.get(k, '').strip().lower() != 'false' for k in ('LIVE_ENABLED', 'KIS_ALLOW_ORDER')):
        raise LedgerBlocked('EXPLICIT_DISABLED_SWITCHES_REQUIRED')
    ledger = PgRuntimeLedger(conn, review.get('account_fingerprint', ''))
    # Preview validation is a read-only transaction. Commit rechecks under the
    # existing helper's advisory, control-row and intent-row locks.
    with conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        controls = ledger.rows('SELECT halted FROM v519_control WHERE singleton=true')
        if len(controls) != 1 or controls[0]['halted'] is not True:
            raise LedgerBlocked('EMERGENCY_HALT_REQUIRED')
        rows = ledger.rows('SELECT * FROM v519_order_intents WHERE intent_id=%s AND account_fingerprint=%s',
                           (review.get('intent_id'), ledger.fingerprint))
        if len(rows) != 1:
            raise LedgerBlocked('INTENT_NOT_FOUND')
        now = conn.execute('SELECT clock_timestamp()').fetchone()[0]
        prepared = prepare_review(rows[0], review, now=now)
        if ledger.rows('SELECT intent_id FROM v519_no_order_reviews WHERE intent_id=%s', (review['intent_id'],)):
            raise LedgerBlocked('REVIEW_ALREADY_RECORDED')
    digest = prepared['evidence_sha256']
    if commit_digest is None:
        return dict(mode='READ_ONLY_PREVIEW', evidence_sha256=digest, orders_sent=0, gate='DENY')
    if commit_digest != digest:
        raise LedgerBlocked('EXPLICIT_REVIEW_DIGEST_REQUIRED')
    # Never rewrite attestations, reviewer identity, timestamp or evidence hash.
    now = conn.execute('SELECT clock_timestamp()').fetchone()[0]
    result = record_no_order_review(ledger, review['intent_id'], review, now=now, env=env)
    return dict(result, mode='COMMITTED', evidence_sha256=digest)


def validate_connection_url(url):
    parsed = urlparse(url)
    # Do not permit libpq query/service parameters to override endpoint identity.
    # Direct/session endpoints on other ports require a separately reviewed change.
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != 'postgresql' or not parsed.hostname or parsed.port != 5432
            or parsed.fragment or any(k != 'sslmode' for k in query)
            or ('sslmode' in query and query['sslmode'] != ['require'])):
        raise LedgerBlocked('DIRECT_OR_SESSION_DATABASE_URL_REQUIRED')
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', required=True, help='Private operator-attested review JSON')
    parser.add_argument('--evidence', required=True, help='Private source evidence bytes bound by review hash')
    parser.add_argument('--commit-reviewed-digest', help='Exact digest returned by preview; requires separate write authorization')
    args = parser.parse_args()
    review = load_review(args.review, args.evidence)
    url = os.environ.get('SUPABASE_DB_URL', '')
    validate_connection_url(url)
    if any(os.environ.get(k) for k in ('PGSERVICE', 'PGSERVICEFILE', 'PGHOSTADDR')):
        raise LedgerBlocked('DATABASE_ENDPOINT_OVERRIDE_FORBIDDEN')
    import psycopg
    with psycopg.connect(url, autocommit=True, sslmode='require', connect_timeout=8,
                         options='-c statement_timeout=10000 -c search_path=public') as conn:
        if conn.info.port != 5432:
            raise LedgerBlocked('DIRECT_OR_SESSION_DATABASE_URL_REQUIRED')
        result = execute_review(conn, review, env=os.environ, commit_digest=args.commit_reviewed_digest)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Any error after commit starts may have an uncertain outcome. Read the
        # audit and intent before retrying. Never print connection or account data.
        code = str(exc) if isinstance(exc, LedgerBlocked) else 'OPERATOR_REVIEW_FAILED_CHECK_DATABASE_BEFORE_RETRY'
        print(json.dumps(dict(gate='DENY', error=code)))
        raise SystemExit(2)
