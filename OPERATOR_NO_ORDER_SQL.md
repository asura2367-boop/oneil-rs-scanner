# Private operator SQL channel (not executed)

This is an offline generator for the same privileged no-order resolution as the reviewed Python helper. It is not a scheduled workflow or automatic recovery path. The generator has no database connection and the CI uses only synthetic local PostgreSQL data. Publication is not permission to execute against production.

## Evidence and authorization

Privately retain the exact user-reported broker-confirmation exchange and independently verified history result. Label the user report accurately; do not invent a broker case number, representative, call timestamp or direct broker document. Bind the actual intent and account fingerprint using a private read-only query. The operator must actually review the combined evidence and give each required attestation. Neither an empty query nor a code change creates broker finality or operator approval.

Use the current trusted operator identity and real review time. Keep source evidence, review JSON, generated SQL and account bindings out of this PUBLIC repository and its logs, inputs and artifacts. A source hash is integrity evidence, not authentication of the broker statement. Keep source bytes privately for later audit.

## Generator

Call tools.operator_no_order_sql.sql_for_review(intent, review, source_bytes, now=trusted_preview_time, env=explicit_disabled_flags). Default schema is public. It reuses prepare_review and verifies SHA-256 of the exact source bytes. It returns the SQL, SQL SHA-256 and the same review evidence SHA-256 used by the original helper. It does not update timestamps or attestations. Review the exact generated statement and its digest before authorizing execution.

All strings in the emitted block are validated or hex-encoded. The private source bytes are not included. Only intent ID, account fingerprint, reviewer and review timestamp are included from the review. Full rows and extra fields are omitted. These identifiers remain private despite hex encoding; hex is not encryption.

## Single atomic statement

After separate authorization, the existing connected Supabase SQL tool could submit the exact reviewed DO block privately. It does not require retrieving or transmitting database credentials. This is the same database already holding the intent, not a new third-party recipient.

The block takes a transaction-scoped advisory lock using the runtime account key, then locks the halt-control row and target intent. PostgreSQL transaction-scoped and session-scoped advisory locks conflict in the same namespace. It verifies current state, date, ticker, null broker ID, zero fills, verified_not_sent=false and fresh review time using the server clock after locks. No scheduled runtime can pass its account lock during this block.

It inserts one OPERATOR_VERIFIED_NO_ORDER audit record and changes only the target status to REJECTED and updated_at. REJECTED here is an operator terminal disposition, not a broker rejection assertion. A failure or affected-row mismatch rolls back both changes. The original identity/day/quantity remain, so daily BUY uniqueness remains. Halt and activation are neither readied nor changed. There is no broker request or order path.

There is no persistent procedure, new schema or credential grant. The only transaction-local setting is a five-second lock timeout. The SQL is a single PostgreSQL statement, so atomicity does not depend on the connector supporting multiple explicit BEGIN/COMMIT statements.

## Verify and uncertain outcomes

Immediately before execution, recheck halt and the exact intent/audit privately. Within 15 minutes of the review, submit the exact SQL once. Afterward independently read the audit and target row, unchanged control/activation and daily guard. A successful tool response without read-back is not sufficient to claim recovery. If the tool fails or times out, outcome may be uncertain: read first, never blindly retry.

This preserves the existing privileged-operator trust boundary. The offline generator validates the evidence; SQL rechecks mutable database state and time. Manually editing its output or bypassing the generator invalidates that reviewed procedure. Production suitability still depends on independent review and real isolated PostgreSQL tests; no production execution is part of preparation.
