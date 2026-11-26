# Premarket lifecycle, native sentiment, and research reporting

The migration remains **ACTIVE and incomplete**. Nothing in this checkpoint authorizes deployment, model promotion, or a trading-policy change.

## Implemented locally

`discovery::Research` now connects catalog screening, causal news acquisition, liquidity history, bounded ranking, optional financial sentiment, full price-history refresh, and immutable daily selection freezing. A scheduler retains the original premarket window, sixty-second polling, and twenty-minute attempt spacing. Research never receives order authority.

An unchanged Python discovery run is captured offline with fake providers and a disposable connection recorder. Rust matches its complete research result ID, source records, rank request, history queries, event payloads, and frozen selection. Additional lifecycle tests cover frozen-session restart, no backdating, preview separation, late completion, missing-benchmark transaction rollback, failure retry spacing, and closed-channel shutdown. The initial new test failed because its final no-orders query used a nonexistent table name; the captured failure is retained, and the query now checks the actual existing `learning_intents` table. Neither strategy expectations nor safeguards were changed.

`sentiment_worker::Inference` loads the existing frozen tokenizer and quantized ONNX model after checking their pinned hashes and manifest revision. It performs serial tokenization, equivalent batch padding, and CPU ONNX execution with one intra-op and one inter-op thread. The single-job worker uses a blocking task whose concurrency permit survives cancellation of the HTTP caller. Its router and direct in-process transport can serve the same sentiment contract without Python or remote model code. No service was started or deployed.

Reference Python versions are ONNX Runtime 1.32.0, tokenizers 0.23.3, and NumPy 2.5.4. Native bindings are pinned to `ort` 2.0.0-rc.12 and Rust `tokenizers` 0.23.2. The Rust tokenizer crate version is explicitly different from the Python package version; equivalence is established for the frozen model by the recorded token/mask tests, rather than inferred from version names. The native test loads the **same ONNX Runtime 1.32.0 Windows library** as Python and verifies its hash. The frozen model was not retrained or replaced.

Across four batch sizes and twelve distinct texts, token IDs, attention masks, type IDs, and raw logits match exactly. Maximum float32-softmax probability difference is **5.820766091346741e-11**, below the fixed test tolerance of 2e-6. Tests include Unicode, variable-length padding, truncation at 128 tokens, source-text hashes, duplicate rejection, sixteen original decoded-body HTTP validation cases, health/scoring, busy responses, artifact-identity/path rejection, and cancellation ownership.

The implementation follows the upstream [ORT runtime-loading interface](https://github.com/pykeio/ort/blob/main/docs/content/setup/linking.mdx) and [tokenizer API](https://docs.rs/tokenizers/0.23.2/tokenizers/). Successful Windows fixtures do not prove Linux/runtime parity.

The authenticated Rust API now includes `/v1/chart-intelligence/phase7`, `/v1/chart-intelligence/status`, and `/v1/research-lab/results`. These read and verify existing content-addressed records, preserve the original report projections, strip internal features and detailed arrays, and expose no promotion authority. Legacy controller SQLite is opened read-only. Tests verify unchanged database bytes, hundred-record status limits, authenticated/unauthenticated behavior, missing-store states, and rejection of tampered synthetic evidence.

## Evidence and reproduction

Run `.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py`. It regenerates frozen offline oracles without overwriting different content, checks all Rust features, executes isolated PostgreSQL and native sentiment suites explicitly, and writes a new timestamped checkpoint directory. It also invokes the read-only Rust `verify-evidence` command against existing local Phase 7, Chart Intelligence, and strategy-lab record archives.

Latest evidence for this checkpoint is under `checkpoints/20261119T064722950787Z/`. The manifest is the authoritative pass/fail result and records the exact tested code and fixture hashes.

The final checkpoint passed: **19 ordinary Rust tests, seven isolated PostgreSQL suites, and two explicitly executed native sentiment suites**, plus formatting and all-feature Clippy with warnings denied. The Rust reader verified all **7,300 existing research records** (16 Phase 7, 18 Chart Intelligence, 7,266 strategy-lab) without writing them. All 40 original Phase 7 frozen files, 38 research-manifest files, and 106 audited Python backend/worker files still matched their saved hashes; these preservation groups overlap.

## Remaining work

Equal-frequency candidate order still needs an explicit migration decision: Python's per-article set order is process-random, while the current Rust research implementation uses first acquired symbol order for ties. The complete sentiment-enabled discovery assembly also needs its own differential fixture. Malformed/raw HTTP edge cases beyond the recorded decoded-body contracts remain unverified.

Most company/workspace/forecast/job routes, native research-controller writes, research/backtesting/training workflows, filesystem publishing, backup/restore/rollback, full telemetry, full-service resource comparisons, Linux verification, and production cutover remain incomplete. See `docs/rust-migration-coverage.md` for the full-scope matrix.

The legacy daily-to-hourly approximation and five-minute allocation policy remain unchanged pending full functional parity and separate horizon/cost/turnover research. No profitable trading advantage is claimed; Phase 7/8 negative results remain authoritative. The deployed Python engines, accounts, credentials, positions, policies, working frontend, and unrelated VPS services were not changed.
