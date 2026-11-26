# Native causal research features

  Local component verification; the full migration is incomplete and deployment remains prohibited.

`apps/backend-rust/src/point_features.rs` implements the existing `information-daily-v3` feature path, independently of the paper portfolio feature path. It combines the original fourteen numerical price features, eight market-context features, and fourteen news/financial/official-publication features. Feature names, group membership, causal cutoffs, version selection, missing-value behavior, lineage and content-addressed snapshot identities are preserved. In particular, the original `all` group selects columns 0–31; it does not include the four official-publication columns. This migration does not alter that policy.

## Differential evidence

- `scripts/generate-rust-point-fixtures.py` calls the unchanged original Python implementation. Existing fixture bytes must match on regeneration. The original corpus has fourteen direct information cases and eighteen feature-build cases; a separate supplementary freeze adds ten boundary cases. Together the build cases contain 271 rows, with exact feature values, lineage and snapshot hashes compared to Rust. These cover missing context, different coherent adjustment bases, session gaps, mixed feeds, empty/flat/zero-volume prices, randomized decimal prices, company-specific facts, absent/ancient news and appended future data.
- `scripts/prepare-rust-point-features.py` seeds separate original-Python and native schemas in a disposable localhost PostgreSQL database. Twenty-four workflows compare 292 exact feature rows and 208 unique persisted snapshot identities. Both companies, historical and prospective acquisition rules, latest-only and full panels, late price corrections, late adjustment bases and earlier cutoffs are exercised. The original source and expected values are not changed.
- `tests/point_features_lifecycle.rs` also verifies repeat insertion, twelve concurrent writers, immutable update/delete rejection, rollback after an injected failure on the second row, rollback of an abandoned transaction, and persistence after closing/reopening the pool. Callers own the database transaction, as with the original helper; the helper does not commit individual rows.

## Genuine discrepancy found and fixed

The first direct comparison failed in two covered-but-empty-news cases: Python emitted integer `365`, while the initial Rust implementation emitted floating-point `365.0`. The arithmetic value was equal, but canonical JSON and therefore a snapshot identity can differ. `point-feature-first-failures.json` preserves that original failure. Native serialization now preserves the original integer sentinel without changing the numerical training representation, expected fixtures, thresholds or original Python implementation. Supplemental fixtures include this sentinel in complete feature rows and compare their exact identities.

## Limits and next dependency

This is not a claim that all research jobs are native. The eight remaining dispatcher workflows, including prospective issue/monitor integration and research/model training, remain unavailable until their complete dependency chains pass. No model was trained, nominated, promoted or deployed by this work. No paper orders were sent. Neither the daily-to-hourly forecast approximation nor portfolio rebalancing frequency was modified.

The component was tested on the current Windows reference environment. Linux numerical/runtime verification and whole-service CPU/RAM/latency/throughput measurements remain required. A connection restart test is not a power-loss or full database restore test. Phase 7/8 negative profitability conclusions remain authoritative.

## Combined checkpoint: PASS

`checkpoints/20261121T050630243216Z/manifest.json` passed the complete local gate: 46 ordinary workspace tests, sixteen isolated PostgreSQL suites, two native sentiment tests, one native IR HTTP test and two native evidence cross-reader tests. Strict all-feature Clippy and formatting passed. The verifier read all 7,327 existing records without writes, including the 27 compressed Phase 8 records.

`point-feature-combined-verification.json` independently records verification of all 176 current source files and corresponding archived ZIP members, all 44 output hashes, and the original preservation groups: forty Phase 7 files, thirty-eight research files, twenty-seven compressed Phase 8 files and 106 original Python sources. It also rechecks the negative-evidence hashes. `progress.json` points to this checkpoint and retains `full_migration_complete=false` and `deployment_allowed=false`.

Reproduce from the project root using the existing local Python environment and localhost PostgreSQL cluster:

```text
.venv/Scripts/python.exe scripts/generate-rust-point-fixtures.py
cargo test --manifest-path apps/backend-rust/Cargo.toml --test point_features
.venv/Scripts/python.exe scripts/test-rust-postgres.py --suite point_features_lifecycle
.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py
```

The PostgreSQL harness creates and drops only its own disposable test database; no production target is accepted. Original workflow oracles, commands, output logs and the tested source ZIP are retained in the timestamped checkpoint.
