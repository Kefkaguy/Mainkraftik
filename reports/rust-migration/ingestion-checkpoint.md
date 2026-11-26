# Native research ingestion persistence and recovery

Local implementation only. The full Rust migration is active and incomplete. No production services, credentials, accounts, entry thresholds, or trading policies were changed. This module does not submit brokerage orders.

## Implemented behavior

`ingestion.rs` connects the native research providers to the existing PostgreSQL schema. It stores immutable source documents by the original provider/URL/content hash identity; writes exact BigDecimal OHLCV values to NUMERIC columns; preserves adjustment basis, exchange availability, completion-time acquisition, feed identity, and original content hashes; stores corporate actions; normalizes and stores GAAP facts; calculates the original metrics; and records refresh-run results.

The original transaction boundaries remain explicit. Price source/bars commit before corporate-action acquisition. Provider failure in corporate actions is reported without discarding successfully acquired bars. Financial source documents, facts, and calculated metrics share one transaction. Missing sessions, missing CIKs, unsupported facts, SPY's financial-ingestion exclusion, and partial-run reporting match the original operations. The existing convention that a corporate-action availability message alone does not mark the complete refresh partial is preserved rather than silently reinterpreted.

Disk headroom retains the configured reserve and original 32 MiB price / 20 MiB financial projections. The independent research database retains its 256 MiB growth gate. Neither guard deletes data. A native disk probe checks that storage stays within the project's `.local` directory and rejects existing ancestors that resolve outside it before creating missing directories. Actual disk and PostgreSQL size probes are exercised locally.

## Recovery improvements

A research-only session advisory lease, identity 753194023, spans a resumable ingestion run and its provider calls. A second run is rejected while ownership is live. Cancellation closes the leased connection; recovery waits until PostgreSQL actually releases that session. This is independent of the existing paper engine lease and changes no trading risk control.

New runs retain the original random run identity. A durable caller can supply a stable identity through `resume`: the symbol input must match; completed/partial runs return their stored result without fetching again; interrupted running records replay the conflict-safe evidence writes. Source identities, initial acquisition records, bars, actions, facts, and metrics survive retries without replacement. The actual job dispatcher still needs to supply this identity once all refresh dependencies are ported; this component is not presented as a completed refresh dispatcher.

## Reproducible evidence

The test harness creates a disposable localhost PostgreSQL database. The unchanged Python implementation runs nine ingestion operations in a separate `python_reference` schema; Rust runs the same operations in the native public schema. It compares results plus six table families: source documents, daily bars, corporate actions, financial facts, calculated metrics, and ingest runs. Random UUIDs are excluded from comparison or replaced by their referenced source hash. Exact NUMERIC text and content hashes are compared; timestamps representing the same instant are equivalent and calculated numerical outputs use the existing 1e-12 tolerance.

Additional checks cover five original resource boundaries, terminal-run replay without provider requests, run-input mismatch, interrupted-run recovery, immutable trigger enforcement, twelve concurrent writers returning one source identity, and a database exception after financial source/revenue writes that rolls the entire transaction back. A subsequent retry succeeds. Another test cancels after bars have committed and while corporate-action acquisition waits; a new owner completes that run without adding duplicate bars. A fetch starting just before close-plus-15-minutes and completing on the boundary records the post-fetch time and admits the newly available bar.

The Python oracle and native transport use synthetic data and fake provider responses. They do not contact SEC, Alpaca data, or brokerage endpoints. The original Python files, deployed databases, research records, and held-out evaluations are preserved.

## Remaining gates

Full refresh still needs native information/IR enrichment, forecast and detection outcome resolution, durable dispatcher integration, and end-to-end workflow comparisons. Malformed external payload/error contracts outside existing references, cross-day recovery, Linux path/filesystem behavior, complete runtime configuration, restore/rollback, and full-service measurements remain unverified. No profitable trading advantage or whole-system speed claim follows from these tests.

Final checkpoint: `reports/rust-migration/checkpoints/20261120T041601040607Z/manifest.json` — PASS. 30 ordinary workspace tests, 11 explicitly invoked PostgreSQL suites, and 2 native sentiment suites passed. Formatting and strict all-feature Clippy passed. All 7300 archived records, 40 Phase7 frozen files, 38 research-preservation files and 106 original Python source hashes verified unchanged. The original ingestion oracle is retained in this checkpoint. Source and output hashes were independently rechecked after completion.
