# Unknown BUY: operator-reviewed no-order resolution

This is a privileged recovery primitive, not an automatic recovery workflow. It has no CLI, network requests, broker order calls, or scheduled entry point. Do not invoke it from the scanner or from untrusted dashboard input.

## Evidence required

An empty screenshot, empty holdings, or an empty pending-order list cannot prove that an unknown submission was not accepted. Preserve the unresolved intent and emergency halt until a trusted operator has verified:

- The broker account and product map to the ledger's account fingerprint.
- The full trading day's order history covers all tickers, sides, venues and statuses, including cancelled and rejected orders, with every page retrieved.
- The KST trading day has ended and the broker confirms history finality. The code deliberately rejects same-day resolution; passing midnight alone is not proof of finality.
- No history row exists for the target ticker. Any such row blocks this negative reconciliation, regardless of side, status or quantity, and requires a separate matched-order investigation.
- The reviewed intent is still a zero-fill BUY in SUBMISSION_UNKNOWN with a null broker ID.

The original transport error may be unavailable. Do not invent it or infer rejection from the missing order ID. Main already captures sanitized diagnostics for future submission failures.

## Authorized operator procedure

1. Independently review and retain broker evidence privately. Do not commit account data or raw history. Obtain explicit approval for the specific ledger resolution; this code and its tests do not provide that approval.
2. Keep LIVE_ENABLED and KIS_ALLOW_ORDER false and the durable halt true. Do not activate a release.
3. Under separately approved database migration authority, install `AUDIT_SCHEMA` from `v519_no_order_review.py`. No migration is invoked automatically.
4. Construct the review object for `prepare_review` from trusted evidence. Its boolean fields are operator attestations, not machine verification of broker provenance. Bind account fingerprint, intent ID, trading day, reviewer identity, review time and a SHA-256 of the privately retained source evidence. Include complete history rows. Never derive attestations from a screenshot alone.
5. Under explicitly authorized database write access, call `record_no_order_review` with a `PgRuntimeLedger` on a direct/session connection. Use trusted server time. It revalidates the locked intent and requires the current review to be no more than 15 minutes old.
6. Verify the audit and terminal state after commit, plus the unchanged halt and original intent identity. A repeated attempt fails closed. Investigate an uncertain database outcome by reading the audit and intent, not blindly retrying.

The transaction inserts `OPERATOR_VERIFIED_NO_ORDER` audit evidence and changes the existing intent to REJECTED solely to use the ledger's existing terminal-state model. This label is an operator no-order resolution, **not a claim the broker rejected an order**. It does not set `verified_not_sent`, fabricate a broker ID, remove the intent, change its day, or enable retries. The daily unique BUY index and `bought_today` guard continue to include this row. The audit and status change roll back together on failure. Live activation and all orders remain separate and unauthorized by this procedure.

## Verification limits

Offline unit tests use fake transactions and cannot establish PostgreSQL permissions, migrations, transaction isolation, broker evidence authenticity or live account state. An authorized isolated PostgreSQL integration check and operator review are required before use. The module is included in the release fingerprint; do not update an approved release merely to deploy this helper.
