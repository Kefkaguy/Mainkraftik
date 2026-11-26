# Native saved-head historical audit

`research_audit.rs` reconstructs predictions from saved technical-v1 fitted parameters. It never calls a trainer, selects a candidate, promotes a model or submits an order. The actual native research dispatcher and durable worker now support `audit`.

The original report fields, partitions, MAE reconciliation criterion, descriptive metrics, diagnoses and methodological limits are retained. The reconciliation check preserves NumPy's original `rtol=1e-10` and default `atol=1e-8`; neither tolerance was loosened. Metric comparisons against the original function use the existing 5e-9 magnitude-scaled numerical tolerance.

New native audits add explicit producer/source/lock hashes and derive their own content IDs. They do not claim to be Python-generated artifacts or reuse a Python content ID for a numerically reconstructed report. Existing Python audits and report links remain unchanged. Repeated native audits with the same inputs and producer resolve to one immutable row.

Loading uses one repeatable-read, read-only database snapshot. Missing, duplicated or mismatched manifest lineage fails before reconstruction. Publication appends one content-addressed record. An owned CPU permit survives caller cancellation; the generic ownership mechanism was verified in the preceding training/evaluation checkpoint.

## Tested behavior

`prepare-rust-audit.py` runs the original audit twice against sealed synthetic history, including an older report that must not replace the latest saved head. The fixture preserves an existing Python audit and a sentinel audit. No protected market evaluation data is used.

`audit_lifecycle.rs` verifies:

- Full original reconstruction fields and metrics; saved report IDs must match exactly.
- Corrupted reconstruction fails the original MAE reconciliation check.
- Unavailable archive rows fail lineage validation without changing stored candles.
- Failed audit publication appends no record.
- Sequential, concurrent and reopened auditors produce one native content ID.
- The real native dispatcher and worker recover from a failure after audit publication but before job completion; retry reaches attempt two without a duplicate audit.
- Saved reports, dataset manifests, candles, research plans and all original audits remain unchanged.
- Fixture transports panic on provider or broker requests.

The isolated PostgreSQL audit test passed on Windows and Linux. Strict all-feature Clippy passed. The complete component checkpoint and independent preservation receipt are recorded below after the gate finishes.

This adds historical reconstruction only. It does not establish profitability, complete the remaining research/training/controller workflows, or authorize deployment.


## Combined component result — PASS

Checkpoint: `reports/rust-migration/checkpoints/20261122T002708858788Z/manifest.json`. SHA256: `5ece7693d03a8ed14e50bfa87a1b815176344afe5067afa67a7f39ef0dac9b03`.

58 ordinary tests and 20 isolated PostgreSQL suites passed on each platform, with database archive cross-reading and two native sentiment tests per platform. Windows real-HTTP/evidence suites, formatting and strict all-feature Clippy passed. All 7,327 original records, frozen groups and original Python sources remain unchanged. The independent receipt `native-saved-head-audit-combined-verification.json` rechecked 239 source/ZIP members and 143 outputs.

Full migration and deployment remain incomplete. The earlier writer-test timeout remains retained and unresolved; this combined run passed with the original timeout and assertions.
