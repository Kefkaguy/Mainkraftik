# Company, workspace, and research data migration

The full Rust migration remains **ACTIVE and incomplete**. The deployed paper system and its policies are unchanged. No Rust server was deployed or given order-submission permission.

## Implemented

Native authenticated handlers now cover ten additional existing route families: workspace, company detail, forecasts, model performance, research ledger, historical experiment predictions, Phase 3 overview, historical candidate predictions, monitoring, and job detail. They query the original PostgreSQL schema and retain the original distinctions between historical development, prospective descriptive reliability, and execution results.

The shared data layer preserves latest adjustment-basis selection, one version per session, availability cutoffs, optional observed-acquisition cutoffs, latest forecast-outcome versions, existing ordering and limits, and original source references. It does not interpolate missing sessions or reinterpret later-acquired data as observed historical delivery.

The native GAAP calculator preserves supported tags and their priority, filing availability, restatement selection, matching units/reporting periods, quarter versus year-to-date separation, debt components, source derivations, and missing-value explanations. Its 58 frozen Python reference fixtures pass with exact JSON-value comparisons. An initial mismatch was an existing encoded character sequence in the original free-cash-flow formula text; Rust now preserves that original string. The expected fixtures and original Python source were not changed.

Descriptive risk/reliability functions reproduce continuity checks, realized volatility, gaps and drawdown, directional accuracy, Brier/log loss, calibration bins/ECE, interval metrics, and grouped prospective summaries. Their fixed statistical tolerance is 1e-12. These measurements are not claims of profitable trading performance. DST/holiday issuance-window cases and seven read-only legacy-ledger progress cases also match the original Python implementation. The legacy progress reader neither enrolls a model nor finalizes or promotes it; it only displays existing research state.

## Verification

An isolated PostgreSQL harness seeds synthetic source versions, company bars/facts, forecasts, immutable outcome revisions, failed research candidates, research predictions, worker health, and a failed job. It then calls the unchanged Python HTTP application and the Rust application against the **same database**.

All 26 reference responses match, including not-found/invalid-symbol cases, as-of restrictions, historical labels, filtered results, and source/report projections. Each path also rejects an unauthenticated request. A revised outcome is selected for display while all five original/revised outcome rows remain present. A later-acquired bar remains visible in the original historical availability-only view and is excluded by the observed-acquisition cutoff.

The HTTP comparison treats different timezone strings for the same instant as equivalent and compares numerical JSON values at a fixed relative/absolute tolerance of 1e-12. Other fields, field presence, arrays, ordering, labels, error messages and statuses remain strict. GAAP pure fixtures use exact comparisons. Migration-created default timestamps are recorded from the same isolated database rather than guessed or removed from expected responses.

Reproduce with `.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py`. The immutable checkpoint `checkpoints/20261119T070653197436Z/` contains commands, logs, implementation/fixture hashes, output hashes, and a copy of the original Python HTTP oracle. The original per-run oracle is also retained under ignored local `research-api-oracles`, with a recorded SHA-256; it is created exclusively and never overwritten.

The final checkpoint passed: **22 ordinary Rust tests, eight isolated PostgreSQL suites, and two explicitly executed native sentiment suites**, plus formatting and all-feature Clippy with warnings denied. The 7,300 existing content-addressed research records passed verification again. The original Phase 7/8 preservation manifests and 106 audited Python backend/worker source hashes remained unchanged. None of these component checks constitutes full application or deployment acceptance.

## Remaining work

Watchlist updates and job creation/dispatch, deterministic chart/detection rendering, additional raw HTTP/type/error cases, SEC/provider ingestion, research/training/controller workflows, native evidence publishing, restore/rollback, full supervision/metrics, Linux verification, full-service benchmarks and production cutover are incomplete. Native Phase 3 progress final-analysis behavior is not implemented by the read-only endpoint.

Discovery frequency ties and the complete sentiment-enabled assembly remain explicit migration gates. The daily-to-hourly approximation and five-minute turnover issue remain unchanged until full functional parity permits separate horizon/cost research. No profitable trading advantage has been established; Phase 7/8 negative evidence remains authoritative.
