# Draft offline rebuild v2

This branch preserves an isolated, simulation-only rebuild for review. It is not a release, deployment, activation, account migration, or authorization to place orders.

## Preserved source

The 119 rebuild source, test, and documentation files are byte-identical to local source commit `1132dc1f4a8e35aed13565b8f0da4308941e01b4`. This preserves corrected source `73d0693`, including `4f3fa28`, `e5c4c24`, and the earlier `b2661f0` rebuild.

The current update adds an inactive authenticated GET-source foundation: injected credential-provider/client contracts, bounded in-memory bearer lifecycle, cancellation, retry and pagination. It adds no Windows credential provider, token issuer, authenticated network implementation, persistent access or production/runtime registration. The existing credential-free HTTPS client is unchanged and rejects authenticated envelopes. Tests use visibly synthetic placeholders and forbid sockets/DNS.

The existing draft includes the supervised SQLite simulation host, dashboard/control lifecycle, policy coordinator, strict supervised Worker composition, bounded read-only evidence pipeline, and fail-closed public KIS schema profiles. Unknown broker schemas and live evidence remain blockers.

The branch remains based on repository main at `880b48859aa351a1a20202cbe8fd25c1a61d28d3`. No existing production code, workflow, dashboard state, account data, or live settings were changed. Existing legacy files remain only as inherited repository context.

## Verification performed on the preserved source

- Network-free aggregate: 818 Python tests and 33 Node tests passed, zero failures and zero skips.
- Authenticated-source tests include 61 synthetic credential lifecycle, cancellable read, pagination, redaction and fail-closed admission cases. The source validation record describes the independent component review.
- New source files checked for common credential patterns and private-data artifacts. Account/session fixtures and broker-like responses are synthetic.
- All 89 inherited GitHub Actions workflow definitions are unchanged from the previously inspected draft. This update uses a `[skip ci]` commit and no pull request was opened, so publication does not request legacy workflow execution.
- Tests include isolated SQLite and a Linux simulation subprocess. HTTPS tests replace DNS/TCP/TLS with offline doubles.

## Open acceptance gates

Real PostgreSQL durability/isolation, native Windows and Task Scheduler behavior, reboot/power-loss testing, a reviewed Windows credential provider, authenticated broker responses, trusted semantic evidence, production HTTPS/session issuance, production database roles, and actual legacy/new-engine account exclusion remain unverified or unimplemented. Detailed source documents distinguish simulation tests from those gates.

No real credentials were loaded, persisted or transmitted for these tests. No real KIS request, token issuance, broker authentication, financial action, deployment, installation or activation was performed for publication. The authenticated-source interface is not authenticated provenance proof; `policy_evidence()` and `submit()` remain blocked.

Do not run legacy trading workflows from this branch, merge it, deploy it, or connect production credentials as part of this review. Live order transport is not supplied by this rebuild.

Start with [authenticated read-source boundaries](oneil_execution_v2/KIS_AUTHENTICATED.md), [the simulation host](oneil_runtime_v2/SIMULATION_HOST.md), [the integration guide](deploy/windows/INTEGRATION.md), and [the validation record](deploy/windows/VALIDATION.md).
