# Authenticated KIS read-source foundation: inactive, synthetic-only

This document describes the original dependency-injected source module. Optional
Windows-only implementations now exist separately in [KIS_WINDOWS_AUTH.md](KIS_WINDOWS_AUTH.md);
none is registered in a runtime. The original interfaces and policy blocks remain unchanged.

Based on corrected public-spec source `73d0693`. `kis_authenticated.py` adds a
credential-provider and authenticated GET-client **interface**, in-memory bearer
lifecycle, bounded read retries and capture pagination. It does not implement a
Windows credential provider, token issuer or authenticated network client. No
runtime, worker, control server or production factory imports this module.

No real credential was loaded, accepted from the user, persisted or transmitted;
no real DNS/socket/TLS/broker call, database connection, Windows-host operation,
service installation, deployment, activation or order was performed for this
work. Tests supply only visibly synthetic placeholders and forbid sockets/DNS.
The existing credential-free `KisHttpsGetClient` is unchanged and deliberately
rejects the new envelope rather than relaxing its five-header boundary.

## Exact read scope

The existing canonical recipe builders and `KisGetOnlyTransport` validate every
request before rate reservation, provider access or client invocation. Caller
URLs, headers, authentication overrides, arbitrary methods and endpoints are not
accepted. Fixed HTTPS origins remain:

- Real: `https://openapi.koreainvestment.com:9443`
- Demo: `https://openapivts.koreainvestment.com:29443`

Only these seven paths and their existing fixed modes are supported:

| Path | TR ID real / demo | Traversal |
|---|---|---|
| `/uapi/domestic-stock/v1/trading/inquire-daily-ccld` | TTTC0081R / VTTC0081R | Bounded history pages |
| `/uapi/domestic-stock/v1/trading/inquire-balance` | TTTC8434R / VTTC8434R | Bounded balance pages |
| `/uapi/domestic-stock/v1/trading/inquire-psbl-order` | TTTC8908R / VTTC8908R | One response; mode 01, blank price |
| `/uapi/domestic-stock/v1/trading/inquire-psbl-sell` | TTTC8408R / unsupported | One response; no continuation |
| `/uapi/domestic-stock/v1/quotations/inquire-price` | FHKST01010100 | One capture; J=KRX, NX=NXT |
| `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | FHKST01010200 | One capture; J=KRX, NX=NXT |
| `/uapi/domestic-stock/v1/quotations/chk-holiday` | CTCA0903R / unsupported | One response; both cursors blank |

There is no general request method, order/cancel POST, token POST, WebSocket,
order submission implementation or fallback endpoint. `submit()` always blocks.
All source conflicts and unresolved semantics described in
[KIS_PUBLIC_SPEC.md](KIS_PUBLIC_SPEC.md) remain in effect.

## Future Windows credential-provider boundary

`CredentialProvider.acquire` receives only the trusted exact
account/product/environment binding, minimum remaining validity, cancellation
token and absolute monotonic deadline. It must return an already-authorized,
ephemeral `CredentialLease` for that exact scope. The lease supplies app key,
app secret and bearer, plus aware issuance/expiry timestamps. No provider
implementation reads Windows Credential Manager, DPAPI, environment variables,
files, browser state, keychains or an existing production token cache.

A future Windows provider needs a separately reviewed design for the authorized
Windows user/service identity, Credential Manager/DPAPI protection, least access,
rotation/revocation, crash-dump/log exposure and bounded cancellation. Acquiring
or persisting credentials and obtaining an OAuth grant are separate security
operations, not implicitly authorized by this interface or its tests. Token
issuance must not be smuggled into the GET client or given an order-capable
transport. Nothing here creates or expands persistent access.

The `AuthenticatedGet` envelope contains one validated `OutboundGet`, a
cancellation token and exactly three additional headers: `authorization` with
Bearer scheme, `appkey` and `appsecret`. Credential strings must be bounded ASCII
without whitespace/control/header injection. The future client must independently
revalidate exact origin, request recipe and security controls; disable ambient
auth, proxies, cookies, redirects, retry middleware and request logging; enforce
body/header limits while streaming; and report actual TLS/method/origin metadata.
The injected client in a test cannot certify those claims.

Leases and envelopes hide all secret fields in repr and refuse pickle. There is
no logger or persistence. Exceptions exposed to callers are fixed codes, with
unsafe dependency messages suppressed in ordinary tracebacks. This is not a
general serialization sanitizer: dataclass inspection, raw properties, memory,
private exception context or arbitrary Python introspection can reveal secrets.
Python strings cannot be securely zeroized. `close()` drops cached leases and
expiry records; it cannot erase references retained by a client/provider.

## Bearer lifecycle and failure behavior

- Validate exact scope, ASCII bounds, aware times, nonfuture issuance, and a
  maximum 24-hour granted lifetime. These are local conservative acceptance
  bounds, not a claim that KIS always issues a particular lifetime.
- Require token validity through the remaining whole operation plus 30 seconds
  of skew margin. Check again immediately before invoking the client.
- Keep both UTC expiry and a monotonic expiry bound. Clock rollback, invalid
  clocks and deadline overrun fail closed. Source-wide checks span operations.
- Preserve each seen bearer's original monotonic cutoff and issuance/expiry
  metadata. Reacquiring a cached token or rotating A→B→A cannot reset its lifetime
  under a frozen wall clock. Store only SHA-256 bearer fingerprints with these
  bounds, at most 32 unresolved grants. Forget a record only when UTC also proves
  it expired. Overflow or changed metadata for the same token blocks.
- A verified HTTP 401 invalidates the exact lease used. At most one renewal is
  allowed per capture and it shares the read attempt/rate/deadline budget. The
  provider must return a different valid bearer; returning the rejected token
  latches authentication closed.
- Two consecutive verified 401s latch this source closed, including across
  separate single-attempt operations. A validated HTTP 200 with `rt_cd=0` clears
  the consecutive count. No automatic reset or force-enable API exists.
- HTTP 403, unknown API-error bodies and HTTP-200 error payloads do not trigger
  a guessed token refresh. A holiday request never automatically retries or
  renews after an attempted read because its daily reservation is already used.

## Rate, retry, cancellation and cleanup bounds

All cooperating instances share the existing process-local limiter by default:
one in-flight request and one start per second, plus conservative 429 cooldowns
and one holiday first-page reservation per day. Every page and retry uses it;
failed attempts consume reservations. This is not a distributed quota service or
proof of a broker account's actual quota. A deployment must share its rate budget
with other processes and the old engine before activation.

Local limiter refusal waits in cancellable one-second intervals without touching
the provider or consuming a network-attempt slot. The provider is consulted only
after recipe validation and limiter admission. Unknown/long Retry-After retains
the existing fail-closed cooldown behavior. HTTP 429 is returned as a blocker,
not automatically retried or worked around.

A capture permits at most three attempts (configurable down to one). Only
validated HTTP 502/503/504 and exact trusted-client `TransientReadFailure` kinds
CONNECT_TIMEOUT, READ_TIMEOUT and PEER_DISCONNECTED are retryable. A generic
exception, certificate failure, redirect, malformed response, parsing failure or
unsupported frame is not retryable. Backoff starts at a configurable 1–5 seconds (default one), doubles and
is capped at five seconds. The same recipe is retained; no account/venue/date/mode
is substituted. A 401 renewal consumes the same attempt budget.

One absolute operation budget covers provider work, rate waits, retries, parsing,
all pages and cleanup. Default is 30 seconds, configurable from 1–120 seconds.
Per-attempt transport defaults remain 3-second connect/read, 10-second total and
256-KiB body, clipped to the remaining operation time. A provider receives the
smaller attempt/operation deadline. No client invocation occurs after provider
work consumes that deadline. Only one operation per source runs at a time;
concurrent/reentrant calls fail rather than racing the token cache.

`Cancellation` wakes waits immediately and is checked before provider/client
calls, while consuming body chunks and after cleanup. The provider and client
must honor the token and supplied deadline within their calls. This is a
**cooperative contract**, not hard preemption of an arbitrary blocking Python
function, OS credential lookup or close method. No background thread/alternate
socket workaround is introduced. Late/failed responses are closed, including a
response whose metadata getter raises before ownership reaches the outer
transport. Cleanup must be bounded local descriptor release with no request.

## Pagination and evidence limits

History uses the existing strict parser: real pages ≤100 rows, demo ≤15,
M/F continuation, D/E terminal, exact N/cursor request, duplicate native-ID
checks, stripped cursor-loop checks, and the supported recent-history window.
Balance checks each page before following a cursor: real ≤50 rows, demo ≤20,
strict position/totals schema, no duplicate tickers and unchanged reported totals.
Full existing balance validation is repeated on the complete capture tuple.
Defaults are 20 pages and 2,000 rows; hard bounds are 100 pages and 10,000 rows.
The absolute operation deadline bounds the whole traversal, not each page alone.
Missing terminal pages, malformed cursors, limits and failures return no partial
capture tuple. Buying power, sellable and holiday never gain continuation.

Every returned value is still `OfflineRead` or a tuple of it. A successful GET,
bearer lease, terminal traversal or matching totals **does not establish**:

- actual authenticated account/product/environment provenance;
- an executable quote, exchange sequence or freshness;
- KRX/NXT eligibility or session permission;
- settled/cash-only capacity, fees, external reservations or causal fill freshness;
- complete daily P&L, managed ownership or exact current risk settings;
- final order/cancellation semantics or ACK-to-history identity.

`policy_evidence()` always raises `KIS_LIVE_EVIDENCE_NOT_ESTABLISHED`.
No verified flag, alternate constructor or runtime registration can admit this
source to the simulator-only policy/worker boundary. Authenticated recorded
schema/provenance verification, semantic evidence composition, native Windows
validation, actual PostgreSQL concurrency gates and independent release review
remain open before any separately authorized activation.

## Offline verification

Focused command:

```sh
python -m unittest discover -s tests/execution_v2 -p 'test_kis_authenticated.py' -v
```

The reviewed aggregate runner and exact source-package inventory include the new
module, documentation and tests. Tests cover scope/header rejection before
provider use, expiry and frozen/backwards clocks, token resurrection prevention,
401 renewal and persistent halt, no-refresh errors, cancellation at every layer,
shared rate/cooldown/holiday behavior, bounded retries, metadata cleanup,
redaction, complete pagination and unconditional admission/submission blocks.
All credential and body values are synthetic. Empirical Windows secret storage,
authenticated HTTPS, broker schemas/quotas, deployment and real orders are not
validated by these tests.

Final source validation: **818 Python + 33 Node tests passed, zero skips**.
Independent review reran **271 KIS tests** (210 existing plus 61 new) and the
frozen-clock cached-token probe, with no remaining blocker for this inactive
scope. Compilation and whitespace checks passed. The reviewed issues addressed
response cleanup before ownership transfer, invalid balance pages before cursor
follow-up, persistent 401 halting and monotonic-token-expiry resurrection.
