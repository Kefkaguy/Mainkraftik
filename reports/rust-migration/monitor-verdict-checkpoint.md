# Native monitoring and original prospective verdict

Local research component implementation. Full migration remains incomplete and deployment is prohibited.

The native `monitor` job now shares the existing dashboard's descriptive health calculation and follows it with the original prospective progress/final-verdict workflow. It keeps the existing enrollment, first 126 complete paired origins, maturity requirement, integrity tolerance, four comparisons, fixed seed, 2,000 resamples, twenty-session blocks and 98.75% intervals. It does not enroll, train, promote, submit orders or change trading policy. The six remaining research job families remain unavailable until their complete native dependencies pass.

## Implementation and numerical fidelity

- `statistics.rs` holds the existing NumPy-compatible contiguous-double reduction and the original paired date-block bootstrap. Date aggregation retains same-date cross-stock dependence. The bootstrap streams indices rather than allocating the full resampled matrix. Its default workload is unchanged; CPU work uses two shared blocking slots and an owned cancellation permit.
- `numpy_rng.rs` adds NumPy's bounded-integer Lemire paths and PCG64 cached 32-bit half handling. Existing normal draws are preserved. The implementation follows the existing locally retained NumPy source and notices, not another RNG.
- `monitoring::snapshot` is shared by the API and actual job. Risk, reliability, overdue outcomes, expected exchange session, worker freshness and recent failed jobs follow the original code. Existing behavior for a future worker timestamp is retained rather than silently introducing a new policy.
- `prospective::progress_and_verdict` holds the original transaction advisory lock 753194044. An existing verdict is returned unchanged. Finalization inserts once into the original immutable table, with no model or deployment action.

The pure Python oracle contains twelve bounded-integer streams (1,003 values each, with post-draw raw state checks), nine mixed integer/normal/raw operations and twelve paired-bootstrap cases. Integer outputs, generator state and bootstrap interval fields are compared exactly. Existing normal-draw comparisons retain their prior numerical tolerance. Moving the already-validated reduction into the shared statistics module does not change the causal feature path; its original Windows/Linux exact snapshot tests remain in the combined gate.

## Full workflow tests

Twenty-three independent synthetic schema pairs run the unchanged original Python functions/worker against native services. Cases include absent/empty enrollment, 125 origins, incomplete pairs, missing outcomes, ready-but-read-only state, positive/negative/no-improvement results, integrity failure and tolerance, first-126-only selection, later corrected outcomes, near-zero comparisons, existing verdicts, insertion failure/retry, eight concurrent finalizers, worker freshness boundaries, resource refusal and recovery after a failed terminal worker transaction.

The correction case stores an older opposite-sign outcome and a later corrected truth, so selecting the wrong version changes the verdict. Final reports and their confidence intervals are compared exactly, including the strict `lower > 0` rule. Only descriptive health/risk/reliability scalars use the existing 1e-12-scale tolerance. Forecast, outcome and bar checksums include absolute full-precision timestamp values, and verdict counts are checked after every attempt. Reopening the pool returns the same persisted final verdict.

All fixtures are synthetic software tests. Their positive branches are not evidence of a profitable strategy and do not supersede the frozen Phase 7/8 negative findings. The original research verdict measures forecast errors against the reference; it is not a cost-adjusted trading-profit verdict.

## Preserved first failure

The first differential run had zero functional-output mismatches and sixty-five checksum mismatches. The original psycopg connection used `America/Los_Angeles`, while SQLx used `UTC`. Hashing raw `to_jsonb(timestamptz)` text compared display representations of equal instants. Only the test checksums were corrected to include full-precision absolute epoch timestamps. No application timestamps or original statistical expectations were changed. All twenty-three original functional outputs remain equal after this correction.

`verdict-first-failures.json` preserves the raw first failures. `verdict-negative-provenance.json` identifies the actual first oracle by its recorded expected checksum and retains its SHA256, the normalized passing reference and the initial failure hash. It also documents that the earlier `verdict-initial-reference.json` pointer was captured after the mutable latest pointer advanced; that earlier pointer is not used as proof of the first failure's oracle.

The isolated workflow suite passed after correction. The accepted combined checkpoint and independent source/output verification are recorded in `progress.json` when the full preservation gate completes.

## Combined checkpoint: PASS

`checkpoints/20261121T061815626262Z/manifest.json` passed the full local gate: 47 ordinary Windows tests, eighteen isolated PostgreSQL suites, two native sentiment tests, one native IR HTTP test, two native evidence cross-reader tests and 47 ordinary Linux tests. Formatting and strict all-feature Clippy passed.

`monitor-verdict-combined-verification.json` independently checks all 193 source files and ZIP members, all 52 output hashes, retained numerical/first-failure provenance, and the original preservation groups: forty Phase 7 files, thirty-eight research files, twenty-seven compressed Phase 8 files and 106 original Python sources. All 7,327 archived plain/compressed records verified read-only. Full migration, full Linux integration and deployment remain explicitly unverified.

Reproduce using the existing localhost PostgreSQL cluster:

```text
.venv/Scripts/python.exe scripts/generate-rust-bootstrap-fixtures.py
cargo test --manifest-path apps/backend-rust/Cargo.toml --test bootstrap_parity
.venv/Scripts/python.exe scripts/test-rust-postgres.py --suite verdict_lifecycle
.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py
```

The database harness rejects remote/production targets and drops only its own disposable test database. Complete Linux integration, remaining research/training jobs, backup/restore/rollback, whole-service measurements and verified cutover remain required. The daily-to-hourly approximation and turnover/cost investigation remain separate work after functional parity.
