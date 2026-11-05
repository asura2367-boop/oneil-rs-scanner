# Durable execution v2: isolated simulation prototype

**Not a live release. Not activated or deployed.** This additive prototype starts
from upstream `d7b4c38e8386190ab81bed9b67681147b045bf47`. It does not modify the
existing scanner, strategy, dashboard, database, schedules, credentials, or account
state. No legacy execution code is imported. No broker HTTP transport exists.

## Why this is a structural rebuild

GitHub Actions is build/test infrastructure, not the order queue or low-latency
execution scheduler. The new boundary is:

1. Authenticated control API records one immutable owner-confirmed request and
   returns its durable request ID immediately.
2. PostgreSQL owns the queue, request states, content hash, authorization expiry,
   control revision, attempt fence, cumulative fills, audit events, and health.
3. One long-lived worker per account reconciles broker outcomes first, validates
   fresh risk/session evidence, and admits at most one submission attempt.
4. Dashboard reads request state and timestamps directly from the control service.
   It must never infer broker acknowledgment from a CI result or display ACK as fill.

The current transport-independent facade is `SimulationAPI`. A production HTTP
service, dashboard integration, KIS adapter, and service deployment are **not yet
implemented**. The simulator is deliberately the only accepted broker type; the
schema rejects every mode except SIMULATION and `PostgresStore` accepts only
explicit databases named `oneil_v2_*`. These are multiple guardrails, not a
substitute for completing the production safety review.

## Implemented components

- `domain.py`: immutable exact-content orders and approval receipts, integer KRW
  policy, venue-bound quote/session evidence, account freshness, position/cash/
  gross/daily-buy/daily-loss limits, and broker identity/fill validation. Strict policy admission additionally rechecks
  the imported strategy, allocation, managed/sellable holdings and explicit calendar.
- `schema.sql`: additive private schema; immutable request content and attempt
  fences; legal state-transition and fill-regression triggers; append-only events.
- `store.py`: durable per-request queue, atomic owner idempotency, short validation
  leases, row locks and dedicated session account lock, durable attempt reservation,
  conditional pre-send cancellation, reconciliation, heartbeat, and read model.
  Admitted policy requests atomically retain immutable original/current evidence.
- `worker.py`: long-lived bounded polling loop, reconciliation before new attempts,
  final atomic admission checks, no transport retry, explicit ambiguity halt.
- `broker.py`: network-free simulator including acknowledgment, rejection, partial
  fills, full fills, lost responses, and process-crash scenarios. Economics are
  partitioned by account and applied once per cumulative-fill delta.
- `api.py`: server-neutral simulation owner control facade. Identity here is a
  trusted server-side type, **not production authentication**.

## Safety invariants and failure semantics

| Event | Durable result and next action |
|---|---|
| Repeated click with same account/key/content/principal | Return same request ID |
| Same key with changed order or principal | Conflict; no new request |
| Expired/invalid individual request | EXPIRED/BLOCKED; other queued requests survive |
| Worker dies during validation | Lease can expire and a new claim token may retry validation |
| Old validation worker resumes | Token/lease check prevents admission |
| Attempt committed, process dies before broker call | SUBMISSION_UNKNOWN; no automatic resend |
| Broker accepted, response lost | SUBMISSION_UNKNOWN and account halt; reconcile exact identity |
| Broker returns ACK | ACKNOWLEDGED, filled quantity zero, not FILLED |
| Partial execution | PART_FILLED; cumulative fill only increases; no fresh request |
| Complete history has no matching order | UNKNOWN stays unknown; absence is not proof of no send |
| Ambiguous, incomplete, stale, or inconsistent history | Halt and fail closed |
| Balance timestamp fresh but not reflecting earlier fills | Block on causal fill watermark |
| Owner halt before admission | Admission fails atomically |
| Owner halt after admission | No new admissions; already admitted call may finish |
| Cancellation before admission | Atomically CANCELLED, no broker effect |
| Cancellation after admission | Refused by local API; needs reviewed broker cancel flow |
| Lost queue notification | Durable polling still finds request |
| DB unavailable | No in-memory fallback; committed unknown remains reserved |

The guarantee is **at most one submission attempt for a durable request**, not
exactly one execution. KIS does not provide the simulator's request-ID association
as a guaranteed idempotency primitive. A real adapter must never invent such a
link. Unknown outcomes can remain blocked indefinitely until trustworthy evidence
is available. There is intentionally no "force retry" or timeout-to-rejected path.

A safety halt is never automatically cleared after reconciliation. Explicitly
resuming simulation increments the control revision, invalidating older queued
approvals. Resume is unavailable while an unresolved order exists.

The session lock is held during broker reads and simulated submission. Each worker
owns a **dedicated connection in its creating thread**. Reentrant lock acquisition
on that connection is rejected; other connections use PostgreSQL advisory locks.
Transaction-pooling proxies are incompatible. Database-trigger checks supplement,
but do not replace, app permissions or account execution ownership.

## Policy and strategy boundaries

No trading limits are guessed or silently changed. Every simulated account
requires an explicit validated `RiskPolicy`. Tests use small synthetic figures,
not the user's real policy. Existing O'Neil selection, stop-loss, pyramiding,
averaging, allocation, and venue preferences remain in the old system unchanged.
The isolated `oneil_policy_v2` importer and planner now feed a strict policy
admission gate, but no current production settings have been imported. Every
normal Worker requires a `SimulationPolicySource`, with exact original intent,
full current policy and explicit enriched account/managed/calendar/control evidence.
Missing evidence blocks; no namespace choice or generic core order enables a
fallback. See `../oneil_policy_v2/ADMISSION.md`. Original core-only tests explicitly
opt into `core_fixture_only=True`, which rejects policy-v2 orders and must never
be used in a runtime factory.

BUY checks include current available cash, order limit, daily committed notional,
position value, account gross exposure, and whole-account daily loss. A separately
approved SELL remains possible at the loss threshold, but never above available
shares. Daily notional uses conservative full committed amounts, even after
rejection/cancellation; it does not silently refund risk capacity.

The core does not prove P&L completeness, cash settlement eligibility, reserved
shares for external orders, or exchange-calendar correctness on its own. The
future broker adapter must provide those facts from complete broker evidence.
The current simulator is a simplified test accounting model, not a market model
or validated trading-strategy backtest.

## KIS venue and clock contract

No exchange eligibility is inferred merely from a weekday/time check. A
`SessionPermit` binds venue, KST trading day, explicit opening/closing instants,
order code, verification source, and freshness. Quotes bind the exact venue and
must be executable. Boundary instants and KST dates are rechecked during atomic
admission. The simulation fixture's rolling 60-second window and
`SIMULATED_LIMIT` code are **not KRX or NXT market hours**.

Before a live adapter exists:

- Verify current official KIS/KRX/NXT API capabilities, session windows, order
  codes, holiday calendar, exceptional closures, and ticker eligibility.
- Distinguish KRX and NXT rather than rerouting from the wall clock.
- Validate ticks, limit-order constraints, KIS account/trading-day/order identity,
  pagination completeness, rate limits, token expiry, and read timeout bounds.
- Use read-only bounded retries when safe; never retry an order POST.
- Correlate acknowledged IDs exactly. An ID-less unknown may require owner-led
  reconciliation rather than an inferred match on symbol/quantity/time alone.

## Intended HTTP/UI contract (not implemented)

A future server adapter must authenticate the owner, map permitted account
fingerprints on the server, enforce CSRF/origin/session expiry, rate-limit calls,
and reject oversized/unknown fields. Never accept `AuthenticatedOwner`, an
approval object, account authorization, or a boolean "approved" directly from
browser JSON. Approval must follow the human's exact displayed order details.

- `POST /v2/requests`: human-confirmed exact order + stable Idempotency-Key.
  Returns 202 `{request_id, state, mode, live_capable}` after DB commit.
- `GET /v2/requests/{id}`: account-scoped receipt and full state history.
- `POST /v2/requests/{id}/cancel`: pending-only cancellation; conflict once admitted.
- `GET /v2/status`: server timestamp, halt/control revision, worker heartbeat and
  staleness, last complete reconciliation timestamp, request list, and live gate.
- `POST /v2/halt`: optimistic control revision; safe stop, not broker cancellation.
- Simulation resume remains explicitly labeled. Production enablement is absent.

The UI must distinguish strategy enabled, worker alive, broker reconciliation
fresh, currently permitted session, account halted, owner authorization, and
request outcome. `worker_alive` currently means heartbeat within 15 seconds,
not proof of broker connectivity. Never show a stale snapshot as current.

Network retries may repeat a request only with the same idempotency key/content.
Do not let double clicks create new keys, clear pending edits on polling, or make
status polling a side effect that dispatches/authorizes an order. API serialization
must turn UUID/datetime objects into JSON strings; the facade returns Python types
in the status model and is not an HTTP server.

## Verification

Run network-free unit tests:

```sh
python -m unittest discover -s tests/execution_v2 -p test_domain.py -v
python -m unittest discover -s tests/execution_v2 -p test_simulator.py -v
python -m unittest discover -s tests/execution_v2 -p test_worker.py -v
```

Single-user PostgreSQL validation (no service, sockets, or network):

```sh
python tools/validate_execution_v2_sql.py --postgres-bin /path/to/postgresql/bin
```

Full integration suite on an explicitly authorized isolated local environment:

```sh
python tools/run_execution_v2_tests.py --postgres-bin /path/to/postgresql/bin
```

The full harness requires installed `psycopg` and PostgreSQL binaries. It creates
and destroys its own temporary cluster, uses a private Unix socket, rejects TCP
authentication, and never reads existing production connection variables. It
fails rather than accepting skipped integration tests. Do not point it at a
production database or use it to circumvent a prohibited socket operation.

Current validation: 42 network-free tests passed (21 domain, 8 simulator, and 13
worker-orchestration tests using a non-durable scripted store); PostgreSQL 16 single-user schema,
queue syntax, lifecycle, heartbeat upsert, and 15 assertions passed. The 37
psycopg/concurrency tests were written but **not executed** because this workspace
rejects Unix socket creation, including the approved escalation attempt. Thus
transaction/concurrency behavior is reviewed and covered by test cases, not yet
empirically proven. A default unittest discovery run lists those 37 as skipped;
that is not a full pass. No GitHub CI workaround was used.

## Production acceptance gates still open

1. Run every real-PostgreSQL test, including concurrent workers/clicks, lease fencing,
   crash windows, halt races, duplicate broker IDs, policy/approval invalidation,
   and DB disconnect during commit. Extend process-kill and network-fault tests.
2. Implement production authentication/confirmation and reviewed KIS read adapter.
   Prove complete order history, balance/position/P&L coherence and causal freshness.
3. Integrate strategy outputs and exact existing user risk/venue settings without
   silent defaults; run recorded-data and shadow comparison against the old engine.
4. Deploy least-privilege runtime/migration roles, audit retention and backup,
   secrets management, supervisor restart behavior, rate limiting and health alerts.
5. Design one shared account execution-ownership fence for the old and new engines.
   This prototype's new schema/lock is **not** a cross-version production fence.
6. Reconcile the old ledger and broker account, preserve/export existing data,
   disable old execution ownership before new ownership, prove no overlap, and
   practice rollback without replaying unknown or already admitted requests.
7. Choose and authorize an always-on host/cost model. No paid service is provisioned.
8. Independent release review and explicit user-controlled live activation and
   financial actions. This work does not authorize or perform real trading.

Until these gates are closed, the old system remains unchanged and this branch is
an isolated development foundation, not a complete deployable trading system.

## Policy admission integration verification

On the isolated `rebuild/policy-worker-v2` worktree, the complete non-network
aggregate suite passed **293 Python + 29 Node tests, zero skips**. This includes
39 new strict admission/store-boundary cases and 59 existing policy cases, plus
core/control/monitor regressions. Independent review also ran the final
293 Python + 29 Node suite, with zero skips and no new blocking findings.

The updated single-user SQL validator and actual PostgreSQL integration suite
were **not executed** in this integration environment: neither `pg_config`,
PostgreSQL server binaries nor `psycopg` are available. No packages were installed,
no sockets/listeners were started, no prior socket denial was retried, and no
network/CI/alternate-environment workaround was used. Earlier baseline SQL results
above do not validate the new admission audit DDL. Durable concurrency and the new
DDL remain mandatory release gates.
