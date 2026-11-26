# Existing prospective issuance in Rust

Local research workflow implementation. Full migration remains incomplete; deployment is prohibited.

`src/prospective.rs` now issues research forecasts from the existing immutable `information-shadow-v3-126` enrollment. It does not create an enrollment, train a model, promote a candidate, create an account or send brokerage orders. The old schema and provenance labels are retained for compatibility; this is not a new trading mode.

`NativeDispatch` connects the actual `issue` job to reference issuance followed by existing-enrollment issuance. The reference transaction commits first. An enrollment failure returns the original sanitized reason and retains that reference, exactly as the Python dispatcher does. Seven other job families remain unavailable pending their native dependencies.

## Preserved behavior

- Original 20:00 cutoff, 20:30 issuance, next-session opening deadline, start-session gate and original advisory lock 753194043.
- Frozen legacy implementation identity and canonical artifact hash checks, with no override or runtime Python fallback. `generate-rust-prospective-identity.py` pins the identity of the unchanged original five Python source files. This identity describes the legacy semantics; it is not a hash of the Rust source. Full source-preservation and parity verification remain deployment prerequisites.
- Causal feature selection, acquired-before-cutoff prices and information, paired-reference requirement, original feature-group availability rules, original missing-value handling and unchanged linear/HGB model artifacts.
- Exact feature and dataset identities, immutable forecast records, unique forecast insertion, original run/event records and transaction rollback across both companies. No thresholds or execution policy were changed.
- Feature computation uses two shared blocking-worker slots; an owned permit remains with a blocking task if its caller is cancelled. The existing durable worker retains exclusive ownership of the full dispatched job.

## Differential and recovery tests

The initial trial passed thirty scenarios against the unchanged Python functions and actual Python worker. It covers successful issuance, repeat calls, deadline boundaries, unenrolled/not-started protocols, code/artifact mismatches, missing/late origins and later corrections, unpaired references, price-volume inputs, required information groups, missing IR, a context session gap, missing model metadata, injected forecast failure and root resource refusal. Frozen linear and HGB artifacts come from the existing deterministic model fixtures; no models are fitted by this test.

Eight simultaneous native issuers are compared to the original serialized behavior under the same advisory lock. Only one forecast set is inserted, while the original per-call runs/events remain visible. Another scenario fails the actual job's terminal database transaction after forecast commits, reconstructs the worker, reclaims the running job and verifies the original retry behavior without duplicated forecasts. Closing and reopening the pool also preserves identities.

The final checkpoint adds four explicit cases: short feature history, zero preceding volume, missing code-hash metadata and missing feature-group metadata. The original zero-current-volume behavior is preserved; it is not silently treated as a new eligibility restriction.

Only the four numerical inference outputs use the existing numerical-model parity tolerance (absolute 1e-11 plus relative 1e-10). Feature values, lineage, manifests, frozen artifacts and hashes retain exact comparisons. Random forecast UUIDs are checked through their persisted symbol/horizon/model identities so unrelated metadata is not discarded. Forecasts here are research observations, not paper fills or profit evidence.

`prospective-initial-reference.json` preserves the first thirty-scenario oracle pointer/hash. The final immutable combined checkpoint retains its expanded original oracle, tested source ZIP, commands and output logs. `progress.json` and the combined verification report identify the accepted checkpoint after it passes.

## Combined checkpoint: PASS

`checkpoints/20261121T055031215734Z/manifest.json` passed all checks: 46 ordinary Windows workspace tests, seventeen isolated PostgreSQL suites, two native sentiment tests, one native IR HTTP test, two native evidence cross-reader tests, and 46 ordinary Linux workspace tests. The expanded prospective suite passed all 34 scenarios. Strict all-feature Clippy and formatting passed.

`prospective-combined-verification.json` independently rechecked all 187 source files and archived ZIP members, all fifty output hashes, the original preservation groups and all 7,327 archive-record results. Preservation groups contain forty Phase 7 files, thirty-eight research files, twenty-seven compressed Phase 8 files and 106 original Python sources. Both original numerical failure-evidence manifests were also rechecked. `prospective-reference-preservation.json` proves that the calendar setup optimization left all thirty initial scenario results and table snapshots exactly unchanged.

The accepted manifest retains `full_migration_complete=false` and `deployment_allowed=false`. The Linux result and independent combined verification also retain `full_linux_validation_complete=false`.

Reproduce with the existing local database cluster:

```text
.venv/Scripts/python.exe scripts/generate-rust-prospective-identity.py
.venv/Scripts/python.exe scripts/test-rust-postgres.py --suite prospective_lifecycle
.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py
```

The harness accepts only its disposable localhost:5543 database prefix and drops that database after completion. It seeds independent synthetic schemas; it never creates or changes the deployed enrollment.

Remaining work includes monitoring/final verdicts, other research/training jobs, complete Linux integration and native ONNX validation, backup/restore/rollback, whole-service measurements and verified cutover. The daily-to-hourly approximation and turnover/cost investigation remain separate, after functional parity. Phase 7/8 negative trading-edge evidence remains authoritative.
