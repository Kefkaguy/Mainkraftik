# Native Linux chart compatibility

Local implementation only. No deployed service, paper policy, brokerage credential, model or research result has changed. Full migration remains incomplete.

The first complete Linux database differential passed seventeen of eighteen suites. The research API suite failed because its native PNGs reproduced the original Windows encoder, while the original Linux Pillow produced a different compressed stream. The rendered RGB pixels and filtered scanlines were identical. The archived image hash guard correctly refused the differing image bytes. `linux-database-first-run/manifest.json` preserves that failed run and the original Linux oracle; `linux-chart-classic-zlib-proof.json` reproduces its classic-zlib IDAT stream exactly.

The application now has two explicit chart compression profiles: `pillow-zlib-ng-2.3.3` and `pillow-zlib-1.3`. API construction requires a selected profile. It does not infer the profile from the host OS or silently use an installed system library. Actual deployment configuration still requires verification against the original deployed runtime.

The existing zlib-ng implementation is unchanged. A bounded, statically linked classic-zlib 1.3 deflate encoder provides the additional profile. The exact [upstream release](https://github.com/madler/zlib/releases/tag/v1.3) archive was acquired from the [official archive directory](https://zlib.net/fossils/), SHA256 `ff0ba4c292013dbc27530b3a81e1f9a813cd39de01ca5e0f8bf355702efa593e`. Vendored source hashes, original license and provenance are retained under `png-codec/vendor/zlib-1.3`. Compilation uses `Z_PREFIX` and `Z_SOLO`, a private wrapper and checked allocation callbacks. The wrapper accepts only initialized fixed-image buffers with at most 1,921 bytes per row and 640 rows, with a two-MiB output bound. It exposes no decoder or arbitrary compressor options. The main application crate continues to forbid unsafe Rust.

Fresh chart responses use the explicitly configured profile. Archived chart delivery first verifies that the database cutoff and archived cutoff identify the same instant. It retains the archived timestamp spelling when recomputing the manifest/input hash. It requires the exact stored input hash and exact stored base-image hash. When the preferred encoder does not match the stored image, it tries only the other independently verified encoder over the same pixels. It annotates using the matching historical encoder. Wrong hashes, altered absolute cutoffs and invalid data remain rejected; historical records are never rewritten.

## Verification

The frozen Windows chart, annotation, font and subpixel fixtures remain unchanged. The unchanged original Python functions generated a separate Linux corpus: five charts, five refusal cases, thirteen annotated images including all cached glyphs and fractional text-position thresholds. Both compression profiles compare full PNG bytes, pixel/filter hashes, manifests and input identities. Maximum-size high-entropy buffers also round-trip through an independent decoder.

The previously failing Linux research API suite passes after the fix, as does the Windows research API suite. Both still compare all 34 original HTTP responses. Additional authenticated requests run the same archived success/refusal cases with the opposite configured encoder; stored hashes and original annotation bytes must still match exactly.

`scripts/test-rust-linux-postgres.py` makes Linux database and archive testing repeatable against the already prepared, owned local WSL PostgreSQL 18 cluster. It verifies cluster ownership, uses fixture-only database identities and broker-key environment values, serializes its runs, requires eighteen completed database tests and one completed backup test, preserves source/output hashes and refuses source changes during the run. It starts/stops only its own cluster when necessary and does not install a system service. Original Python and native fixtures use the Linux Python 3.12 reference environment. The batch runner also now treats signal-terminated subprocesses as failures.

```text
cargo test --manifest-path apps/backend-rust/Cargo.toml --test chart_parity --test chart_classic_parity
cargo test --manifest-path apps/backend-rust/Cargo.toml -p mainkraft-png-codec
.venv/Scripts/python.exe scripts/test-rust-postgres.py --suite research_api
.venv/Scripts/python.exe scripts/test-rust-linux-postgres.py
.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py
```

## Accepted combined checkpoint: PASS

`checkpoints/20261121T225329120634Z/manifest.json` passes the combined local component gate: 54 ordinary Windows tests, 54 ordinary Linux tests, eighteen isolated PostgreSQL suites on each platform, database archive cross-reading on each platform, two Windows native sentiment tests, one Windows IR HTTP test and two Windows evidence cross-reader tests. Formatting and strict all-feature Clippy pass. The Linux runner starts and stops its owned test cluster and verifies that all disposable databases were removed.

`linux-chart-combined-verification.json` independently verifies all 217 source files and ZIP members, all 124 output files, the nested Linux source/output evidence, all original Windows PNG corpora and the unchanged original preservation groups: forty Phase 7 files, thirty-eight research files, twenty-seven compressed Phase 8 files and 106 Python sources. All 7,327 archived research records verify read-only. The first failing Linux oracle remains preserved.

## Read-only deployed runtime qualification

The running Mainkraft API reports Python 3.12.16, Pillow 12.3.0, classic zlib 1.3.1 and FreeType 2.14.3. Eighteen synthetic chart/annotation fixtures executed through its original pure functions produce exactly the same full PNG bytes as the separate Linux corpus and native classic profile. This qualifies the observed deployed runtime for those fixtures; its library version is retained as 1.3.1 and is not relabeled 1.3. The source hashes of its vision module and the unchanged chart-function ASTs were checked. No service was restarted, configuration changed, database queried or brokerage endpoint accessed by the fixture observation.

The scoped API and paper-container source audits distinguish different deployed image versions. The active paper experiment is enabled, and ten key active source files match the local baseline byte for byte, including experiment, service, settings, forecast strategy and its direct data/calendar dependencies. The API image omits the local optional chart-intelligence/lab router imports; inactive legacy-paper source differences are separately recorded. These observations do not authorize changing deployed policy, silently deploying optional research modules or claiming fresh model-artifact parity. `runtime-observations/active-baseline-summary.json` and the timestamped source/fixture receipts preserve the exact scope; differing Python sources are retained privately under `.local`.

Full Linux ONNX/HTTP integration, remaining native research/training/controller workflows, complete service recovery/cutover and whole-service measurements remain unfinished. A separate pinned Linux sentiment environment and original Python reference have been prepared. Its tokens, logits, scores and HTTP contract equal the frozen Windows reference exactly, but native Rust inference against the Linux shared library is not yet verified; see `linux-sentiment-reference-preparation.json`.
