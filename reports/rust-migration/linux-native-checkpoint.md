# Local Linux component verification

Ubuntu WSL now has an isolated Rust 1.93.0 toolchain, CMake and pkg-config. The toolchain is stored under the local user's `~/.local/share/mainkraft-rust-validation` directory with `--no-modify-path`. The installer is downloaded from the [official rustup distribution](https://rust-lang.github.io/rustup/installation/other.html) and checked against its published SHA256 before execution. No VPS services or trading services are installed or started.

The first locked all-feature workspace build succeeded. Forty-five ordinary tests passed and the causal-feature test failed in eleven cases. The original output and detailed differences are retained in `linux-core-first.txt` and `linux-point-first-failures.json`.

## Root cause and independent reference

All measured feature differences were news-count `log1p(2)` values, one ULP apart. The Windows NumPy 2.5.4 reference emits `1.0986122886681098`; the actual Linux NumPy 2.5.4 reference emits `1.0986122886681096`, exactly matching Rust's Linux result. The existing Windows expected values and original application sources were not changed. Changing production calculations to force the Windows value would have broken equivalence with the Linux reference.

`linux-windows-original-feature-comparison.json` checks the two independently generated corpora: inputs and lineage are identical; three direct information values and sixteen feature values differ, confined to columns 22/23. Sixteen synthetic reference snapshot IDs consequently differ. These are synthetic oracle outputs; no existing deployed or historical database snapshots were rewritten.

An isolated local Python reference environment loads the unchanged original application functions, with NumPy 2.5.4, scikit-learn 1.9.1, pandas 3.1.1 and pandas-market-calendars 5.6.0. `generate-rust-point-fixtures.py --reference-platform linux` produces a separate immutable corpus and runtime/source/hash provenance. No AST extraction, replacement implementation or manually adjusted numerical expectations are used. Existing Windows fixture hashes are also retained in that provenance.

The native test now selects the independently produced reference for its target platform. All feature values, lineage and snapshot IDs are still compared exactly. The subsequent Linux run passed all 46 ordinary workspace tests, including chart/PNG bytes, scoped C codec, evidence durability/concurrency, numerical-model and RNG compatibility, information timestamp parsing and causal research features. `linux-point-negative-evidence.json` preserves hashes of the first failure and its independent diagnosis.

## Scope limits

This is a local Linux component gate, not full Linux deployment validation. PostgreSQL workflow suites, native ONNX worker inference/HTTP validation, IR HTTP orchestration, full original Python cross-reader orchestration, production environment compatibility and whole-service benchmarks still need explicit Linux verification. The numerical reference currently uses CPython 3.12.3 on local Ubuntu; the complete original reference environment uses CPython 3.14.2 on Windows. The remaining full-environment parity requirement is not waived by this component result.

`test-rust-linux.py` is included in the combined checkpoint. It regenerates the frozen Linux oracle through the unchanged original functions, runs the native tests with the locked dependencies and two build jobs, and retains its commands/logs/results. It does not install packages during a test. The final accepted checkpoint and independent hash verification are recorded in `progress.json` after the full gate completes.

The combined gate passed at `checkpoints/20261121T055031215734Z/manifest.json`; its retained Linux result records all 46 ordinary tests passed. `prospective-combined-verification.json` independently verifies the Linux logs/result hashes alongside the complete 187-file source archive, fifty combined output hashes and unchanged original preservation groups. This does not clear the remaining Linux integration or deployment gates above.

Reproduce after preparing the local WSL dependencies/reference environment:

```text
wsl -d Ubuntu -- bash /mnt/c/Users/erosi/OneDrive/Desktop/allprojects/keftrade-intelligence/scripts/setup-rust-linux-validation.sh
.venv/Scripts/python.exe scripts/test-rust-linux.py
```

The setup helper is restricted to local WSL; it refuses an ordinary Linux host. The test helper likewise targets the current local Ubuntu WSL environment. These helpers are development tools, not deployed application services.
