# Inactive bounded seven-stream polling

`kis_polling.py` composes the existing GET-only authenticated read-source interface.
Construction performs no reads. An explicit `KisReadPoller.run()` is single-use,
synchronous and finite. There is no thread, scheduler, runtime/CLI registration,
credential factory, network setup, ledger access, database or activation path.
This is reconstructed source work against baseline `0f1512d`, not recovery of an
unavailable unpublished commit.

The caller supplies a trusted source, exact AccountBinding, fixed ISO day, ticker,
and explicit market venue (default KRX). Canonical recipes are all validated
before any source call. The seven streams are history, balance, buying power,
sellable, price, book/session fields and holiday. The seven-stream plan requires
real-mode recipes because sellable/holiday have no verified demo contract; demo
is rejected rather than substituted. This is not authorization for real reads.

History and balance use the source's bounded complete traversal methods. Other
streams use capture. Holiday is attempted only once per run. The source's shared
24-hour holiday reservation remains authoritative, including across pollers;
the poller neither resets it nor caches calendar observations. A new poller must
not be used to circumvent quotas. No captured values or timestamps are refreshed
or promoted to current trading evidence.

## Bounds and ownership

PollLimits defaults: one cycle, one-second minimum gap after each completed read,
and a 120-second total duration. Bounds: 1–100 cycles, 1–60-second gaps, 1–3600-second
total duration. Maximum read operations are `6 * cycles + 1`; source-level pages
and retries retain their own stricter caps and shared limiter. Any exception or
invalid capture stops the run; no poll-level retry or skip-and-continue occurs.

One absolute monotonic deadline covers all streams, source work and cadence
waits. The source capture/history/balance methods now accept optional
`deadline_monotonic` and `deadline_clock`. The supplied clock must be the exact
same callable used to construct the source. The deadline clips the existing
operation budget, which propagates through provider, outbound request, retries,
parsing, pagination and cleanup checks. Omitting both preserves prior behavior.
These are cooperative bounds: arbitrary injected functions, callbacks or native
calls cannot be forcibly interrupted. Real client/provider enforcement is still
required and not proven by mocks. Clock reversal/nonfinite readings fail closed.

The poller owns its source for one run. `stop()` cancels its shared Cancellation
and sets cancelled status immediately. The run owner calls source.close only
when the source operation has returned, including all error paths; stop never
races close against an in-flight source lock. A stopped poller cannot restart.
Cleanup failure cannot result in completed status. No credential is persisted.

Cancellation/deadlines are checked before and after waits, before and after
source calls, and around request/response callbacks. A response callback receives
only the fixed stream label, before its read counter is committed. If that
callback stops the run, no success is committed. stop and publication serialize
under a lock, so a cancelled status cannot be overwritten by late success.
Callbacks must be bounded local operations and must not publish raw data.

## Sanitized status

PollStatus exposes only fixed state/stream labels and historical completed-read
and completed-cycle counters. Terminal failure/cancellation clears the active
stream; counters are progress history, never a current successful observation.
No account, ticker, recipe, economics, response body, dependency exception or
credential appears in status. live_eligible, provenance_authenticated,
policy_ready and live_capable remain false. policy_evidence and submit always
block. No evidence enters policy, worker or runtime registries.

This does not prove broker schema, freshness, account provenance, cash capacity,
exchange session permission, settled balances or order semantics. The existing
KIS_AUTHENTICATED, KIS_TRANSPORT and KIS_PUBLIC_SPEC limitations still apply.

## Offline verification

`python -m unittest discover -s tests/execution_v2 -p 'test_kis_polling.py' -v`

Tests use synthetic fixtures and mock sources/clients, with socket and DNS
creation forbidden. They cover all seven fixed paths, pacing, finite cycles,
holiday single capture, invalid config/clocks, shared source deadline propagation,
provider overrun before client invocation, stop before start, during callbacks,
response streaming, retry/pagination/cadence waits, source cleanup, stale-success
suppression, repeat/reentrant start, redaction and policy/submission blocks.
No network call, actual credential, installation, workflow, financial operation
or production setting was used or changed. This component remains inactive.
