# Sentiment, active engine and service recovery checkpoint

Scope: authorized local Rust migration work on November 21, 2026 (America/Los_Angeles). No deployed service, policy, brokerage credentials, capital, or unrelated service was changed. No live-money execution or new account is enabled. Combined checkpoint result will be recorded separately after completion.

## Observable behavior implemented and verified

- Discovery's per-article set traversal and frequency ties reproduce the original CPython hash/table behavior, including seeded hash profiles and large-table resize boundaries. The production profile was observed read-only as CPython 3.12.16, siphash13, 64-bit, random process hash seed. A fresh random seed preserves the original process-level behavior; this does not imply different independently random processes choose the same ties.
- Actual native ONNX inference is now used in the complete sentiment-enabled discovery test. Original NumPy float32 exponential rounding differed by one ULP and changed immutable content IDs. The original failed run and unchanged expected fixture were retained. A native scalar implementation of the pinned NumPy FMA kernel fixes the computation; it does not remap IDs or relax acceptance tolerances. Complete source/event/selection IDs match on Windows and Linux.
- Read-only retrieval of the deployed model found the exact previously qualified bundle: `c287fcc54b746686c2d30b3fe3c1d94bcd3bb00c037f762e3ae94fcbcf06429f`. The model was not trained or modified.
- A new integration test exercises actual deployed-bundle inference, causal feature generation, the two-account engine, simulated fills, restart reload/deduplication, and the original matured-only residual learner. Expected features, forecasts, source identity and coefficients come from unchanged original Python functions. Stale execution quotes prevent new forecasts/orders. Forecast midpoint accuracy remains distinct from execution equity.
- A shutdown regression was reproduced before fixing `Engine.run`: cancellation could discard an acknowledgement after broker acceptance. An owned active cycle now drains; stop checks prevent later account/order work. The existing transactional operator-pause guard remains in force.
- Native service supervision coordinates the private paper HTTP bridge, engine and existing discovery scheduler. Unexpected exit stops peers. Graceful shutdown preserves owned work; a forced deadline returns an explicit recovery-required failure. No brokerage execution permit or deployment entry point is introduced.
- The service integration test verifies public health, private-route authorization, in-flight acknowledgement ownership, the competing-worker lease, restart deduplication, and pause during acquisition.
- A real child process is killed after an external loopback mock broker accepts **0.25 shares**. PostgreSQL releases the dead process's session lease. Restart with a 404 lookup does not resubmit; subsequent partial and full fills reconcile cumulatively. The second fixture account continues independently.
- A separate original Python -> Rust -> Python handoff test uses one disposable database and the same two in-memory broker books. It verifies partial-to-full reconciliation, immutable evidence, durable cycle/forecast reuse, no duplicate submissions, and mutual exclusion in both language directions. This is worker/database rollback evidence, not container rollback evidence.

## Evidence preserved

Original failures remain under `failures/20261122T010204-real-sentiment-assembly/` and `failures/20261122T011744-graceful-stop/`. No expected 0.25-share assertion, threshold, quote freshness requirement, risk limit or frozen model was weakened. Existing Phase 7/8 artifacts and original Python sources are guarded by the combined checkpoint.

The immutable existing strategy snapshot is referenced by `progress.json`. Its zero-score/positive-exp-weight behavior, daily-return scaling and alpha=100 learner remain the comparison baseline. A fixture explicitly demonstrates that the original policy can allocate capital even when the expected return is below its cost allowance. Preserving that behavior during migration is not approval of its economics.

## Readiness limits

This work establishes component and worker recovery behavior. It does not establish complete migration, a deployable release, a profitable strategy, or a $35/week result. Remaining gates include complete runtime configuration/startup and release-image validation, artifact/container rollback, representative whole-service resource measurements, remaining relevant API/research workflows, and the final cutover audit. The original unreviewed detector weights/license gates remain closed. New strategy activation and automatic promotion remain unavailable.

The separately authorized intraday strategy phase remains after migration parity: cash/no-trade allocation, genuine 60-minute native models, bounded adaptive-learning comparisons, and purged cost-aware validation must be evaluated independently. Phase 7/8 negative findings remain authoritative and exposed audit history must not become new selection data.

## Final results of this component checkpoint

**PASS for this component scope; NOT READY for full migration/deployment.**

- Combined source checkpoint: `checkpoints/20261122T044616179363Z/manifest.json` (SHA-256 `23395e9c226723d2a0f8f2b9aea2aea073baf21b93c80057f4f7dac8056d4c56`). Independently verified: 266 source/ZIP members, 155 output files, 66 ordinary tests and 24 explicit disposable-PostgreSQL suites per platform, backup and native sentiment gates. Strict all-feature/all-target Clippy passed on both platforms.
- Supplemental combined proof: `recovery-combined-checkpoints/20261122T050710246272Z/manifest.json`.
- Worker handoff proof: `paper-handoff-checkpoints/20261122T045608773727Z/manifest.json`, including preserved Windows/Linux logs and harness source archive.
- Actual deployed reference probe: `runtime-observations/20261122T050619842892Z-deployed-synthetic-reference/receipt.json`. CPython 3.12.16: 95 Counter/set cases plus 25 large-set cases. NumPy 2.5.4: 22,092 exact float32 exponential bit patterns. This read-only check imported no trading modules, used no account data and made zero broker requests. The initial probe used an incorrect container source path; setup-failure diagnostics were retained, the path was corrected from the existing Dockerfile, and the unchanged expected references passed.
- Preservation: 40 Phase 7 frozen files, 38 research manifests, 27 compressed Phase 8 records and 106 original Python source files unchanged. Existing content-addressed research records verified read-only.

Reproduce local verification with `scripts/verify-rust-checkpoint.py <checkpoint manifest>` and `scripts/verify-rust-paper-handoff.py <handoff manifest>` using the project virtual environment. `scripts/test-rust-paper-handoff.py` runs the owned local database fixture; `scripts/test-rust-linux-paper-handoff.py` runs the corresponding owned WSL cluster fixture. The deployed-reference helper is a separate explicitly read-only diagnostic, not part of offline tests.

Current remaining gates are recorded in `progress.json`. Complete runtime/release configuration, relevant remaining API/research workflows, representative whole-service resource measurements and complete artifact/container rollback are unfinished. The strategy improvement phase has not started; no strategy was activated and no profitability improvement was claimed.
