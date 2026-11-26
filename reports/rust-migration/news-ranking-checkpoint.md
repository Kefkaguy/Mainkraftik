# Native news acquisition and ranking checkpoint

Result: **PASS for the tested components; full migration incomplete. No deployment.**

Implemented native paper-asset catalog access, bounded Alpaca news pagination, original field allowlisting and article replacement semantics, future-data exclusion, source identities, liquidity screening, and the Groq ranking request/response path. The request uses the exact extracted original system prompt and schema, a 75-second timeout, no redirects, no retries, no paid fallback, and a bounded response body. No broker credentials or order tools enter the language-model request.

The existing PostgreSQL advisory lock and append-only request ledger preserve the three-call daily and sixty-call monthly budgets. A committed reservation remains consumed when a request fails. Validation events retain bounded allowlisted provenance, never raw completions or provider errors.

## Reproducible verification

Run `.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py` from the project root with the isolated local PostgreSQL fixture on port 5543. The harness rejects remote targets and provisions disposable databases. Each invocation creates a new timestamped checkpoint directory containing commands, logs, code and artifact hashes, and preservation checks; previous checkpoint directories are retained.

Evidence: `checkpoints/20261119T062513362197Z/manifest.json`, `rust-tests.txt`, and `isolated-postgres.txt` in that directory.

- 16 ordinary Rust unit/parity tests passed. The six database tests intentionally excluded from ordinary `cargo test` were explicitly executed by the isolated PostgreSQL harness and all passed.
- Exact original Python request body/hash, nonalphabetic candidate/source order, seven provider-response cases, and validation events matched.
- Twelve concurrent ranking attempts produced exactly three provider calls. Failure and restart retained the budget. The monthly cap and immutable reservations passed.
- Original Python two-page news acquisition payload and source ID matched. Updated article replacement, malformed/future timestamps, field allowlisting, replay deduplication, and original source order passed.
- Pagination stopped at twenty pages with explicit truncation. Existing model, order-recovery, two-account, authentication, and dashboard-status checks remained passing.
- Rust formatting and Clippy with warnings denied passed.
- All 40 original Phase 7 frozen files, 38 additional research-manifest files, and 106 audited Python backend/worker source files matched their saved hashes. These groups overlap; their counts must not be added as unique files.

## Remaining limitations

Acquisition and ranking are implemented library components, not a complete discovery service. Candidate-frequency ranking, orchestration through sentiment and history refresh, daily selection freezing, and scheduling remain to be integrated and tested. Python builds frequency counts from per-article sets, so tied candidate order can depend on Python hash iteration; this needs explicit treatment during full discovery parity rather than an unreported selection change.

The native sentiment client and aggregation are implemented, but the actual tokenizer/ONNX inference worker is not yet ported. Remaining company/research/reporting routes, research/training workflows, restore/rollback, full-service benchmarks, Linux verification, and complete cutover remain pending. The existing numerical-only benchmark is not a full-service benchmark.

The daily-to-hourly approximation and five-minute turnover issue remain separate pending investigations after functional parity. This checkpoint makes no trading-profitability claim. The deployed Python engines, accounts, credentials, policies, positions, frontend, and unrelated VPS services were not modified.
