# Owner additional-buy queue reliability (draft, not activated)

## Behavior

- Eligible owner requests are considered after exits and before automatic new-position buys, without spending their available cash on automatic candidates first.
- Request-local failures or an already-recorded request no longer hide later queue entries. Global control/account/recovery failures stop the queue. The run stops after one manual acknowledgement; unknown submission outcomes halt and never retry.
- Newly confirmed requests may have an explicit lifetime up to 40 minutes, still bounded by the same KST day and 15:15 cutoff. Existing request timestamps are not changed. This accommodates the nominal 20-minute schedule but does not guarantee execution if scheduling is delayed.
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
