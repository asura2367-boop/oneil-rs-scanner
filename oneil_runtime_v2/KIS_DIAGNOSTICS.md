# KIS read-to-diagnostic runtime integration

This is offline-tested source, not a live integration or trading release. The
existing seven-stream poller previously discarded response bodies after checking
capture types. A completed polling cycle therefore did not say whether the KIS
parsers accepted its bodies. `KisDiagnosticRun` now composes those actual captures
with the existing strict seven-stream pipeline and projects the result into the
authenticated simulation host. No mock parser or invented policy context is used.

## Explicit ownership and operation

A trusted embedding caller constructs `KisDiagnosticRun` with its separately
permitted source, exact AccountBinding/day/ticker, UTC and monotonic clocks, and
finite PollLimits. KRX is the only supported diagnostic scope because native
history is not verified for NXT. The source still owns its credential permissions,
rate limiter, endpoint constraints, bounded reads and quota reservations.

Construction, snapshot reads and `host.attach_kis_diagnostics(run)` perform no
broker reads. No CLI, host factory, browser route or worker constructs an
authenticated source or starts this object. A trusted embedding caller explicitly
calls `run.run()` once; it owns threading and waiting for termination. This is not
a ready-to-run account connection or a consent interface. Actual source use still
requires its independent authorization and native/broker acceptance gates.

The poller owns orderly source close after reads return. The host only requests
cooperative cancellation on supervisor stop or close. It never closes a source
concurrently or silently starts a thread to clean it up. A never-started source
remains the embedding caller's responsibility; running a previously cancelled
object performs no read and closes it. Python cannot forcibly stop a dependency
that ignores its deadline/cancellation. Such a dependency is outside the bounded
source contract and must not be placed on the simulation owner thread.

## Complete cycles, invalidation and freshness

- Each new history request clears the previous report before the source runs.
  Partial cycles and failed refreshes cannot leave old success visible.
- Complete history/balance tuples and the five single captures are assembled into
  `KisOfflineInputSet`. The real parser checks exact scope, shapes, cursor chains,
  page/row bounds, freshness and cross-endpoint inconsistencies.
- The projection distinguishes parsed, incomplete, inconsistent, collecting,
  stale, cancelled, expired, clock-invalid and failed outcomes. `parsed` means
  supported shapes only. Inconsistent facts are reported rather than repaired.
- A response callback cannot expose a cycle before the poller commits it after
  its deadline/cancellation recheck. Cleanup failure removes apparent success.
- The original once-per-run holiday read is reused without changing timestamps
  or resetting its shared source quota. If it ages out, subsequent cycles cannot
  pretend to be fresh. No hidden second holiday request is made.
- Freshness expires using both UTC age from the oldest capture and a monotonic
  cutoff. A frozen UTC clock cannot extend that cutoff. Either clock going
  backwards latches the diagnostic closed. Configured maximum age is 1–300
  seconds and is a capture-age bound, not verified broker as-of freshness.

Raw cycle captures are cleared after parsing and on run exit. Only the original
holiday capture is retained between cycles, then dropped at exit. Sources may
retain their own input or credentials; this object cannot zeroize Python memory,
remove external references, or make arbitrary in-process introspection safe.

## Host and browser boundary

The fixed synthetic trading account remains independent. Attaching a real-account
scope for diagnostic observation does not map it to that account or modify
simulation holdings, policy registration, proposals, worker evidence or orders.
There is no order/cancel transport. `submit()` and `policy_evidence()` always block.

The existing authenticated `/api/v2/host-state` response includes only fixed-code
summary data. Account numbers, account fingerprints, tickers, request recipes,
response bodies, holdings, amounts and credential values are absent. Raw captures
and diagnostic reports are not written to SQLite or other files. Restart drops
the attachment; no prior account connection or diagnostic state is restored.

The browser shows a clearly labeled snapshot from the last manual refresh,
not an automatically updating live status. Authentication failure and hiding the
page clear the diagnostic display. There are no broker start, attach, credential
entry, capture upload or override controls. The runtime's real production identity
remains unavailable, and all provenance/policy/live flags remain false even after
complete parsing.

## Verification limits

Tests exercise actual polling and parsing, an actual authenticated-source object
with synthetic provider/client responses, actual isolated SQLite/WSGI host
composition, clock and cancellation races, stale/malformed/inconsistent captures,
source cleanup and browser DOM doubles. Socket/DNS calls are prohibited. These
are not native Windows, real browser/TLS, real credential, authenticated broker,
PostgreSQL, installation, deployment or live-order validation.
