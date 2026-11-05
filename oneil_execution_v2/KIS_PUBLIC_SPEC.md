# Public KIS specification conflicts: conservative offline correction

Reviewed 2026-11-05, based on public, unauthenticated official KIS portal pages
and official sample source. No API test request, account read, credential lookup,
trade, deployment or activation was performed. This change starts at `e5c4c24`.
The latest sample snapshot reviewed was
`3a9be59a3ed8745720dc3aa9d584bdf3b7830362`; earlier fixtures used `d8c7f793`.
Portal pages are mutable and have no immutable commit identifier.

## Canonical cancellation-confirmed quantity

The [history portal schema and generated response type](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/trading/inquire-daily-ccld)
spell the field `cncl_cfrm_qty`. The
[official display sample](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/domestic_stock/inquire_daily_ccld/chk_inquire_daily_ccld.py)
uses `cnc_cfrm_qty`. The strict previous parser would reject a portal-conformant
row, rather than default the missing quantity to zero.

The parser now accepts only canonical `cncl_cfrm_qty`. The sample alias, alone
or alongside the canonical key, blocks even if values agree. Case normalization
and collision rejection happen first. Observations expose the fixed parser
profile `KIS_PUBLIC_PORTAL_2026_11_05` and canonical field name, not authenticated
source provenance. Canonical-only synthetic fixtures and conflicting-alias
regressions make this choice explicit.

This is a field-name correction, not a terminal-state model. Fill, remaining,
cancel-confirmed and rejected quantities remain independent. No final cancellation
is inferred from a flag, zero remainder, field-name spelling or arithmetic.
ACK organization is still not substituted for the history numbering branch;
UNKNOWN recovery is unchanged.

## Blank-price market-mode buying-power inquiry

The [buying-power portal](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/trading/inquire-psbl-order)
requires blank `ORD_UNPR` for inquiry mode `ORD_DVSN=01`. The official sample's
positive-price example conflicts with that rule.

The supported query now requires absent price (`None`) and emits an empty wire
string, never `0` or a caller's positive limit price. The restricted transport
reconstructs this exact recipe and rejects positive-price mutations before client
invocation. Parsed `inquiry_price_krw` is None. The offline pipeline likewise
rejects a positive inquiry price rather than ignoring it or claiming the response
was bound to it. The response's quantity-calculation price remains a separately
reported field, not a reconstructed request price.

No-receivables `nrcvb_buy_amt/qty`, orderable cash and maximum capacity remain
separate. No-receivables is not relabeled settled-cash-only. This is an inquiry
mode, not permission to submit a market order or proof of exact limit-order
capacity, fees, reservations or concurrent-account state.

## Sellable and holiday: nonpaginated portal profiles, unresolved runtime shapes

Both header sections of the [sellable portal](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/trading/inquire-psbl-sell)
and [holiday portal](https://apiportal.koreainvestment.com/apiservice-apiservice?/uapi/domestic-stock/v1/quotations/chk-holiday)
state that `tr_cont` continuation is unsupported. The GitHub samples nevertheless
recurse. D/E is not a documented terminal requirement for these endpoints.

The explicit portal offline profiles therefore:

- Accept missing/blank response `tr_cont` only. M/F, D/E and all other nonblank
  values block. The restricted transport rejects these before body consumption.
- Fix holiday request `CTX_AREA_FK/NK` to required blank strings and reject
  forged request continuation before any injected-client call. Nonblank holiday
  response cursors also block. No daily rate-limit bypass through a continuation
  request exists.
- Accept a single sellable `output1` object, matching the portal schema. Its own
  prose and sample say `output`; aliases, simultaneous keys and arrays block.
- Accept exactly one holiday `output` object and exactly the requested valid
  date. The sample's array and multipage forms block. No dates or horizon are
  invented. Weekday 01–07 is retained separately from Y/N day flags.

These are **synthetic portal-profile parsers**, not a claim about actual recorded
responses. `unresolved_contracts` explicitly retains body-key/shape and
portal-versus-sample conflicts. Both observations keep
`traversal_complete=False`, `live_eligible=False` and unauthenticated provenance.
The pipeline permanently reports these missing facts and still refuses policy
context creation. No more permissive sample fallback exists.

## Intentionally separate work and remaining gates

The portal additionally documents REST book field paths and informational
operation-code meanings. This focused correction does not implement that decoder
or promote a code such as 20 to session permission. Existing raw session outputs
stay non-permitting. `KIS_EVIDENCE.md`'s original sample-based session discussion
describes why that initial parser was cautious, not the limit of the newer portal
documentation. A later versioned parser may use those documented paths/codes
only as informational observations.

Still required before live evidence: sanitized recorded response schemas;
authenticated account/product/environment/venue binding; quote exchange date/time,
sequence/freshness and reconnect behavior; special exchange sessions; NXT symbol
eligibility; cash/fill causality; final order capacity; managed holdings; complete
daily P&L; revision/terminal semantics and exact ACK-to-history identity.

Source review is not runtime observation. No statement here verifies what is
running on the user's computer or identifies the cause of any production incident.
All existing live-submission, recovery, policy and activation restrictions remain.

## Verification

Offline KIS adapter/evidence/transport/HTTPS-mock suites and the offline pipeline
suite pass. The test harnesses use injected/mocked I/O; no actual socket or broker
request is made. Tests include canonical and contradictory field spellings,
blank/positive prices, missing/blank/nonblank continuation, forged cursor recipes,
body-key/array conflicts and unchanged non-readiness. PostgreSQL, network/TLS,
browser/socket and Windows-host integration were not run for this correction.

Final local results: 210 KIS tests (including mocked HTTPS), 34 offline pipeline
tests, and 49 domain/simulator/recovery regressions passed: **293 passed, zero
skipped**. Compilation and diff checks passed. An independent reviewer reran
158 adapter/evidence/transport tests and reviewed the final source/docs changes
without blocking findings. These are synthetic checks, not live acceptance.
