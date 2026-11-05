# KIS account and market evidence: offline observations only

**Not a live integration or activation gate.** `kis_evidence.py` is a pure parser
and offline GET-recipe builder. It accepts caller-supplied transcripts, never
sends a request, reads credentials, opens a database/socket, or changes account
state. No production, policy planner, runtime factory or worker imports it.
`policy_evidence()` and `submit()` always raise `Blocked`.

The implementation starts at local base `3a017e8`. Official source was reviewed
2026-11-05 at `koreainvestment/open-trading-api` commit
`d8c7f7936793f85dc1e9c364ba5dc66832353f8c`. This is a pinned contract review, not a
claim that current live responses or exchange schedules were tested.

## What is implemented

- Typed, immutable request contexts, capture windows, parsed observations and
  outcomes for account balances, buying power, sellable shares, current price,
  raw session-related fields and holiday records.
- Exact request matching, including account/product, endpoint, environment TR ID,
  ticker, inquiry price/mode, venue selector and cursor/filter preservation.
- String-only exact integer/Decimal parsing. No float conversion, silent rounding,
  missing-number defaults, numeric coercion, duplicate-case key acceptance or
  partial-result promotion.
- Bounded supplied-page processing, cursor-loop checks including padded tokens,
  duplicate-record rejection, consistent balance totals across pages, explicit
  truncated/extra-page errors, and whole-result rejection on malformed evidence.
- Capture timestamps must be aware, ordered, not future, and fresh from the FIRST
  page start. Account/quote captures cannot cross their KST day context. Calendar
  queries can target a separate explicit date. The age limit is caller-supplied.
- Six typed fixture cases with **invented** values and placeholders, plus adversarial
  tests. Fixtures are not production recordings and do not validate live schemas.

`EvidenceOutcome.valid` means only that the supported offline response shape
parsed. Every observation has `live_eligible=False` and
`provenance_authenticated=False`. `OfflineRead` is not a provenance attestation;
the caller can forge its recipe, timestamps and body. Query account/environment,
requested venue and trading-day context are not claimed as broker-echoed facts.
Account identifiers are omitted from repr, but raw recipes still contain them;
do not serialize/log/transmit them merely because they have a redacted repr.

## Account fields and supported subset

### Balance

`/uapi/domestic-stock/v1/trading/inquire-balance`, real `TTTC8434R`, demo
`VTTC8434R`. The recipe fixes normal/default balance mode `AFHR_FLPR_YN=N`,
instrument aggregation `INQR_DVSN=02`, price mode `01`, no fund settlement or
automatic loan redemption, and `PRCS_DVSN=00` (previous-day trades included).
It is not an assertion of exchange-specific holdings or a complete account ledger.

The source documents 50 rows per real page and 20 per demo page, and that
zero-held rows may remain after full sales. `hldg_qty`, `ord_psbl_qty`, exact
`pchs_avg_pric`, `pchs_amt`, `evlu_amt`, signed `evlu_pfls_amt`, and `loan_amt`
are retained per instrument. No managed holdings are inferred. Balance
`ord_psbl_qty` is named **balance_orderable_quantity**, not silently promoted to
the separate sellable endpoint's quantity.

`dnca_tot_amt` is deposit total. `nxdy_excc_amt` and `prvs_rcdl_excc_amt` are
separate settlement observations. Total valuation, net assets, purchase sum,
valuation sum and signed valuation P&L are retained separately. Negative deposit
values remain negative. Neither valuation P&L nor asset change is daily loss.
No deposit field is substituted for available/settled buying cash.

All pages must reach D/E with the exact cursor chain; M/F means more pages and
the next request uses N. D/E semantics are explicitly verified for this endpoint
in the official legacy sample. Only balance has `traversal_complete=True`.
Equal summaries do not prove snapshot isolation, recent fill reflection, or
cross-endpoint consistency; `snapshot_consistent` remains false.

Sources: [request and pagination](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_balance/inquire_balance.py),
[response labels](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py),
[explicit terminal markers](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/legacy/Sample01/kis_domstk.py#L323-L360).

### Buying power

`/uapi/domestic-stock/v1/trading/inquire-psbl-order`, real `TTTC8908R`, demo
`VTTC8908R`. Single-object endpoint. `ord_psbl_cash`, `nrcvb_buy_amt/qty` and
`max_buy_amt/qty` are distinct observations. The latter are not substituted for
no-receivables quantities. `psbl_qty_calc_unpr` retains exact decimal precision.

Only inquiry code `01` is supported. Official documentation warns that `00`
does not reflect instrument margin rates for the described full-buy quantity
inquiry. This inquiry recipe does NOT change any order to a market order and
does not establish a limit-order authorization or final capacity. CMA and
overseas inclusion are explicitly N. Venue-specific eligibility is not asserted.

Sources: [request and quantity warning](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py),
[response labels](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_psbl_order/chk_inquire_psbl_order.py).

### Sellable shares

`/uapi/domestic-stock/v1/trading/inquire-psbl-sell`, real `TTTC8408R`. No demo
TR ID is invented. A single supplied response must contain exactly one matching
`pdno` row; `cblc_qty` and `ord_psbl_qty` remain separate.

The official sample shows M continuation with N on the next request, without
cursor parameters. It does NOT verify a terminal enum. This initial supported
subset only records a single D/E-marked candidate response, explicitly keeps
`traversal_complete=False`, and refuses M/F or unknown/missing markers instead of
repeating an unverified pagination flow. A parsed quantity cannot authorize a sale.

Sources: [request/pagination sample](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_psbl_sell/inquire_psbl_sell.py),
[response labels](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_psbl_sell/chk_inquire_psbl_sell.py).

## Quote, session and holiday evidence

### Current price

`/uapi/domestic-stock/v1/quotations/inquire-price`, `FHKST01010100`.
`FID_COND_MRKT_DIV_CODE` is explicitly J for KRX or NX for NXT. Integrated UN
is unsupported; no wall-clock rerouting exists. Six-digit symbols only; the
separate Q-prefixed ETN form is not supported.

`stck_shrn_iscd` must match the requested ticker. Current price, temporary-stop
flag and raw instrument-status code are retained. The source response mapping
provides no exchange timestamp. A recent capture is not proof of a fresh price;
`exchange_timestamp=None` and `executable=False` even for a positive price with
no halt flag. A Korean market name is not treated as an exchange identity echo.

Sources: [official REST functions](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_user/domestic_stock/domestic_stock_functions.py),
[price fields](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_price/chk_inquire_price.py).

### Session-related observations, not session permission

`/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`,
`FHKST01010200`, with the same explicit venue selector. The official display
mapping combines output1/output2 field names, without proving each key's object
membership. The adapter therefore retains the actual supplied object path for
`new_mkop_cls_code`, optional `antc_mkop_cls_code` and `aspr_acpt_hour`, requires
unambiguous fields and matching ticker evidence, and accepts only object-shaped
output1/output2. Other shapes remain unsupported pending schema proof.

No operation-code enum, quote-time format/timezone/date, opening/closing times,
tick/order capability or live-session completeness is invented. Raw unknown
codes remain raw; `session_permitted=False`. A 09:59:58-like time is not promoted
to an exchange timestamp. Holiday flags and ordinary exchange schedule times
cannot fill these gaps.

The official KRX websocket `H0STMKO0` sample describes VI trigger/release delivery,
not a comprehensive current-session heartbeat after reconnect. It is not wired
up or used as a substitute for session proof.

Sources: [book/session-related labels](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/chk_inquire_asking_price_exp_ccn.py),
[websocket behavior](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_user/domestic_stock/domestic_stock_functions_ws.py).

### Holiday records

`/uapi/domestic-stock/v1/quotations/chk-holiday`, `CTCA0903R`. The executable
sample TR ID is used rather than its docstring's `TCA0903R`. BASS_DT and the
unsuffixed `CTX_AREA_FK/NK` parameters remain stable across the supplied chain;
M/F continues with N. The sample recommends preferably one call per day. No
requests, cache refresh or monitoring are scheduled here.

Explicit base date and business/trading/opening/settlement flags remain separate.
The exact requested date must be present, valid and unique; missing days are not
generated from weekdays. The weekday code is preserved without interpreting its
enum. A trading day and an opening-day Y flag do not establish intraday or
exchange-specific permission or exceptional schedules.

The source's non-M/F fallback does not establish terminal completeness. D/E are
accepted only as the supported **candidate stopping markers**, with
`traversal_complete=False` regardless. Blank/unknown markers and truncated/looping
chains fail. No server page count or date horizon is asserted; max_pages/max_rows
are local resource bounds, not KIS limits.

Sources: [request, continuation and frequency guidance](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/chk_holiday/chk_holiday.py),
[calendar field labels](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/chk_holiday/chk_chk_holiday.py).

## Remaining acceptance gates

Before any production evidence bridge, independently verify authenticated
account/product/environment/venue response binding; sanitized recorded payloads;
exchange timestamps and freshness; terminal markers for sellable/calendar;
cash settlement and final order-capacity semantics; account-wide daily P&L and
buy totals; managed holdings and external-order reservations; fill watermarks and
causal consistency; exceptional calendars, KRX/NXT session codes, eligible symbols,
order modes and tick validation. A weekday/calendar/price combination is not a
substitute. Missing or conflicting evidence must stay blocked.

No account policy, risk limits or strategy was changed. The existing simulation
worker's exact broker-type restriction and production-off guards are unchanged.

## Verification

```sh
python -m unittest discover -s tests/execution_v2 -p test_kis_evidence.py -v
```

49 tests pass, including all six fixture paths with socket creation forbidden.
They cover missing/malformed values, pagination/cursor changes, mixed accounts,
stale/future/cross-midnight capture windows, duplicates, precision, separate cash
semantics, unknown session/holiday status, and refusal of policy conversion or
live submission. No production connection or DB/socket test was attempted.

The focused regression set also passes: 87 KIS boundary tests (38 existing +
49 new), 21 domain tests, 8 simulator tests and 20 recovery tests: **136 passed,
zero skipped**. Python compilation and `git diff --check` pass. This does not
include PostgreSQL, browser/socket, Windows-host or live-KIS validation.
