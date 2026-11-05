# Operator-only no-order runner (prepared, not deployed)

Run from a private trusted workstation with a direct/session PostgreSQL connection. Do not add this to a scheduler or dashboard. No broker API credentials are required. No live execution is authorized by this document.

Keep two private files outside version control:
- Source evidence: exact user question/answer reporting KIS customer-service confirmation, its actual message reference if available, the verified closed-day history run URL/source commit and complete observation result, account/intent binding. Label the confirmation `user_reported_broker_confirmation`. No direct broker document, representative, case number or broker confirmation timestamp has been supplied; do not invent them.
- Operator review JSON: existing helper's full required fields, reviewed by an identified trusted operator. `source_evidence_sha256` is SHA-256 of the exact retained evidence bytes. Include `rows: []` only for the verified complete zero-row history. The current operator supplies their own actual review time, not a fictitious broker call time. All true attestations require actual review; the runner never generates them.

The current evidence is GitHub run 41965423199 at source e57823a07e13200a8f23c75da26d7ef7bfaa6599, observed 2026-11-05T13:04:26.241035+09:00 for trading day 2026-11-04. It reports matched account, one completed page, zero rows, ALL statuses/venues and no orders/writes. Combine this with the exact user-reported KIS confirmation and audited identity binding, not a screenshot alone.

Explicitly set LIVE_ENABLED=false and KIS_ALLOW_ORDER=false. Supply SUPABASE_DB_URL securely; never place credentials in shell history, source, command arguments, or output. Connection must use TLS and a direct/session endpoint, not transaction pooling. The runner requires explicit port 5432, permits only sslmode=require as a URL query parameter, rejects service/host-address overrides, and checks the connected port. An operator must still verify endpoint mode; port 5432 alone does not prove session pooling.

1. Run `python tools/operator_no_order_review.py --review /private/review.json --evidence /private/evidence.json`. This defaults to a read-only transaction and emits a review digest, without raw identities or evidence. Read-only preview still requires access to the existing audit table.
2. Parent/operator reviews the proposed single-intent resolution and grants specific write authorization. A correct digest does not supply that authorization.
3. Within the helper's 15-minute review freshness limit, run the same command with `--commit-reviewed-digest <exact-preview-digest>`. Do not change review fields between preview and commit. The helper rechecks under account and row locks. No automatic timestamp refresh occurs.
4. Read back the audit and intent and verify unchanged halt, activation, identity, broker ID, verified_not_sent and daily BUY guard. Do not enable trading.

Only commit writes: one OPERATOR_VERIFIED_NO_ORDER audit row and the matching intent's status=REJECTED plus updated_at. REJECTED is the existing terminal status for this operator resolution, not a statement that the broker rejected an order. The original intent remains and its daily uniqueness still applies. Halt/activation are not changed. No migrations are performed.

If execution fails or connection drops, read audit and intent before any retry. A commit might already have succeeded. Do not blindly rerun. The local test dependency directory and all evidence files must remain uncommitted.
