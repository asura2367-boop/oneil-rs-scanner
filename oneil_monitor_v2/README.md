# Authenticated simulation monitor integration

**Development only. Not deployed, activated, or connected to a real account. No
real credentials were created or used. Live orders and activation are absent.**

This package versions the previously isolated monitor inside the execution-v2
checkout. It integrates the existing `oneil_control_v2.ControlAPI` and
`CoreSimulationBackend` through a deliberately read-only WSGI host, plus a
same-origin authenticated browser client. Existing trading/worker/core code is
unchanged. The original standalone prototype and production Site are unchanged.

## Boundaries

- `read_model.py`: vendored `monitor.v1` projection, with source initially
  `UNBOUND_SIMULATION_PROJECTION`. The authenticated adapter supplies
  `AUTHENTICATED_SIMULATION_CORE` only after session validation.
- `host.py`: `MonitorHost` accepts the existing concrete control API/core bridge
  and this versioned projector. It requires simulation commands disabled and
  rejects every non-GET method itself. Only six fixed static assets and
  `GET /api/v2/status` exist. There are no command, live, activation, login,
  credential-installation or mock-fixture endpoints.
- `web/client.mjs`: fixed same-origin GET, browser-managed HttpOnly cookie, no-store,
  redirects rejected, ten-second timeout, stale-response fence, and validated
  snapshot/source contract. No URL-configured backend and no browser token storage.
- `web/app.mjs`: source-aware Korean UI. All owner command buttons remain disabled,
  independent of authentication. Authenticated reads poll every five seconds only
  while visible, without overlap; 401/403 suspends polling until a successful
  user-triggered read. Read transport failures do not imply order rejection.

The generic HTML shell contains no account evidence and may load without a
session. Actual evidence requires the control API's existing server-mapped
principal/account session and `monitor.read` scope. Session expiry and revocation
are rechecked after backend reads. A runtime host must use the existing approved
issuer; this package does not invent a login flow or loosen HTTPS for preview.

`compose_monitor(state=..., store=..., origin=..., broker_max_age_seconds=...,
clock=...)` creates the composition using an already supplied isolated
PostgresStore. It starts no socket, opens no connection, issues no session, and
changes no control/account state. Each serving thread needs its own core
connection as required by the existing bridge. Trusted HTTPS and exact Host are
required; forwarded headers never turn HTTP into trusted HTTPS.

## Honest state and privacy

- ACKNOWLEDGED is not FILLED; partial fills remain pending.
- UNKNOWN stays unknown, never becomes safe to resend, and is never inferred to
  mean rejected or unfilled. Displayed zero means zero **confirmed** fills.
- Heartbeat, complete broker reconciliation, halt, strategy, session and owner
  authentication are distinct. Missing strategy/session evidence remains unknown.
- Server observation times remain unchanged on reads. Browser freshness includes
  elapsed time and conservatively the full request round trip, so a slow response
  cannot become artificially fresh.
- 401/403 clears the in-memory model, rows, request details, counts and health
  evidence. Transport failure retains the last evidence explicitly as uncertain.
- Authenticated pages clear evidence and fence pending responses when backgrounded
  or leaving. Returning or restoring the Back/Forward cache requires a read before
  data can reappear. Required authentication stays required across these events.
- No local/session storage, service worker, credential logging or account IDs are
  emitted. Request IDs can occur in local URL fragments for detail navigation;
  these fragments are not sent to the server. Prior browser history is not erased.
- All data text uses textContent. No response-provided HTML or state label is trusted.

The core's list still lacks full order content, requested quantity, execution
price, venue and event history. The UI says so rather than inventing details or
fill percentages. Counts cover the latest 100 requests, not the entire account.
Reason strings are bounded allowlisted fields, not a general secret scrubber;
backends must continue emitting only reviewed operational reason codes.

## Fully mocked execution and browser QA

The test harness uses **real** ControlState session verification in a temporary
private SQLite file, real ControlAPI, real CoreSimulationBackend and the versioned
projector, with a scripted FakeStore substituted only inside tests. Public test
session strings are conspicuously labeled and never installed outside temporary
test journals. This validates integration, not production identity or PostgreSQL.

`tools/monitor_v2_test_response.py` emits one such in-process response. Node client
tests consume those responses through mocked fetch, exercising the full contract
from server session checks to the actual UI controller. No HTTP listener is used.

`tools/build_monitor_v2_mock.py` exports response envelopes to a separate empty
preview directory. It labels them `MOCK_AUTHENTICATED_SIMULATION` and
`MOCK_SESSION_READ_ONLY`; the browser banner explicitly says mock authentication,
virtual core and no actual login. There are no tokens in exported files. The real
monitor host never serves these fixtures. Neither runtime mode accepts the other's
source label, and authenticated mode also rejects the old synthetic fixture source.

This replay has a frozen fixture clock. Each scenario read restarts replay age;
that behavior is disclosed and must never be confused with fresh backend evidence.
A mock success is not evidence of browser cookie delivery or a real owner login.

## Run

From the execution-v2 checkout, with Python 3.10+ and Node 18+:

```sh
python tools/run_monitor_v2_tests.py
# Or run focused checks:
python -m unittest discover -s tests/monitor_v2 -v
node --test tests/monitor_v2/*.test.mjs
python -m unittest discover -s tests/control_v2 -v
python tools/monitor_v2_test_response.py --scenario authenticated
python tools/build_monitor_v2_mock.py --output /absolute/empty/mock-preview
```

No package installation is needed for these tests. The existing bridge import
uses the checkout's `psycopg` requirement; no actual Postgres connection is opened.
Start the generated static mock directory only with the environment's supported
local preview tool, such as `sites-preview start /absolute/empty/mock-preview`.
This is local QA, not Site registration or deployment. Its optional
`qa-mobile.html` is a 390-pixel iframe, not a mobile device emulator.

## Validation and remaining gates

Current aggregate: **224 network-free tests pass without skips**: 80 existing core/
KIS tests, 69 control tests, 46 monitor Python tests and 29 Node tests. Coverage includes auth expiry/revocation, no read mutations,
strict route/source isolation, stale evidence, unknown/ACK semantics, concurrent
refresh, failed retries, timeouts, native browser fetch binding, and private-view
lifecycle fencing. Independent review findings were fixed and regression-tested.

Browser QA uses only exported mocks. It covers rendering, session-expiry and
permission-denial clearing, recovery, transport errors, stale/unknown states,
filters, repeated refresh, detail close and Back/Forward, and locked controls.
Real browser login, TLS/cookie deployment, identity provider, actual PostgreSQL
concurrency, broker transport and live behavior remain **unverified**.

Open gates remain: actual PostgreSQL bridge/concurrency and crash testing;
reviewed identity issuer and logout flow; HTTPS reverse proxy, edge timeouts and
rate limiting; cross-browser/touch and actual BFCache lifecycle certification;
core broker evidence, legacy/new execution ownership fencing, recovery and joint
journal restore procedures. Any future simulation-command UI requires a separate
reviewed exact-confirmation flow. This monitor does not expose one. Live financial
operations require their own separately authorized user-controlled process.
