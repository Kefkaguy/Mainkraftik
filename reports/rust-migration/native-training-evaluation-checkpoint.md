# Native research training and evaluation

This continues the existing migration. It does not select a new trading strategy, alter either deployed PAPER account, or replace a frozen model. The direct intraday strategy phase remains gated on full migration parity and separate strategy validation.

## Implemented

`research_training.rs` implements the original research.py normalized Ridge and Newton-logistic heads: the same intercept treatment, regularization, Hessian floor, iteration bound, convergence criterion and sigmoid clipping. The CPU owner survives caller cancellation. Invalid shapes, nonfinite values, single-class labels and excessive projected work fail without producing a model.

`research_evaluation.rs` implements the original technical-v1 historical protocol: ten features, one- and five-session labels, chronological 60/20/20 partitions, five-session purging, calibration intervals, simple baseline comparisons, descriptive slices and the original paired block bootstrap. This is migration parity for an existing forecast protocol. It does not estimate executable trading profitability or validate a new candidate.

`research_evaluation_store.rs` persists new reports transactionally with the access log. Dataset identity retains the original protocol/cache hash, so a native code change cannot turn an existing dataset into another model-selection attempt. New reports explicitly record native source and dependency-lock hashes; they do not claim to have been produced by Python. Existing reports remain unchanged and readable. A sealed holdout allows only frozen-report access. Publication checks the seal again under a table lock to prevent a report from being published after a concurrent research declaration.

The actual `NativeDispatch` and durable `Worker` now route `evaluate` through this implementation. Five other research job kinds remain unavailable pending their dependencies; they are not replaced with placeholder success.

## Reproducible tests

- `generate-rust-training-fixtures.py`: ten synthetic datasets, 2,166 training rows, widths 1–36, constant/collinear/tiny-scale/large-offset/imbalanced cases and three single-class refusals. Original normalization means must match exactly; scale tolerance is 1e-12, coefficients 2e-9 and predictions 1e-9, scaled by magnitude.
- `generate-rust-evaluation-fixtures.py`: nine synthetic candle cases cover minimum history, complete reports, insufficient/invalid streams and single-class failure. Full panels, partitions, parameters, intervals and bootstrap conclusions are compared with the original implementation. Numerical tolerance is 5e-9 scaled by magnitude; keys, strings and conclusions must match exactly.
- `prepare-rust-evaluation.py` plus `evaluation_lifecycle.rs`: isolated Python/public PostgreSQL schemas, exact dataset IDs and manifest payloads, fresh/repeat/sealed/missing-report behavior, concurrent cache reads, rollback on access-log failure, actual native worker completion failure and restart recovery, and a deterministic concurrent holdout seal. Original reports and candles are preserved. No provider or broker network request is permitted by the fixture transports.
- Linux native sentiment uses the separately pinned original Python/ONNX/tokenizer reference. Token IDs and raw logits match exactly; maximum observed probability error is 5.820766091346741e-11. Two native inference/HTTP suites passed in the standalone run.

Windows evaluation persistence passed, including the concurrent-seal guard. The standalone Linux core gate passed 58 ordinary tests. Full combined checkpoint evidence is recorded separately once all platform and preservation gates finish.

## Retained failure

The first expanded Linux core run, `.local/rust-migration/linux-core/20261121T235900743574Z`, failed an existing evidence-writer test at its ten-second wait for two staged writers. The isolated test then passed in 0.06 seconds, and the complete Linux rerun passed all 58 ordinary tests. No evidence-writer production code, timeout or assertions were changed. The intermittent failure remains an unresolved test-reliability observation, not a claimed fix. Both runs are retained. A separate all-feature isolated run also passed in 0.09 seconds. The failed output and this reproduction are archived with hashes under `reports/rust-migration/failures/20261122T000044-linux-evidence-wait/receipt.json`.

## Remaining limits

This does not complete all candidate trainers, research controllers, API/runtime composition, whole-service benchmarks, artifact rollback or production cutover. No new one-hour model or rebalancing strategy has been activated. Phase 7/8 negative results remain authoritative; no profitable edge or $35/week result is claimed.


## Combined result — PASS for this component checkpoint

Checkpoint: `reports/rust-migration/checkpoints/20261122T000539023086Z/manifest.json`.

SHA256: `e92d6932d4ece61cf8d6f491a9caa830e951018ad90d8fe9fefa2183d48b42ba`.

The complete gate passed 58 ordinary tests on each platform, 19 isolated PostgreSQL suites and one database archive cross-read on each platform, two native sentiment tests on each platform, one Windows real-HTTP suite and two Windows evidence suites. Formatting and strict all-feature Clippy passed. All 7,327 existing records verified; 40 Phase 7 frozen files, 38 research-preservation files, 27 compressed Phase 8 files and 106 original Python source files remain unchanged.

`native-training-evaluation-combined-verification.json` independently rechecks all 235 source/ZIP members and 139 output files. Reproduce this receipt using `scripts/verify-rust-checkpoint.py` with the manifest path above. The verifier requires both the archived source and current working source to match the tested hashes; later code changes require a new checkpoint.

| Requirement | Result |
| --- | --- |
| Native original Ridge/Newton-logistic training | PASS on Windows and Linux synthetic references |
| Original historical evaluation protocol | PASS on Windows and Linux synthetic references |
| Evaluation persistence, real native worker and restart recovery | PASS on both PostgreSQL platforms |
| Existing sealed reports preserved; concurrent seal blocks publication | PASS |
| Linux native frozen sentiment and HTTP behavior | PASS |
| Frozen models, research evidence and original Python sources | PRESERVED |
| First Linux writer-test timeout | Retained; root cause unresolved; isolated and full reruns pass |
| Complete Rust migration and service rollback | INCOMPLETE |
| Direct intraday strategy and net profitability validation | NOT COMPLETE; no strategy activated |
| Production deployment | NOT AUTHORIZED BY THIS CHECKPOINT |

The next implementation work remains the other research/controller/training dependencies and complete API/runtime parity, followed by service-level benchmarks and recovery/rollback. Direct intraday forecasting and cost-aware rebalancing remain a separate validation phase. The $35 average weekly objective has not been established.
