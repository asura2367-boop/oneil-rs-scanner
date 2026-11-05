# Operator-reviewed synchronous pre-send abort

Prepared offline; publication and CI do not execute a recovery or install an audit table. This path is separate from broker-history no-order review. The latter must continue rejecting same-day absence evidence.

## Narrow proof, not an error-string shortcut

This implementation is intentionally pinned to GitHub workflow run 41975997898, job 127075193914, source commit abf446b8af195684f5d3884e72a09706081a379e, exact engine blob f9dc1f6ce4329542a64de6071a81fa7b5f1b52da and exact workflow blob 8637f74f52a53acfed79b28027f5893bb774474c. Job ID uniquely identifies the execution even when no numeric attempt value is available. Do not invent an attempt number.

In that complete source, KisBroker.submit line 547 raises FRESH_EXECUTABLE_BOOK_REQUIRED before calling _send_verified at line 550. Cash-order transport is at line 566. The reviewed workflow refreshes only the encrypted token data file after checkout, not engine source. Its exact terminal stack and code must match both authenticated GitHub job logs and the finished ledger-run result. No request-start event for CASH_ORDER may appear. These facts are stronger than zero confirmed orders or an absent broker ID.

Raw bytes alone cannot authenticate GitHub origin. A trusted operator must retrieve and verify origin, immutable source, unchanged execution source and unique run/intent association. The generator validates pins and contents after that review; its booleans are explicit operator attestations, not fake automated signatures. Preserve the raw log and private source evidence outside this PUBLIC repository.

Binding must include account fingerprint, deterministic BUY intent identity, target day/ticker, created_at within the unique finished run, exact matching failure and temporal job result. The engine did not persist a direct workflow-run-to-ledger-run identifier in this incident; reject if any competing candidate prevents unique attribution. SQL rechecks both competing matching runs and all account reservations within the target run.

Never generalize this path to arbitrary TradingBlocked messages, HTTP/timeouts, POST attempts, invalid acknowledgements, or failed acknowledgement storage. The presence of a submission attempt counter does not mean a POST occurred; the pinned synchronous source and trace establish the boundary here. Same-day resolution is allowed only through this distinct proof, not by weakening final broker-history rules.

## Proposed operation

The module exposes no CLI, connection, scheduler or broker request. Its offline generator first validates evidence and explicit disabled order flags. It emits one atomic DO statement with the runtime account advisory-lock key, control and intent row locks, and a shared lock on the finished run. Current halt=true, zero-fill UNKNOWN BUY, null broker ID and verified_not_sent=false are mandatory. Run identity/result/timing, unique attribution and a fresh operator review of no more than 15 minutes are rechecked at execution.

A separately authorized migration must install v519_pre_send_reviews using AUDIT_SCHEMA first. It enables RLS, revokes PUBLIC and existing anon/authenticated grants, and records OPERATOR_VERIFIED_PRE_SEND_ABORT with source/run/job/log digests and truthful reviewer identity. It is not a broker rejection assertion. No schema changes occur in the generated recovery block.

The atomic recovery inserts one audit and updates only the target status to REJECTED and updated_at. It preserves verified_not_sent=false so no SELL retry semantics are borrowed, keeps the original daily BUY row/uniqueness, and changes neither halt nor activation. Any prior audit, concurrent lock, changed evidence/state, audit failure or zero-row update blocks and rolls back.

Before a specific production invocation, privately re-read and review the exact target, evidence, SQL and hash. Obtain separate authorization for the actual ledger disposition. After one invocation, independently verify the audit, unchanged identity/guard/halt/activation and all other intents. On uncertain outcome read first, never blindly retry. This change does not fix quote timing or enable trading; those are separate concerns.

## Testing

The committed fixtures are complete public source snapshots, not executed test modules and not user evidence. Synthetic tests cover source/workflow/provenance mismatches, message-only/post-wire failures, duplicate logs, account/ID/run ambiguity, fills/acknowledgements, stale review, same-day resolution, runtime-lock contention, atomic rollback, prior/repeated audit, untouched halt/activation/identity and daily uniqueness. CI provisions only a local disposable PostgreSQL service and receives no production secrets.
