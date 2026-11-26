# Native reference forecasting and outcome resolution

The full Rust migration remains active and incomplete. This is a local port of the existing research reference workflow, not a new strategy, model promotion, paper policy, or deployed service. Both actual paper accounts and their execution engine remain unchanged.

## Implemented

`forecasting.rs` implements the original one/five-session historical reference forecasts, their issuance runs/manifests, forecast outcome resolution, and chart-detection outcome resolution. It preserves the existing advisory transaction locks 753194010/11/12, fixed model identity, minimum sample count, historical mean/direction/quantiles, target definitions, original provenance and limitations, missing-data/continuity refusals, and unique prospective forecast identity. Original text, including the existing encoded interval-description characters, is preserved.

Issuance uses the service clock, exchange issuance window, 20:00 New York cutoff, and next-session open deadline. Both market availability and actual acquisition must precede the cutoff. The latest valid adjustment basis and latest acquired source version per session are retained. No API parameter can backdate this operation. Test clocks are injected exclusively into isolated fixtures.

Resolution waits for target close plus fifteen minutes, requires the original entry/exit data and feed checks, preserves missing-SPY returns as null, and appends immutable outcome rows with the original source-bar provenance. Existing outcomes and their correction history are skipped rather than rewritten. Chart detections created at or after the entry open cannot receive a retroactive next-open outcome.

The original resolver's availability-only reconstruction is preserved: later-acquired bars can be selected when their reconstructed market availability precedes the outcome cutoff. This is explicitly different from issuance's observed-acquisition restriction. Such outcomes are research labels, not evidence of an executable historical quote, a genuinely observed forecast outcome at that earlier instant, or an Alpaca fill. The test deliberately includes a later-acquired source to prevent this distinction from disappearing during migration.

## Decimal arithmetic

Outcome prices remain exact PostgreSQL NUMERIC text before arithmetic. Positive decimal division uses integer quotient/remainder with 28 significant digits and half-even rounding, followed by a separately rounded subtraction and final float conversion. A `1e-21` price return remains nonzero; converting prices to floats before division would erase it.

The first exact arithmetic test exposed a one-bit error in BigDecimal's direct `to_f64` conversion on an extreme value. That failed run is retained in `forecast-decimal-first-failure.txt`. Conversion now parses the final rounded decimal string, and all 263 original Python Decimal outcomes match bit for bit. Neither the expected fixtures nor the precision requirement was relaxed. Nonpositive prices and nonfinite final returns are explicitly refused; malformed/extreme inputs outside the original valid-data domain remain an error-contract release gate.

## Verification

Twenty-one frozen baseline cases compare statistics at the existing 1e-12 tolerance and preserve sample counts, refusal messages, and descriptive text exactly. The separate decimal comparisons use exact float bits.

The PostgreSQL harness seeds independent reference/public schemas in one disposable localhost test database. The unchanged Python implementation and Rust execute twelve issuance/resolution operations covering pre-window and entry-open restrictions, repeat issuance, missing sessions, insufficient samples, late acquisition, target maturity boundaries, missing entry/benchmark data, feed mismatch, adjustment-basis gaps, existing corrections, and late chart detections. Five persisted families are compared: dataset manifests, forecasts, forecast runs, forecast outcomes, and detection outcomes. Random identities are normalized to their stable forecast identity; manifest/source hashes remain exact. Equivalent timestamp representations and statistical outputs use the established comparison rules.

Eight concurrent issuers create one pair of forecasts for a fresh synthetic symbol. Eight concurrent forecast resolvers insert one pending result; eight chart resolvers insert exactly the two missing horizon outcomes. Replays append no duplicate outcomes. Injecting an error on the second pending forecast outcome rolls back the first outcome too; a clean retry inserts both. An issuance failure on its second horizon rolls back the earlier forecast, dataset manifest, and run record; retry succeeds. The existing immutable-outcome trigger remains enforced.

## Remaining integration

The complete native issue/refresh dispatcher still needs the remaining frozen prospective-controller dependencies and information/IR enrichment. Full runtime assembly, additional malformed payload/error contracts, cancellation during a database transaction, Linux verification, backup/restore/rollback, and whole-service CPU/RAM/latency/throughput comparisons remain open. Calendar coverage remains the existing exported 2000â€“2040 range and requires an explicit refresh process for future calendar changes.

No provider or broker endpoints were contacted by these tests. Historical research artifacts and authoritative Phase 7/8 negative results are preserved. These implementation checks do not validate a profitable trading edge.

Final checkpoint: `reports/rust-migration/checkpoints/20261120T043039999281Z/manifest.json` — PASS. 31 ordinary workspace tests, 12 explicitly invoked PostgreSQL suites, and 2 native sentiment suites passed. Formatting and strict all-feature Clippy passed. All 7300 archived records, 40 frozen Phase7 files, 38 research-preservation files and 106 original Python source hashes verified unchanged. The original workflow oracle is retained in this checkpoint. Source/output hashes were independently rechecked after completion.
