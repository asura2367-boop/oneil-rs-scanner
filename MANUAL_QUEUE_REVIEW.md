# Owner additional-buy queue reliability (draft, not activated)

## Behavior

- Eligible owner requests are considered after exits and before automatic new-position buys, without spending their available cash on automatic candidates first.
- Request-local failures or an already-recorded request no longer hide later queue entries. Global control/account/recovery failures stop the queue. The run stops after one manual acknowledgement; unknown submission outcomes halt and never retry.
- Newly confirmed requests may have an explicit lifetime up to 40 minutes, still bounded by the same KST day and supported execution session. Existing request timestamps are not changed. The lifetime remains 40 minutes after shortening the nominal schedule to 5 minutes; actual scheduling delays may still prevent execution.
- Exact owner quantity, maximum price, holding quantity, buy/risk revisions, account and approved release remain bound. Their control/approval binding and expiry are checked again inside the final send guard.
- Durable PostgreSQL daily/ticker reservation, unresolved-order blocking, account lock, risk limits and halt remain unchanged. A fresh dashboard request ID cannot evade the engine's daily BUY key.

## Dashboard companion required

The dashboard needs matching confirmation text and TTL for newly created requests. Its queue-only day/ticker unique index can be replaced with atomic exclusion of unexpired same-ticker requests and a 30-record daily transport cap. Never mutate or extend an expired request; require a newly confirmed ID and retain the old record. An expired request is not evidence that an order was never sent. The engine ledger remains authoritative.

The dashboard companion and generated migration are prepared separately, not applied by this PR. Until updated, the dashboard continues creating valid 20-minute requests. No live database change, deployment, activation, halt clearing, risk change or trading dispatch is authorized by this patch.

## Verification

- Local engine suite: 227 tests, 70 PostgreSQL tests skipped because no isolated local PostgreSQL service was configured.
- Recovery/cash/runner regression suites: 57 tests, 6 PostgreSQL skips; root durable-submit/pipeline suite: 4 passed.
- Added real PostgreSQL CI tests for queue progress, commit-before-wire, unresolved-order stop and fresh-ID daily-key protection.
- Fixture integration tests cover manual-before-auto, blocked/recorded first entries, later success status, unrelated quote failure, sell control changing OFF, halt/unknown outcomes, control changes during send-guard acquisition, expiry during the send guard, exact quantity, and bounded old/new expiry.

PR CI must pass on the exact head before merge review. Main-branch workflows include live infrastructure: do not merge or deploy as part of this review. Source changes require the existing source review and owner activation process; this patch does not grant approval.


## Explicit owner-selected immediate dispatch

The existing trusted main workflow accepts `operation=manual-buy` with one `manual_request_id`, `approved_release`, and `approval_revision`. This is an explicit owner-click processing request, not a promise of immediate execution: all source tests, PostgreSQL integration tests, shared session preparation, account concurrency and market/expiry gates still run.

The new CLI mode `--manual-buy --manual-request-id UUID --execute` requires the exact owner, triggering owner, main workflow/ref/event, release fingerprint and current immutable approval record. It does not scan, enter the integrated/automatic cycle, activate an account, clear a halt, send an exit, or select a different request. Under the existing account lock it reads full current-day history, recovers known orders, then obtains fresh controls/account state and processes only the selected immutable UUID. Approval and settings are checked again before reservation and inside the send guard. Missing, duplicate, expired, changed or already-recorded requests never become automatic fallback trades.

Manual KIS buys now read a final executable quote once after slow checks and reservation, inside the send guard. The final price must equal the reserved price exactly and its depth must cover the exact quantity. No repricing or quantity change is allowed. The original 3-second freshness, 1% spread, venue/session and owner expiry limits remain unchanged. A quote failure, price/depth change, or expiry/session change at this pre-POST step is a typed unsent rejection that retains the durable daily BUY key. Order POST is never retried. A submit/ack uncertainty remains unknown even if the secondary halt write also fails.

Private request UUIDs are masked before entering any shell-step environment; reports contain no UUID, ticker or account data. Per-request checks remain in the private run record/encrypted dashboard. Broker acknowledgement and fill/recovery are distinct states. Snapshot capability `manual_immediate_supported` signals that this isolated path exists.

Added regression coverage includes exact selection, no automatic/exit/activation calls, owner/event/source/revision fail-closed checks, immutable request changes, unconditional history/recovery, settings/halt/limits, quote latency and changes, expiry/session boundaries, diagnostic/halt failures, and real PostgreSQL mutual exclusion, reserve-before-wire, duplicate/daily keys and uncertain outcomes. Branch CI must pass on the exact head before review. No live dispatch or activation is part of validation.
