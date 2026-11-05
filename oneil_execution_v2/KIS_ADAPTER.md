# KIS adapter boundary: offline recipes and read-only normalization

This is **not a live KIS integration**. It is an additive implementation alongside
an isolated simulation core. No KIS request, credential lookup, production database
read/write, order, deployment, or activation was performed for this work.
`Worker` still rejects every broker other than the exact `SimulatedBroker` type.
`KisReadOnlyAdapter.submit()` always raises `KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED`.

## Implemented

- Immutable, credential-free **offline request recipes** for KRX cash limit orders
  and a single-day recent order-history query. Recipes contain account identifiers,
  so they must not be logged or sent without appropriate authorization. Their repr
  omits parameters, but that is not a general serialization/redaction guarantee.
- A trusted server-owned account+product fingerprint mapping, exact environment,
  side and venue validation, separate demo/real TR IDs, string numeric wire fields,
  and preservation of the caller's immutable order details.
- Strict cash-order response normalization. A well-formed success means only
  ACKNOWLEDGED. HTTP errors, API errors, malformed responses and transport ambiguity
  remain SUBMISSION_UNKNOWN. No automatic resend or rejection guess exists.
- A GET-only transport protocol and a bounded pagination orchestrator, exercised
  only with mocks. All filters stay unchanged across pages. Both returned cursor
  values are carried forward with `tr_cont=N`; repeated/padded cursors, truncation,
  duplicate native IDs and malformed pages return explicit incomplete results.
- Case-insensitive field handling with collision rejection. Numeric strings are
  parsed exactly, with no float conversion, rounding, exponent or sign acceptance.
- Separate broker-reported fill, remaining, cancel-confirmed and rejected quantities,
  original-order reference and cancellation flag. No terminal state is inferred.
- Exact history-ID lookup only within account+product fingerprint, environment,
  KST date, venue, history branch and broker order number. Leading zeros survive.
  Missing or incomplete history returns no resolution, never proof of no send.

The immutable recipe object contains a POST method label for protocol testing but
has no host, credentials or executable write transport. No production code imports
this module. There is no token issuer, automatic refresh, HTTP client or cancel API.

## Facts verified against the official sample source

Reviewed on 2026-11-05. Main sample snapshot:
`koreainvestment/open-trading-api`, commit
`d8c7f7936793f85dc1e9c364ba5dc66832353f8c`.

- Cash order: `/uapi/domestic-stock/v1/trading/order-cash`; real BUY `TTTC0012U`,
  real SELL `TTTC0011U`, demo equivalents `VTTC0012U` / `VTTC0011U`.
  Quantity and price must be strings; POST body keys are uppercase.
- Recent history: `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`;
  real `TTTC0081R`, demo `VTTC0081R`. Page limits are 100 real / 15 demo.
- The current history sample explicitly uses M/F for more pages. The official
  legacy domestic-stock implementation for the same endpoint explicitly uses
  D/E for the last page. Missing/blank/unknown headers are not accepted as complete.
- History request supports a venue filter. This implementation deliberately only
  permits KRX; NXT is blocked rather than rerouted. Complete NXT/SOR eligibility,
  order modes and session rules require a separate review.
- The official display mapping spells one venue key `excg_id_dvsn_Cd`. Casing is
  normalized, but an absent or unexpected venue value is not guessed from clock,
  ticker or query parameters.

Sources:

- [Current cash-order request sample](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/order_cash/order_cash.py)
- [Cash-order response mapping](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/order_cash/chk_order_cash.py)
- [Current history sample](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py)
- [History response mapping](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_daily_ccld/chk_inquire_daily_ccld.py)
- [Official legacy terminal-page handling](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/legacy/Sample01/kis_domstk.py)
- [Forwarding organization definition](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)

## Sample pitfalls deliberately not copied

1. Current history sample recursion passes positional arguments in a different
   order from its signature, shifting filters after the first page. This module
   reconstructs all parameters by name and changes only the cursor fields.
2. Its recursion-depth exit returns partial rows without a completeness flag.
   This module retains partial observations with `complete=False` and a reason.
3. A non-M/F header alone does not prove traversal completion. Only D/E are accepted.
4. Cash ACK `KRX_FWDG_ORD_ORGNO` is a forwarding organization, while history
   `ord_gno_brno` is an order-numbering branch. The samples do not prove they are
   equal. A `CashAcknowledgement` therefore cannot manufacture a `NativeOrderId`.
5. Cash ACK reports a time, not an order date. `trading_day_context` describes
   the admitted attempt's context, not a broker-asserted date.
6. Field names do not establish an accounting conservation equation for amended
   orders. Quantities are preserved individually; cancellation flag alone is not
   full cancellation, and zero remaining quantity alone is not a full fill.
7. Symbol, side, price, quantity and nearby time are not a unique client-request
   identity. Neither the request body nor ACK supplies a guaranteed client
   idempotency key. An ID-less unknown remains blocked.

## Completeness and trust boundaries

`NativeHistory.complete` means this bounded traversal reached a documented terminal
page and every row passed syntactic/scope validation. It does **not** assert:

- that a recently submitted order has already appeared in the broker's database;
- a snapshot-isolated view across pages or an account-wide scan across venues/days;
- causal consistency of cash, positions, P&L and fills;
- correct terminal-state interpretation of revised/cancelled orders;
- authorization or readiness to trade.

The timestamp is captured **before the first page**, so a slow multipage traversal
is not relabeled fresh when its last page returns. A future live read transport
must validate request/response provenance, enforce timeouts, rate limits, payload
limits and authentication, and recheck freshness before any consequential use.
The adapter itself does not retry even reads. It redacts exception text in generic
transport failures because those messages can contain identifiers or credentials.

History queries use the recent endpoint with a conservative maximum age of 89
calendar days in KST. Older dates are refused, not silently redirected. This is a
local supported-subset limit, not a claim that KIS defines three months as 89 days.
The normalizer intentionally requires explicit venue and numeric evidence; real
payloads with a different shape will fail closed until their contract is reviewed.

## Next acceptance gates

Before any live use, obtain authoritative schema evidence and sanitized recorded
fixtures for ACK-to-history identity, order revisions, terminal state transitions,
venue fields, account/product binding and KST midnight/session boundaries. Implement
and test exact mapping persistence across ACK-save failure. An unprovable mapping
must remain UNKNOWN and require a reviewed reconciliation procedure.

Then independently implement quote freshness, available-to-sell quantities,
settlement/cash eligibility, account P&L and fill watermarks, exchange holiday and
session eligibility, tick/order-code checks, credential isolation, bounded GET
transport and rate limiting. None is inferred or implemented by this boundary.
A production submission transport, shared old/new engine execution fence,
production confirmation and deployment/live-activation review remain separate gates.

## Verification

```sh
python -m unittest discover -s tests/execution_v2 -p test_kis_adapter.py -v
```

38 synthetic, network-free tests pass. They cover wire recipes, ACK versus fill,
uncertain outcomes, redaction, pagination/filter stability, malformed data, repeated
cursors, limits, duplicate IDs, exact lookup, date/venue isolation, retained raw
quantities and refusal of live worker registration/submission. A test forbids socket
creation while exercising the mock flow. These tests do not prove real KIS payloads
or production connectivity. No actual KIS read or order was sent.

## Offline recovery architecture

The additive [recovery model](RECOVERY.md) now rehearses exact signed synthetic
identity/observation recovery and both-engine ownership handover. It accepts no
raw KIS output as authenticated proof and implements no live mapping or transport.
All actual KIS and legacy production acceptance gates above remain open.

## Offline account and market observations

The additive [account/market evidence boundary](KIS_EVIDENCE.md) parses supplied
synthetic balance, buying-power, sellable, quote, session-code and calendar data.
It has no transport and cannot create live policy evidence or a session permit.
Deposit, orderable cash, valuation P&L and sellable quantities stay distinct.
Unverified response completeness, exchange timestamps and session semantics remain
explicit gaps; no production acceptance gate above is waived.
