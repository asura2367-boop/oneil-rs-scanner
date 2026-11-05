# KIS-to-policy offline composition boundary

**Offline diagnostics only. Not a live integration, permission, activation,
installation, deployment, or evidence that a real account is safe to trade.**

`kis_pipeline.py` composes the existing KIS account/market parsers and native
history parser into one explicit account/environment/day/ticker/venue
scope. It accepts only supplied transcripts, performs no network or database I/O,
and has no credential loader, transport callback, submit method or clock lookup.

## What the composition establishes

`inspect_kis_inputs(KisOfflineInputSet(...), now=..., max_age_seconds=...)` parses:

1. Complete supported balance traversal.
2. Portal mode-01 buying-power inquiry with blank wire price, not exact-price capacity.
3. Separate sellable-quantity inquiry.
4. Current-price observation.
5. Raw session-related fields.
6. Holiday records for the explicit target day.
7. Native KIS history through a strictly in-memory transcript consumer.

Every request recipe must match its expected account/product, environment,
ticker, venue, day, inquiry mode, filters and supported pagination cursor.
Mode-01 `inquiry_price_krw` must be None; a positive value is rejected rather than
reported as a response binding. All history
captures must be ordered, aware, fresh from the first page, and on the explicit
KST day. Missing/truncated history and extra supplied pages are rejected without
retaining partial history as a coherent account input. NXT history is unsupported;
there is no KRX fallback. Empty input is missing data, never zero cash/holdings or
an empty verified order history.

Cross-source checks report holdings disagreement, mismatched reported purchase
or valuation totals, nonpositive current prices, reported temporary stops and
reported calendar closures. Disagreements are never repaired by dropping rows,
choosing the more permissive endpoint or inventing a value. Matching sums and
quantities still do not establish broker snapshot isolation or fill causality.

`shape_complete=True` means only all seven supported transcript shapes parsed.
`policy_ready` and `live_capable` are always false, even with matching values.
`summary()` contains fixed endpoint/error/gap codes, not account numbers, raw
responses or financial amounts. Full typed observations remain sensitive internal
input and must not be serialized/shared just because their repr is redacted.

## Actual policy/worker failure path

`SimulationPolicySource.register_kis_offline(request_id, inputs, ...)` is a
fail-closed seam, not a successful admission API:

- It blocks the request ID and removes any old synthetic registration **before**
  parsing, including when inspection raises. A failed KIS refresh cannot leave a
  previously installed synthetic context available as a fallback.
- It returns the diagnostic report when parsing completes. Returning a report
  never means registered, approved or ready. There is no PolicyContext result.
- Subsequent `read()` and ordinary fixture `register()` for that ID raise
  `KIS_POLICY_EVIDENCE_NOT_ESTABLISHED`. There is no clear-block method.
- The normal Worker and the final PolicyAdmissionGate both read the same source;
  they reject before reserving an attempt. This also covers a source change after
  the initial worker read but before final admission. Other request IDs retain
  their separately installed synthetic evidence.

The block lives in the in-memory simulation source. It is not a durable incident,
production fence or a means to retract an already committed/in-flight attempt.
On restart a fresh source remains empty and cannot execute without independent
explicit reconstruction; reconstructing trusted fixture assertions is not proof
of KIS authenticity. Durable attempts, unknown outcomes, owner authorization,
reconciliation, final policy and runtime guards are unchanged.

## Facts this implementation cannot establish

All current KIS observations are unauthenticated. A request recipe and caller
capture time do not prove origin or broker as-of freshness. The report always
lists missing proof for authenticated account/response provenance, complete
cross-endpoint snapshot and fill causality, native-order/durable-request identity,
whole-account daily loss and commitments, managed ownership, all-stock sellable
shares, no-receivables power and final exact-order capacity, executable side/depth, authoritative
calendar and exceptional closures, session boundaries/order code, instrument and
side eligibility, and fresh policy/control/strategy inputs.

The report also retains explicit portal-versus-sample sellable and holiday
contract conflicts. Successful portal-profile parsing cannot resolve actual
response behavior. No-receivables capacity is not settled-cash-only.

In particular:

- Deposit totals and settlement observations are not available buying cash.
- Valuation P&L is not daily realized plus unrealized whole-account loss.
- Current trade price and raw session codes are not an executable quote or session.
- A holiday Y flag does not authorize a venue, instrument, side or order code.
- Complete page traversal and absence of history cannot resolve an UNKNOWN send.
- Native history order numbers are not invented local request IDs.
- No caller-supplied `verified=True` switch, override or made-up session/P&L value
  can turn these transcripts into an admitted order through this boundary.

Positive policy admission continues only in independent, explicitly synthetic
simulation fixtures. This work deliberately does **not** claim the missing real
KIS-to-policy integration is complete. Reviewed authoritative providers and actual
recorded-response validation are required before any genuine policy evidence can
be produced. Production authentication, shared legacy ownership fencing, Windows
supervision, real PostgreSQL durability/concurrency and live activation remain
separate open gates.

## Verification and packaging

`python tools/run_monitor_v2_tests.py` includes all KIS parser tests, this pipeline's
unit and scripted worker end-to-end cases, and all existing offline v2 regressions.
The bundle validator now requires the KIS evidence implementation, documentation,
fixtures, tests and this pipeline, and regression tests reject even hash-valid
bundles that omit them. No socket/listener attempt, prior-denial workaround,
external service, installation, production settings mutation or trade is needed.

The worker tests use a scripted in-memory store and the exact SimulatedBroker.
They prove the tested application failure paths, not real PostgreSQL transactions,
concurrency, Windows behavior, authenticated broker facts or financial readiness.
See [KIS evidence sources and limits](../oneil_execution_v2/KIS_EVIDENCE.md),
[native-history contract](../oneil_execution_v2/KIS_ADAPTER.md), and
[strict admission contract](ADMISSION.md).
