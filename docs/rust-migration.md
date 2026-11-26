# Full Rust migration: acceptance contract

Source objective: `C:/Users/erosi/.codex/attachments/19d70abc-ddd1-45f6-9495-0632aa9b735e/goal-objective.md`.

This is an active migration, not a completed Rust backend. The complete generated module map is in `reports/rust-migration/migration-map.md`. A narrow model parity test is not proof of application parity.

## Scope and gates

1. Inventory every backend, worker, training/research, operations module, dependency, schema and API contract. Snapshot actual deployed images, account/policy state, model bytes, database schema, and existing Phase 7/8 research hashes without changing production state.
2. Implement native Rust inference for the exact frozen Ridge/Logistic/HistGradientBoosting artifacts and residual ridge updates. Python is the test oracle only. Never silently substitute training algorithms. Account for canonical JSON/digest compatibility, Decimal quantities, NumPy random exploration, calibration, missing features, exchange calendars, partial/unknown fills, and clock semantics.
3. Implement the full paper lifecycle with SQLx and a durable lease compatible with the existing Python worker's advisory lock. No order-capable Rust process may run alongside Python for the same accounts. Local tests use isolated PostgreSQL and fake brokers only: no extra accounts and no shadow engine connected to either deployed account.
4. Implement the remaining ingestion, news/discovery, research/backtesting, authentication, API, job scheduling, reporting, storage/backup, sentiment and vision integration contracts. Retain the working Next frontend. Explicitly identify training functionality without a faithful native implementation; do not disguise substitutes as parity.
5. Run unit/integration/contract/parity tests, actual CPU/RAM/latency/throughput benchmarks on identical workloads, append-only evidence and restore/rollback tests. Verify each explicit acceptance item. Production deployment remains blocked by any critical parity gap.
6. Only after parity: separately test a genuinely horizon-matched forecast and position holding/rebalance variants. Label forecast return, executable opportunity return, actual fill-to-fill P/L, costs and turnover separately. Do not alter frozen evidence or trade policy to improve reported numbers. Evaluate residual learning against its fixed prior on forecasts recorded before outcomes. A negative result remains negative.
7. Deploy only a complete verified replacement, stopping Python first, preserving both account identities, existing positions/learning histories and policies. Verify rollback ownership/reconciliation before allowing old engine execution. Never access live-money endpoints, Minecraft, old KefTrade or unrelated services. No new paper accounts or shadow trading mode.

## Required evidence

- Per-module migration coverage and API/database contract coverage; exceptions must be explicit, not silently counted complete.
- Frozen model numerical parity, deterministic allocation (including exploration), quantity and identity parity.
- Fake broker tests: unknown acknowledgements, partial fills, cancellation/rejection, repeated/reordered observations, restart recovery and duplicate prevention.
- Append-only history, old-client-ID recovery, transaction failure and lock ownership tests.
- Existing session/CSRF/authentication behavior and frontend routes tested against Rust.
- Bounded concurrency, queue backpressure, logging with secret redaction, metrics, and shutdown/restart checks.
- Pre/post-migration off-host verified backups; rollback test against isolated restored state.
- Actual comparative CPU, RAM, latency, and throughput; no unmeasured speed claims.
- Evidence-backed fixes for hourly horizon mismatch and cost/turnover evaluation, separately versioned from migration parity. No guaranteed profit claim.
