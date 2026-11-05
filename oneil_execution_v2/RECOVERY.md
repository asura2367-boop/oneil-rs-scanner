# Offline exact-evidence recovery and ownership architecture

**Synthetic rehearsal only. No broker connection, order submission, production
credential, actual legacy change, worker activation, migration or deployment.**
This additive model begins at runtime commit `7f026d0`. It does not replace the
existing Worker, PostgreSQL ledger, RuntimeState or legacy gateway. Passing its
checks is not permission or readiness to trade.

## What is implemented

`recovery.py` implements a deterministic reducer whose only accepted source is
`FixtureEvidenceAuthority`. Receipts are HMAC-authenticated to an ephemeral test
issuer, exact coordinator audience, purpose, schema and short validity interval.
They establish **synthetic fixture provenance**, not KIS authentication. The issuer
is intentionally named Fixture, all attempts require `environment="fixture"`,
and accepted native identities use synthetic `demo` scope. No production verifier,
key loading/export, credential retrieval or trusted live evidence exists.

- A durable attempt starts SUBMISSION_UNKNOWN. There is no attempt reset, retry,
  timeout-to-rejection, negative-history resolution or symbol/size/time matching.
- A separate positive identity receipt binds request ID, attempt ID, immutable
  order digest, account, environment and KST day to the complete native identity:
  account, environment, day, venue, branch and order number. Leading zeros matter.
- Raw `NativeHistory`, `CashAcknowledgement` and booleans are not receipts.
  A KIS forwarding organization is never converted to a history branch.
- Observations require exact identity, complete traversal, the explicit synthetic
  semantic contract, non-future/current capture time and monotonically increasing
  sequence and cumulative fills. An exact duplicate is idempotent. A conflicting
  duplicate, stale sequence, regressing fill or contradictory terminal result blocks.
- ACK is zero fill. Partial fill stays unresolved and preserves cumulative fill.
  Full fill requires exact total quantity. Synthetic cancellation preserves earlier
  fill and requires less than the full quantity; rejection requires zero fill.
  These are **fixture semantics**, not a discovered interpretation of KIS fields.
- Terminal status and quantity are immutable. A late broker correction requires a
  separate reviewed procedure, not silently rewriting settled state.

## Durable offline integration

`oneil_runtime_v2/ownership.py` combines the reducer with a separate exclusively
created SQLite coordinator. Opening uses `mode=rw`; missing/version-mismatched/
corrupt state or a different issuer cannot silently initialize a healthy ledger.
The same test authority must remain available across a reopen. Real key rotation,
process restart key custody, backup/restore and hostile writers are not implemented.

All updates in one recovery batch are validated before any transition is written.
A bad later row or duplicate native identity rolls the whole batch back and records
an account halt. Native identity is unique across stored attempts. A separate collision key also
blocks ambiguous reuse of zero-padding variants; it is never used to match
identities or settle an order. Attempt IDs and
request IDs cannot be reused through the API. Audit events are append-only through
SQL triggers. The journal is not tamper-proof against an owner of the file.

Successful recovery always leaves the account halted. Partial/unknown/ACK states
block both new fixture reservations and ownership handover. After a terminal result,
resume additionally needs the explicit trusted synthetic owner and a fresh signed
account snapshot for the exact local generation, with exact cumulative fill
watermarks and no external unresolved orders. Positive fill requirements survive
terminal cancellation and file reopen. This is not cash, settlement, P&L or risk
validation and must not replace the existing strict policy admission gate.

## Legacy/new exclusion model

One coordinator account has one holder, a random process incarnation, an increasing
epoch and a monotonically changing generation. Both `LEGACY_SIMULATION` and
`V2_SIMULATION` use the same file and scope. Scope holds `BEGIN IMMEDIATE` throughout
the caller's offline operation, so another connection cannot acquire ownership in
the middle. It is a cooperative model, not a broker-side fencing token.

Handover requires a signed fixture receipt bound to the current generation, exact
previous holder/incarnation/epoch, exact destination engine, a pre-pinned boundary
manifest covering both participants, exact stopped-holder proof, separate legacy
shutdown evidence matching a current legacy holder's process incarnation, and complete empty-unresolved broker and legacy snapshots.
The local ledger must independently have no unresolved attempts. A stale lease,
expired health or empty local queue never proves the old process stopped. Missing
legacy proof, a changed incarnation, an unrecognized boundary manifest or replayed
receipt blocks. Ownership is never stolen on a timeout.

Handover creates a new epoch but stays halted. Its coverage is short-lived and is
checked at every scope and fixture reservation, rejecting times before the
attested coverage interval. The caller supplies a trusted clock; this coordinator
does not detect rollback within that interval. There is no implicit coverage renewal, automatic resume or unsafe
release API. A persistence failure while latching a halt poisons the current
coordinator object, preventing further admission through it.

The reservation helper commits exactly one UNKNOWN before returning and performs
no effect. There is deliberately **no dispatch or submit method**. It demonstrates
recovery/crash semantics only. The future real implementation must place ownership
and the authoritative core attempt reservation in one transaction boundary. It
must not mirror a live request into this separate SQLite journal and claim atomicity.

## KIS and actual legacy blockers remain open

The current KIS sample does not prove ACK forwarding organization equals history
branch, a guaranteed client idempotency association, or the complete semantics of
cancelled/amended/rejected quantities. Recent traversal completeness also does not
prove a just-submitted order has appeared. Therefore real KIS observations cannot
be promoted by this synthetic model. The existing read-only adapter and
`Worker`'s exact-SimulatedBroker restriction are unchanged.

No actual legacy entrypoint consults this coordinator. Before production, inventory
and enforce every real submission boundary; reconcile the real broker/legacy
ledgers; independently prove old process incarnations and scheduling entrypoints
are disabled/drained; validate an authoritative shared store and fail-closed
permissions; and rehearse crashes, split-brain and rollback without replay. No
current claim of real cross-version mutual exclusion is made.

Authenticated live capture, reviewed KIS identity/terminal semantics, account causal
consistency, production owner authentication, real-PostgreSQL concurrency, Windows
power-loss/disk-full behavior, authoritative restart custody and independent live
release review remain mandatory gates. The prior PostgreSQL socket denial was not
retried or bypassed. No alternate environment or CI was used for it.

## Verification

Run the focused suites or complete offline aggregate:

```sh
python -m unittest discover -s tests/execution_v2 -p test_recovery.py -v
python -m unittest discover -s tests/runtime_v2 -p test_ownership.py -v
python tools/run_monitor_v2_tests.py
```

Tests exercise synthetic signed evidence, field/content tampering, issuer/audience/
purpose isolation, exact identities, stale/future captures, replay, partial fills,
terminal conflicts, atomic batch rollback, duplicate broker IDs, file reopen,
causal fill proof, both-engine contention, exact process exits, stale epochs,
missing legacy evidence, append-only audit and persistence-failure latching.
The integrated path is tested with socket creation forbidden. SQLite concurrency
here is same-host connection contention, not proof of production PostgreSQL or
Windows service behavior.


Validation on this isolated change: **412 Python + 29 Node tests passed, zero
skips**. Independent review reran **20 recovery + 24 ownership tests** and confirmed
three repaired edge cases: contradictory account evidence now halts an already
active fixture account; legacy shutdown incarnation must match the current legacy
holder; and ambiguous zero-padding native-ID reuse is rejected. No remaining
blocking finding was identified within the explicitly synthetic, trusted-clock,
cooperative single-file model. These checks do not close any live acceptance gate.
