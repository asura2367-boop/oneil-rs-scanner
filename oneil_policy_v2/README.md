# Explicit strategy-policy migration, simulation proposals only

This additive package never imports a legacy live module, opens a network or
DB connection, reads environment credentials, changes settings, enqueues an
order, creates approval, or activates anything. All outputs retain `gate=DENY`,
`authorized=False`, and `orders_sent=0`. It has no CLI or service entry point.

**It is not a deployed or live policy adapter.** The strict simulation worker now
revalidates the complete policy at its durable admission boundary through
`admission.py`. Every normal `Worker` requires an explicit `SimulationPolicySource`;
missing policy, intent or enriched evidence blocks the request. The original
proposal is not an approval: the existing exact owner-confirmation, control
revision, lease, unresolved-order and single-attempt fences remain mandatory.

Read [ADMISSION.md](ADMISSION.md) for the complete integration and trust boundary.
The separate `core_fixture_only=True` path exists only for original synthetic
core tests, and rejects every `policy-v2-` order. It must never be enabled as a
runtime fallback. No production settings, calendar, managed-lot or account adapter
is implemented by this integration.

## No current-settings claim or defaults

`import_legacy_policy` accepts an explicitly supplied, narrow export only:

- `schema_version`, account SHA-256 fingerprint, aware `observed_at`.
- Buy `enabled`, `revision`.
- Risk `reserve_pct`, `per_stock_pct`, `revision`.
- Legacy sell `enabled`, `revision`.

A separate explicit runtime input supplies actual stop/profit thresholds and
`selection_policy=RS_ONLY_V1`, `min_rs=90`, `automatic_sell_enabled=True`.
A separate reviewed constraints object supplies stock-count and evidence-age
limits plus **every** existing core risk field. Missing values never fall back
to 40/30/5/10 or any other historical defaults. Tests deliberately use non-default
37/28/6/12 values to verify preservation; those are synthetic fixtures, not the
user's settings. No current production export was accessed or migrated.

Full runtime responses are rejected rather than copied; only the whitelist is
hashed. Provenance reference, source timestamp, source digest and migration
reference become part of the immutable policy. All effective policy fields affect
the content-derived version. A current `Controls` observation must identify this
version and account and explicitly establish that halt is false.

The importer preserves a subtle verified legacy rule: `sell.enabled` means
**stop-loss enabled**, while profit-taking remains enabled independently. It must
not be relabeled as an overall sell switch. The immutable v2 policy then exposes
three independent Boolean controls for automatic buys, profit exits and stop-loss
exits. A reviewed changed policy gets a different version; old control evidence
cannot authorize it. There is no setting write endpoint in this package.

## Verified semantics retained

Source evidence comes from repository code at base commit
`5fd6730336910437ac27f6dcbb227756ac9fd96e`; it establishes implementation semantics,
not the correctness of real exchange rules or current production settings.

- `v519_cash_policy.py:2–11`: current deposit, not initial capital or equity,
  determines trading budget. Reserve and per-stock percentages remain configurable.
- `oneil_v519_live_engine.py:229–310,417–465`: count whole-account holdings
  conservatively at max(cost, valuation), and include a broker-reported higher
  exposure. Charge ceil(notional × 1.005). Every cash and broker cap must fit.
- `oneil_v519_live_engine.py:793–808`: buy, take-profit and stop-loss semantics
  above. Stop loss and profit thresholds are explicit, integer 1–50 percent.
- `build_v4b_candidates.py:81–82,103–106,122–130` and engine `1505–1516`:
  current candidate policy is RS_ONLY_V1, RS >= 90, ranked by RS descending then
  ticker. Older flow, volume, channel and technical entry filters are not blended
  into it. This package consumes an explicitly evidenced candidate batch; it does
  not recalculate RS from raw bars or certify cohort completeness.
- Engine `244–267`: automatic exits sell only min(holding, sellable, managed)
  shares. Valid percentage exits do not require technical data. A supplied
  technical exit must have verified `BB_ICHI_TREND_V1` provenance and a confirmed
  source session; profit versus stop classification follows price versus average.
- Engine `1141–1225`: manual add uses exact quantity and owner limit, existing
  holding quantity, account/policy identity, UUIDv4, same session/day and at most
  40-minute lifetime. It checks bought-today, fee-inclusive allocation, broker
  quantity and amount, book depth and exit priority. It never shrinks a request.
- No verified manual exit semantics were found. `plan_manual_exit` always raises
  `MANUAL_EXIT_UNVERIFIED_NOT_IMPLEMENTED`. This is a deliberate unresolved gap,
  not a claim that the requested feature is complete.

A configurable maximum count includes all held stocks, including unmanaged ones.
This is an explicit new constraint because no persisted legacy portfolio-count
setting was established. It prevents new distinct holdings, not adding to an
already-held stock. Zero prevents all automatic new-stock proposals.

## Evidence and missing production dependencies

The core `AccountSnapshot` does not have managed-lot provenance, average price,
cost basis or sellable quantity. `AccountEvidence` therefore requires an enriched
explicit import with these fields. None is guessed from the existing snapshot.
A causally reflected history reference must match the supplied complete history
reference. Any pending/unresolved order blocks this planner; it does not invent
reservations or bypass the core's unresolved-order fence.

Every broker power/quote is tied to account, ticker, side and venue; freshness,
book quantity and executable status are explicit. Holiday/business-day evidence,
same-day venue session interval, order code, instrument eligibility and side
capability are all mandatory. There are no built-in market times, weekday
shortcuts, holiday calculations, KRX/NXT fallbacks or inferred broker codes.
Candidate source day must match an explicitly attested accepted exchange session.
A calendar adapter must decide whether that session is acceptable for the current
session; this package cannot authenticate an assertion made by arbitrary JSON.

`Evidence.verified` and references are an **internal trusted-adapter contract**,
not cryptographic proof and not authentication. Do not construct these from an
untrusted browser request. A real broker/calendar/identity adapter and verified
holiday source remain unimplemented. Imported source settings are historical
provenance, not forever-fresh permission. Current controls/account/history/quote/
calendar/capability observations are required for every planning call.

Proposals expire at the earliest evidence expiry/session close/request expiry.
One call returns at most one proposal, exits first. It never finances a buy with
an unfilled sell or predicts fills. Replanning the same snapshot does not reserve
money or produce idempotency: queue deduplication and atomic account reservations
are the strict worker/store integration's responsibility. Reconciliation and fresh
evidence are required before another proposal can be acted on.

## Validation

Run the network-free focused suite:

```
python -m unittest discover -s tests/policy_v2 -v
```

Run the existing network-free core/control/monitor regression suite separately:

```
python tools/run_monitor_v2_tests.py
```

No PostgreSQL listener, broker call, production settings fetch, paid service,
order, activation, publish, deployment, or denied-operation workaround is needed
or performed. Real database concurrency and complete authenticated runtime
integration remain separate release gates. The aggregate offline suite now also
runs policy import/planner/admission/store-contract tests. Scripted-store and
scripted-connection tests are not PostgreSQL transaction or concurrency proof.
