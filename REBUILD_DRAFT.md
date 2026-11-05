# Draft offline rebuild v2

This branch preserves an isolated, simulation-only rebuild for review. It is not a release, deployment, activation, account migration, or authorization to place orders.

## Preserved source

The 81 rebuild source, test, and documentation files are byte-identical to local source commit `b2661f0df102c9f7ff6234961a6a35ce75dcdfaf`. They include the execution core, offline KIS contracts and evidence normalizers, authenticated simulation control API, read-only monitor, explicit policy admission, Windows packaging foundations, durable runtime orchestration, exact-evidence recovery, and cooperative ownership rehearsal.

These additions are over repository main at `880b48859aa351a1a20202cbe8fd25c1a61d28d3`. No existing production code, workflow, dashboard state, account data, or live settings were changed. Existing legacy files remain only as inherited repository context.

## Verification performed on the preserved source

- Network-free aggregate: 412 Python tests and 29 Node tests passed, zero skips.
- Offline KIS account/market evidence suite: 49 Python tests passed separately.
- New files checked for common credential patterns and private-data artifacts; account and session fixtures are synthetic.
- The 89 inherited GitHub Actions workflow definitions were inspected. This draft branch uses a `[skip ci]` commit and no pull request was opened, so publication does not request legacy workflow execution.

## Open acceptance gates

Real PostgreSQL durability/isolation, native Windows and Task Scheduler behavior, restart/power-loss testing, trusted evidence acquisition, production HTTPS/session issuance, production database roles, and actual legacy/new-engine account exclusion remain unverified or unimplemented. The source's detailed validation documents distinguish simulation tests from those gates.

Do not run legacy trading workflows from this branch, merge it, deploy it, or connect production credentials as part of this review. Live order transport is not supplied by this rebuild.

Start with [the execution README](oneil_execution_v2/README.md), [the Windows runbook](deploy/windows/RUNBOOK.md), and [the validation limitations](deploy/windows/VALIDATION.md).
