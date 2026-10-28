# V5.19 candidate-to-KIS-to-Supabase integration

This is a **real read-only integration**, not a live-order release. It consumes the existing V5.10 report schema, rejects reports older than 15 minutes, selects RS >=90, queries KIS reference prices and reads the Supabase halt/unresolved-order state. It outputs candidate quantities as **theoretical caps only**. No KIS order endpoint is called.

**Critical gap:** the existing V5.10 candidate-producing workflow must produce `v5_10_report.json` **in the same job** before `python v519_pipeline.py --report v5_10_report.json` runs. GitHub Actions jobs do not automatically share artifacts or workspaces. The supplied standalone workflow deliberately fails if the report is absent rather than silently using a 2-day-old report. To integrate into the actual scanner, copy the Python invocation and dependencies into its job immediately after report generation; do not merely schedule the standalone workflow.

Existing dependencies in the repository: `v519_candidate_adapter.py`, `v519_pg_ledger.py`, `oneil_v519_live_runner.py`, `kis_buying_power_readonly.py`. The existing `v519_order_gateway.py` remains closed. This is not a production-ready automatic trading system; no order submission, broker acknowledgment recovery, independently verified quote freshness, whole-account loss limits, or sell execution are wired.
