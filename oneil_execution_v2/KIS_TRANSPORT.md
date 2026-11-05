# KIS GET-only injected-client boundary

**Development contract only. Not a live KIS connection or activation gate.**
`kis_transport.py` adds a restricted GET boundary to the offline contracts at
`b2661f0`. The boundary itself has no network implementation, credentials or authentication headers.
A separate optional concrete client is documented in [KIS_HTTPS.md](KIS_HTTPS.md);
it remains credential-free and inactive. The boundary has no
token issuer/refresh, environment-secret lookup, socket, database, scheduled
polling, order submission, cancel endpoint, production wiring or deployment.

The only client operation is `open_get(OutboundGet)`. The adapter accepts an
explicit, trusted `AccountBinding`; a browser cannot select accounts or URLs.
The client is injected. It is a trusted dependency, not a sandbox: an arbitrary
Python object can ignore its controls or fabricate metadata. **Offline tests do
not prove actual TLS, network deadlines, authenticated account provenance, DNS
routing, broker freshness or real response schemas.**

## Exact request scope

The origins are exact strings bound to the configured environment:

- Real: `https://openapi.koreainvestment.com:9443`
- Demo: `https://openapivts.koreainvestment.com:29443`

Different schemes, host casing, ports, trailing slashes, userinfo, query strings,
fragments and alternate hosts are rejected. These values were checked against
[KIS's official configuration](https://github.com/koreainvestment/open-trading-api/blob/main/kis_devlp.yaml)
on 2026-11-05; this is a source review, not a live connectivity check.

Exactly seven reviewed GET paths are supported:

- `inquire-daily-ccld`: existing exact recent-history recipe, KRX only.
- `inquire-balance`: existing normal/default balance recipe.
- `inquire-psbl-order`: portal buying-power inquiry, code `01` with blank `ORD_UNPR` only.
- `inquire-psbl-sell`: real only, no invented demo TR ID.
- `inquire-price`: explicit KRX/J or NXT/NX selector, no rerouting.
- `inquire-asking-price-exp-ccn`: same explicit venue selectors.
- `chk-holiday`: real only, explicit date and required blank cursors; continuation blocked.

Paths retain their full prefixes from `kis_adapter.py` / `kis_evidence.py`.
See [KIS_ADAPTER.md](KIS_ADAPTER.md) and [KIS_EVIDENCE.md](KIS_EVIDENCE.md)
for pinned endpoint sources and deliberately unsupported meanings.

The adapter reconstructs the existing immutable recipe and requires exact
matching of method, path, environment TR ID, parameter order/values and
continuation header. Duplicate/unknown/missing parameters, account/product
changes, altered filters, unsupported modes and query/header injection fail
before the injected client is called. Parameters are URL-encoded. There is no
arbitrary URL, body, authentication-header or request-method input. The URL
contains account information and must not be logged.

## Client obligations and adapter checks

`OutboundGet` is immutable and fixes:

- `method=GET`, `verify_tls=True`, `follow_redirects=False`.
- `use_environment=False`: no ambient proxy, netrc, auth or cookie inheritance.
- `automatic_retries=0`, identity encoding and JSON response request headers.
- Connect/read timeout defaults 3 seconds each, total deadline 10 seconds.
  Configured connect/read bounds are 0.1–10 seconds, total 0.1–30 seconds,
  and neither phase bound may exceed the total.
- Default body ceiling 256 KiB, configurable 128 bytes–1 MiB; chunks at most
  16 KiB. Header ceiling 16 KiB and 64 entries.

A future reviewed network client **must enforce all controls before/during I/O**,
including TLS certificate and hostname verification, redirect refusal before any
second request, no automatic authentication refresh/retry, and remaining total
time in every blocking operation including response cleanup. It must stream raw
identity bytes without eager buffering and enforce header limits during receipt.
It must not emit request URLs, auth data, bodies or unsafe exceptions in logs.

The adapter additionally checks elapsed monotonic time before/after open, each
chunk, parse and close; rejects backwards/invalid clocks; validates the actual
response method, final URL, verified-TLS flag and zero redirect count; refuses all
3xx and every Location header before body consumption. A synchronous adapter
cannot interrupt an injected client that hangs or lies. Its post-return checks
are not a substitute for client-side enforcement. The separate concrete client now implements a narrow Content-Length-only HTTPS
subset, tested with fully mocked I/O. Actual network/TLS validation remains open;
see [KIS_HTTPS.md](KIS_HTTPS.md).

Only HTTP 200 with explicit JSON/UTF-8 content type and identity encoding is
parsed. Optional response `tr_id` must match the recipe. Declared length is
bounded and must equal received length; streamed size is independently bounded.
Duplicate/case-colliding headers or JSON keys, excessive depth, malformed UTF-8,
unsupported floats/constants and oversized numbers fail closed. Only `tr_id`
and `tr_cont` response headers leave the adapter. API business errors remain
raw, non-executable observations for existing parsers to reject.

Response cleanup is attempted on failures. The adapter never retries a GET,
refreshes a token, follows redirects, schedules later calls or changes an order.

## Conservative local rate budget

All default instances share one process-local limiter: one request start per
second and one request in flight. Explicit shared limiters can set a longer
window and at most two starts per window. Every attempted read consumes its
reservation, including HTTP/parse/client failures. Saturation returns a fixed
error immediately; no sleep or retry occurs.

HTTP 429 establishes a shared cooldown. Integer and aware HTTP-date Retry-After
values are never shortened; valid values up to seven days have a conservative
60-second minimum. Missing, malformed, timezone-ambiguous or longer values latch
that limiter closed with no automatic reset. HTTP-date handling uses an injected
aware UTC clock and fails closed if it cannot be read.
A caller still needs a reviewed broker-quota policy before any network use.
The holiday portal recommends preferably one call per day, so holiday starts
additionally have a 24-hour local cap. Nonblank holiday cursors or request
continuation are rejected before client invocation; there is no continuation
exception to the daily cap. No calendar refresh is scheduled.

The portal declares buying-power, sellable and holiday response continuation
unsupported. Any nonblank `tr_cont`, including D/E or M/F, is rejected before
body consumption for these endpoints. The contradictory sample recursion is
not followed. This does not resolve actual sellable/holiday body-shape conflicts.
See [KIS_PUBLIC_SPEC.md](KIS_PUBLIC_SPEC.md).

These are local safety bounds, **not verified KIS account quotas**. They are not
persistent across process restart and cannot coordinate another process, legacy
engine or independently supplied limiter. Production needs one shared budget
across all clients of the same broker credentials plus the actual broker rules.

## Output and redaction

`read()` returns the existing `KisResponse`; `read_history()` restricts it to the
history path. `capture()` adds validated aware capture timestamps and returns
`OfflineRead`. Neither output proves authenticated provenance, traversal
completion, current account consistency, exchange time or session permission.
Existing normalizers continue to mark observations `live_eligible=False` and
`provenance_authenticated=False`. `submit()` and `policy_evidence()` always block.

No network payload is logged. Reprs hide URL/account-bearing values and response
bodies. Raised messages are a closed set of fixed codes; external exceptions,
including spoofed private exceptions, cannot put their messages in ordinary
formatted traceback output. This is **not** a general-purpose data sanitizer:
raw response bodies, URL attributes and private exception contexts must not be
serialized, inspected into logs or shared. Data needed by the caller remains raw.

## Offline validation and open gates

Run only the socket-free unit suite:

```sh
python -m unittest discover -s tests/execution_v2 -p 'test_kis*.py' -v
```

The initial result is 149 tests passed, zero skipped: 62 new transport cases plus
87 existing request/parser cases. All new tests forbid socket creation and use
synthetic account placeholders and injected clients, not real credentials.
Coverage includes all seven endpoints, hostile origins/paths/parameters, TLS and
redirect metadata, streaming bounds, clock deadlines, parser ambiguity, fixed
error redaction, rate contention/cooldowns, cleanup and non-executable outputs.

Remaining before production network use: validate the concrete client on an
explicitly authorized network environment (including real TLS, redirects,
timeouts and streaming), reviewed
authentication/account mapping and safe logging, cross-process quota management,
authorized sanitized recordings and genuine provenance checks. All existing
policy, freshness, complete ledger, database concurrency and live activation
acceptance gates remain open. No DB/socket workaround, service, dependency
installation, GitHub CI fallback, broker request or financial action was attempted.

The final focused regression suite also passed 211 tests, zero skipped (the 149
KIS tests plus domain, simulator, worker-orchestration and recovery tests).
Compilation and `git diff --check` passed. Independent review reran the 149 KIS
tests, verified the official origins and adversarial cooldown reproductions, and
reported no remaining blocker within this documented injected-client-only scope.
Its two findings (private-exception leakage and premature server cooldown expiry)
were fixed before final review. Actual PostgreSQL/concurrency, Windows-host,
network/TLS and live-broker validation were not performed.
