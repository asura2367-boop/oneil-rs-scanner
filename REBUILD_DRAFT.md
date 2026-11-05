# Draft offline rebuild v2

This branch preserves an isolated rebuild for offline review and simulation. It is not a release, deployment, activation, account migration, or authorization to place orders.

## Preserved source

All 128 rebuild source, test, and documentation files are byte-identical to local source commit `d01a181ec1baf969c95b523f56a291bfcadd80da`. Earlier sources `1132dc1`, `73d0693`, `4f3fa28`, `e5c4c24`, and `b2661f0` remain preserved.

This update adds optional, inactive Windows Credential Manager source, exact-scope expiring local permission guards, a separately permissioned one-attempt token issuer, and a validated authenticated HTTPS GET client. These are callable source components, not a configured credential store, consent UI, service or live data feed. No runtime, dashboard, worker or policy factory activates them. All credential and network tests use synthetic values and mocked Windows/network APIs.

The token issuer has only the exact official real/demo tokenP endpoints. A broker token may have rights beyond these local GET restrictions; it is not described as a broker-enforced read-only token. Actual credential access/storage, token issuance and transmission require separately obtained user consent. No such operation was performed.

The existing draft includes supervised SQLite simulation, dashboard/control lifecycle, strict policy/Worker composition, offline recovery, bounded read-only evidence contracts and fail-closed public KIS schema profiles. Unknown broker schemas and live semantic evidence remain blockers.

The branch remains based on repository main at `880b48859aa351a1a20202cbe8fd25c1a61d28d3`. Existing production code, workflows, dashboard state, account data and live settings were not changed. Legacy files remain only as inherited repository context.

## Verification

- Exact-source network-free aggregate: 905 Python tests and 33 Node tests passed, zero failures or skips.
- The new source adds 87 mocked tests for permissions, credential storage/provider behavior, authenticated HTTPS, token issuance and composition.
- Source additions were scanned for common credential patterns and private-data artifacts. Account, app-key, app-secret and bearer test values are explicitly synthetic.
- All 89 inherited GitHub Actions workflow definitions remain unchanged. This publication uses `[skip ci]` and no pull request, avoiding legacy workflow execution.
- Tests include isolated SQLite and a Linux simulation subprocess. Windows credential APIs, DNS/TCP/TLS and broker responses are mocked.

## Open acceptance gates

Native Windows identity/Credential Manager behavior, a trusted human-consent and secret-entry UI, real authenticated broker schemas/provenance, machine-wide quota and legacy-execution exclusion, semantic evidence, PostgreSQL durability/concurrency, native Windows restart/power-loss behavior, production identity and deployment review remain open.

No real secret was retrieved, stored or transmitted. No KIS request, token issuance, credential configuration, installation, deployment, activation or real order was performed for publication. Green offline tests and source publication do not authorize these actions or establish production readiness. Live `policy_evidence()` and `submit()` remain blocked.

Do not run legacy trading workflows from this branch, merge it, deploy it, or connect production credentials as part of this review. No live order transport is supplied by this rebuild.

Start with [Windows-local authentication boundaries](oneil_execution_v2/KIS_WINDOWS_AUTH.md), [the simulation host](oneil_runtime_v2/SIMULATION_HOST.md), [the integration guide](deploy/windows/INTEGRATION.md), and [the validation record](deploy/windows/VALIDATION.md).
