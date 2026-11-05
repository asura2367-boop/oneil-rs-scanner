# Concrete KIS HTTPS GET client, offline validated only

**Not activated, deployed or live tested. No credentials or authentication.**
`kis_https.py` implements the concrete client interface for `KisGetOnlyTransport`
on the integrated `e107bae` base. Import and construction perform no TLS setup,
DNS lookup or socket operation. Only an explicit `open_get()` performs I/O, and
no production factory, worker, policy or control module calls it.

No real network request was made while implementing or reviewing this module.
The tests replace all DNS, TCP and TLS operations with offline doubles. This is
implementation plus mocked validation, not empirical network/TLS certification,
authenticated account provenance or a production readiness claim.

## Request boundary

The client independently revalidates the exact `OutboundGet` type, all fixed
security controls, bounds, five allowed headers and the trusted account binding.
It reconstructs the seven reviewed read recipes from the canonical URL and
rejects account/product/TR/filter changes, alternate ports/hosts/schemes, URL
userinfo/fragments, extra parameters, arbitrary headers and every write path.
See [KIS_TRANSPORT.md](KIS_TRANSPORT.md) for the official source and endpoint set.

There is only `open_get`, not a generic request method. The wire request is
literal HTTP/1.1 GET with a fixed Host, `Connection: close`, JSON/identity
acceptance and the reviewed transaction headers. No body, credentials, cookies,
netrc, environment proxy, token issuer/refresh, authentication challenge handler,
redirect, retry or connection pool exists. There is no HTTP POST/order/cancel
method and no extension hook accepting one.

The concrete client must be used behind `KisGetOnlyTransport` for the shared
rate budget, cooldowns, JSON checks and non-executable capture semantics.
Direct use does not supply those adapter-level protections or authorize a read.

## TLS and direct connections

- A fresh `SSLContext(PROTOCOL_TLS_CLIENT)` requires certificate verification and
  hostname checking, minimum TLS 1.2, exact official-host SNI and HTTP/1.1 ALPN.
  An absent negotiated ALPN is accepted; HTTP/2 and older TLS are refused.
- System trust is loaded with `load_default_certs(SERVER_AUTH)`. Present
  `SSL_CERT_FILE` or `SSL_CERT_DIR` overrides fail before DNS. The code does not
  change process environment or accept a caller-provided TLS context.
- TLS key logging is explicitly disabled. It avoids `create_default_context`'s
  `SSLKEYLOGFILE` handling and does not open the environment-selected keylog file.
- DNS resolves only the exact official hostname/port. Only the first returned
  public/global unicast IPv4/IPv6 address is considered, with an exact port and
  no IPv6 scope. Private, local, multicast and malformed addresses fail closed.
- TCP connects once to that numeric address, avoiding a second hostname lookup.
  There is no alternate-address fallback, HTTP retry, cleartext fallback or proxy.
- TLS handshake completes before the single GET send. Certificate or handshake
  failures close the socket without sending the request.

Platform/OS trust is still a trusted dependency. No live trust-store contents,
real certificate chain, DNS answer or platform-specific socket behavior has been
validated. The client's test hooks are ordinary test patches, not a public bypass
API; arbitrary modification of Python/module state is outside this boundary.

## Deadlines and cleanup

DNS, TCP connect and TLS handshake share one absolute connect-phase deadline,
within the request's total deadline. Socket connect, TLS handshake, sendall and
every recv use the smaller of their phase/read timeout and remaining time.
Monotonic time is checked after operations. Header/body trickle does not extend
the total deadline, and backwards/invalid clocks fail closed.

The OS DNS resolver cannot be cancelled synchronously. The caller waits only its
remaining connect budget. One process-wide daemon lookup slot remains occupied
until the resolver actually exits; another attempt fails fast while it is busy.
A lookup not yet scheduled is cancelled before entering the resolver. An already
running late lookup only records its result and releases the slot. It never creates a
socket or sends a request after the original caller has timed out.

These limits use Python's socket/SSL timeout semantics and ordinary OS scheduling;
they are not hard real-time guarantees. Local synchronous trust-store/file setup
cannot be interrupted by a socket timeout. Elapsed time is checked before DNS,
so slow local setup prevents subsequent network work after the budget is gone.
This local I/O caveat is separate from network deadline enforcement.

Response/error cleanup closes descriptors locally without TLS unwrap or any
additional HTTP exchange. Responses are single-use; callers must close them,
preferably through the outer adapter's `finally` path. No persistent connection
can turn extra response data into a subsequent request.

## Deliberately narrow HTTP response subset

Header bytes are bounded during receipt, including delimiter splits. The limit
is 16 KiB including the status line and terminator, at most 64 headers, with
8 KiB individual lines. Oversized status lines, bare LF/CR, folded fields,
controls, duplicate/case-colliding headers and interim 1xx responses fail closed.
3xx and every Location header are rejected without issuing another request.

For HTTP 200:

- Explicit bounded Content-Length is required and received body length must match.
- Transfer-Encoding, chunked transfer and close-delimited bodies are unsupported.
- Content type must be JSON/UTF-8; compression is refused.
- Pending bytes received with headers count toward the body and cannot exceed
  the declared size. Later reads request at most the remaining length/chunk cap.
- EOF before the required length fails. `suppress_ragged_eofs=False` also refuses
  conversion of a truncated TLS stream into an ordinary EOF.

Unsupported framing is a compatibility blocker, not a reason to retry or silently
relax limits. A future chunked implementation needs separate framing review.

Non-200 status and validated headers are returned after closing the socket; the
body is not parsed or further consumed. A bounded initial header recv can
naturally contain prefix body bytes. In particular, 429 retains Retry-After even
when its ignored error body is chunked/compressed, so the outer adapter still
establishes its server cooldown. Redirects and invalid headers remain failures.

This subset follows [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html) framing
principles and [Python SSL](https://docs.python.org/3.12/library/ssl.html) timeout
and verification interfaces. These links describe contracts, not live test results.

## Redaction and unchanged safety gates

Public exceptions use fixed codes and suppress unsafe dependency exception text
in ordinary formatted tracebacks. Reprs omit account-bearing URL, header and
payload content. There is no logger. Raw response properties and URL attributes
still contain private data; neither this class nor repr protection is a general
serialization sanitizer. Private exception contexts must not be logged.

No credentials are discovered, read, accepted or saved. A future authenticated
client needs separately reviewed authorization, safe secret handling and account
binding. This unauthenticated development client cannot establish actual broker
account identity, complete/fresh evidence, session permission or order capability.
Every existing policy/live gate and simulator-only worker restriction is intact.

## Verification

```sh
python -m unittest discover -s tests/execution_v2 -p 'test_kis*.py' -v
```

Initial result: 202 KIS tests passed, zero skipped, including 52 new concrete-client
tests. All concrete-client tests mock DNS/socket/SSL, including delayed lookup,
TLS settings/errors, direct numeric connections, malicious environment settings,
timeouts, trickle streams, malformed headers/framing, cleanup, redaction and
outer-adapter 429 integration. Compilation and diff checks passed.

Not run: real DNS, sockets, TLS handshakes, broker authentication/requests,
PostgreSQL/concurrency validation, Windows-host networking, deployment or orders.
No database/socket denial was retried or worked around, and no packages or paid
services were installed/provisioned.

Final focused regression: 264 tests passed, zero skipped (202 KIS cases plus
domain, simulator, worker-orchestration and recovery cases). Independent review
reran the final 202 KIS tests, compilation and diff checks, with no remaining
blocker in this inactive, credential-free scope. Its compressed/chunked-429
cooldown finding was fixed and retested, including outer-adapter composition.
The final delayed-DNS-worker cancellation was also independently reviewed.
