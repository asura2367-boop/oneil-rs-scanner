# Strict simulation policy admission

This is an isolated, network-free integration, not a live release, strategy
authorization, production settings migration or a deployment. Only the exact
`SimulatedBroker` type is accepted by `Worker`. No account, credential or broker
connection is added.

## Construction and admission contract

1. Import a narrow, explicit settings export and all required runtime constraints.
   `TradingPolicy.core_risk` must be the account's registered durable risk policy.
   No settings or risk fields are inferred. Every effective setting affects the
   content-derived policy version.
2. Supply `PolicyContext`: full policy, whole-account enriched evidence, controls
   and durable control revision, complete broker-history digest, causal fill
   watermarks, candidate batch where needed, and explicit market, calendar,
   capability and technical evidence. All unresolved orders must be reconciled.
3. Use the pure planner to create one exact simulation `Proposal`. Retain its
   original context. The proposal always remains unauthorized and sends nothing.
4. Obtain the existing exact simulated-owner confirmation through the control
   service/API and enqueue the immutable order. The service's authentication,
   approval and idempotency requirements are not bypassed by policy evidence.
   Manual additions must use `policy-manual-<ManualAdd.request_id>` as their
   durable account-scoped idempotency key. A second key for the same manual intent
   fails admission, and the original key conflicts if order content changes.
5. Register `PolicyAdmission(proposal, context, mode, manual, original_context)`
   in an explicit `SimulationPolicySource` for the returned durable request ID.
   Only `AUTOMATIC` and `MANUAL_ADD` exist. Manual exit remains unimplemented.
   Registration is trusted internal simulation setup, not a browser endpoint.
   Repeated registration can refresh context but cannot rewrite the original
   proposal, mode, manual intent or original context.
6. Install the explicit market/calendar context on the simulator. Its independently
   initialized balance/holdings must reconcile exactly with enriched quantities,
   valuations and equity. Installing context never resets cash, fills or ownership.
7. Start the normal Worker with `policy_source=source`. There is no permissive
   default. On restart, reconcile durable history first and reinstall verified
   intent/evidence explicitly; missing in-memory registrations block new attempts.
   The offline `SimulationRuntime` can restore immutable original intent from its
   explicit archive with separately supplied current contexts. It never restores
   current health, activation, broker balances or owner authorization.

The standalone `oneil_control_v2.SimulationPolicyCoordinator` now composes these
steps for authenticated, exact `policy_order` previews/confirmations with a strict
Worker. Its trusted host supplies evidence and plans proposals; no browser field
becomes trusted evidence and no proposal is automatically confirmed. The core
bridge refuses raw orders. See `../oneil_control_v2/POLICY_COORDINATOR.md`. An explicit
started SimulationRuntime can also be bound; its independent owner activation,
health, runtime-first ownership locks and immutable intent archive remain required.
An owner command without a registered bundle still cannot execute under the default
worker, and HTTP confirmation never activates the runtime.

## Mandatory final checks

The store invokes the exact `PolicyAdmissionGate` under its account/control/request
row locks. A generic callback, `None`, or a hand-built audit dictionary cannot admit
a policy-v2 request through `reserve_attempt`. The gate is bound to the exact store,
request and order digest. It re-reads the registered current context at this boundary.

- Original exact order, policy content/version, mode, reason and buffered cost.
- Current durable control revision and halt, plus explicit fresh policy controls.
- Complete actual broker history and enriched history digest/reference agreement.
- Causal fill-watermark agreement with actual broker snapshot and durable fills.
- All held symbols and total quantities/market values, including unmanaged stock.
- Conservative maximum of cost, valuation and higher broker-reported exposure.
- Explicit sellable/managed quantity, managed-lot provenance and average cost.
- Deposit-derived reserve/allocation, cash and broker power, book size, stock count,
  core risk ceilings and the legacy fee buffer, without shrinking the confirmed order.
- Full fee-buffered same-day durable BUY commitments, including terminal rejected
  or cancelled attempts. All committed/broker-history BUY symbols join bought_today.
- Candidate/session provenance, exit priority, independent buy/profit/stop switches,
  verified business day, venue, instrument, side, order code and session interval.
- Exact broker quote/session agreement and every evidence expiry at admission.

Replanning must produce exactly the original economics. Fresh observations may
not extend the original order lifetime. Changed quantity, limit, symbol, side,
venue, policy, reason or shorter validity blocks and requires a new exact proposal
and owner approval. A generic non-policy order cannot avoid strict default Worker
validation because the complete TradingPolicy version must still match.

The explicit `core_fixture_only=True` flag retains the old synthetic core tests,
not a runtime fallback. It rejects the policy-v2 namespace and cannot be combined
with a policy source. Do not enable it in service factories or deployment scripts.

## Durable evidence and failure behavior

The same SQL update that commits `SUBMISSION_UNKNOWN` and attempts=1 saves original
and current policy/evidence, effective buffered daily exposure, broker observations,
request/digest/revision and validation timestamp in `admission_evidence`. The audit
becomes immutable with the attempt. The schema also rejects policy-v2 attempts
without matching simulation audit identity metadata. This SQL constraint checks
metadata, not the strategy semantics implemented by the typed gate.

A database commit/response failure never permits retransmission. Missing history,
unknown outcomes, partial fills, stale evidence, changed settings and owner halt
retain the prior fail-closed behavior. A full simulated sell now removes the
zero-share holding instead of presenting it as an open position. After any fill,
old enriched evidence no longer agrees with broker quantities/cash/watermarks;
it must be explicitly refreshed before a later proposal can be admitted.

## Trust and verification limits

These typed values and `verified=True` flags are assertions from a trusted offline
fixture/provider. Deposit basis, sellable/managed ownership, acquisition cost,
broker buying power, calendar authority and candidate cohort completeness are not
independently authenticated by the simpler core snapshot. The gate rejects
missing/inconsistent evidence; it does not turn invented assertions into proof.
A real broker/calendar/identity adapter remains an unresolved production gate.

Python application code, SQL migration access and a process able to rewrite class
methods remain trusted. The gate prevents accidental callable bypass, not hostile
code execution. Runtime database roles, cross-engine account ownership, restart
reconstruction, production authentication, authoritative market facts and all
live activation are still separate release requirements.

Run `python tools/run_monitor_v2_tests.py` for all network-free regressions. The
policy/store tests use scripted in-memory connections; they do not prove actual
PostgreSQL commits, locking or concurrency. The updated socket-free validator can
be run only where PostgreSQL binaries already exist. No denied listener operation
may be retried or worked around. No real orders, production settings, schedules,
credentials, paid services, GitHub publication or live activation were touched.

## KIS transcript composition fails closed

The optional `SimulationPolicySource.register_kis_offline` entrypoint now composes
seven supplied KIS transcript streams for diagnostics. It blocks that request ID
before parsing and invalidates any prior synthetic registration; even successful
parsing cannot create a PolicyContext. Missing authenticated provenance, session,
eligibility, P&L, managed ownership and causal account proof remain explicit. The
normal Worker and final gate reject this source before an attempt. See
[KIS_PIPELINE.md](KIS_PIPELINE.md) for the exact scope and test limitations.
