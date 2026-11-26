# Native database archive and restore verification

Local component work only. The deployed paper system, policies, accounts and credentials have not changed. Full Rust migration and production cutover remain incomplete.

`backup.rs` reads the original research format-1 archives and the existing operations format-2 research/paper ZIP archives. Native exports retain format 2 and PostgreSQL binary COPY streams. Optional per-table SHA256/byte counts and sequence metadata extend the manifest without breaking the original Python reader. Frozen original Python backup implementations remain unchanged.

Exports use a read-only repeatable-read transaction, bounded COPY-to-ZIP channels and a staging file in the destination directory. Publication uses no-overwrite persistence, file synchronization and directory synchronization on Unix. Cancellation of a request retains the owned worker. Windows directory power-loss durability has not been established. PostgreSQL sequences are not MVCC data; captured sequence positions can include concurrent advances. Restores preserve those positions while preventing a sequence from falling below restored keys. Legacy archives without sequence metadata retain the original maximum-key fallback.

Restore authority is restricted to explicit, separate local verification databases on port 5543. Existing public tables cause refusal. Migration, removal of newly created seed rows, COPY, count/hash verification and sequence restoration take place in one transaction. Invalid input rolls back the new schema as well as its rows. This restricted verifier is not yet a production restore or service rollback command. Central-directory metadata, table counts, expanded bytes, archive bytes, manifest bytes and COPY frames have explicit resource limits. Archives are read directly; member paths are never extracted into the filesystem.

SQLx URL query parameters can override the apparent host, port or database. Native backup validation rejects `host`, `hostaddr`, `port` and `dbname` overrides, including percent-encoded parameter names, before connecting. `backup-target-validation.json` records the inspected driver source identity and preserved interrupted checkpoint. ZIP64 metadata receives the same allocation bounds even when legacy ZIP fields are not sentinel values. A restored archive's original open file descriptor is hashed before and after COPY, before commit; changing its path or contents cannot silently change the restore receipt. Busy workers refuse additional database jobs rather than accumulating an unbounded queue.

The native CLI provides `backup inspect`, `backup export`, `backup restore-new` and `backup verify-local`. Database commands require explicit `INTELLIGENCE_DATABASE_URL` or `INTELLIGENCE_PAPER_DATABASE_URL` environment variables; credentials are not command arguments or receipt fields. `restore-new` additionally requires the exact new database name through `--ack-new-database`. There is still no deployable trading/service entrypoint.

## Reproducible tests

```text
cargo test --manifest-path apps/backend-rust/Cargo.toml --lib backup::tests
.venv/Scripts/python.exe scripts/test-rust-backup.py
.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py
```

The Python harness creates only owned, disposable local databases, runs the unchanged original exporters/readers and native implementation, compares all restored table rows, and removes its databases afterward. It retains synthetic archives, original manifests, native receipts and stdout/stderr. No actual brokerage endpoint is accessed. Test rows include linked UUID/NUMERIC/JSON data, both fixed/adaptive account identities, append-only learning history and unknown/partially-filled/filled order intents.

Failure cases cover wrong database kind, PostgreSQL major, row count, COPY content, CRC, payload hash, schema, migration history, missing/extra members and duplicate members. Failed targets must remain entirely empty. Other checks cover existing-target refusal, exact acknowledgement, production-target refusal, sequence continuity including empty advanced sequences, resource refusal, concurrent no-overwrite publication and cleanup of a failed automatic verification database. Cancellation is observed while an export holds an established snapshot and waits on a real relation lock; a concurrent committed update must not change that snapshot's archived rows.

The first isolated run found a 64-byte generated paper verification database name, exceeding PostgreSQL's 63-byte identifier limit. The prefix was corrected; test expectations and restore safeguards were not relaxed. The failed run remains in `.local/rust-migration/backup-runs/5e29ebd50d8e`. Initial cross-reader success is retained in `818779d8651d`, and extended cancellation/concurrency/CRC success in `2ece02edd999`.

## Accepted component checkpoint: PASS

`checkpoints/20261121T221924459300Z/manifest.json` passes the complete existing local component gate: 51 ordinary Windows tests, eighteen isolated Windows PostgreSQL suites, the native backup cross-reader suite, two native sentiment tests, one IR HTTP test, two evidence cross-reader tests and 51 ordinary Linux tests. Formatting and strict all-feature Clippy passed. All 7,327 original plain/compressed research records verified read-only.

`backup-combined-verification.json` independently verifies 196 source files and their ZIP members, all eighty output files, seventy original/native binary COPY payloads and all original preservation groups: forty Phase 7 files, thirty-eight research files, twenty-seven compressed Phase 8 files and 106 original Python sources. The sealed backup evidence includes 43 research tables and 27 paper tables, eleven archive rejection cases, advanced/empty sequence checks and all four native CLI actions. Sources were unchanged throughout the checkpoint.

The additional Linux backup cross-reader suite also passes against PostgreSQL 18.7 and original Python 3.12.3. `linux-backup-crossread/manifest.json` seals its thirty-one synthetic artifacts/logs and independently checked seventy COPY payloads. Its disposable databases were removed, and the owned Linux test cluster was stopped with its data preserved; see `linux-postgres-stop-receipt.json`. This completes local database archive verification, not complete service rollback.

The first combined run, `checkpoints/20261121T221429639726Z`, was deliberately interrupted after passing ordinary Rust tests and strict Clippy so the independently discovered URL target-override issue could be corrected before acceptance. `aborted.json` preserves its partial output hashes and reason. It is not a passing checkpoint. No disposable PostgreSQL test databases remained after that interruption.

## Remaining boundaries

Database archive parity is only part of complete disaster recovery. Artifact/model archives, production-scale restore rehearsal, Linux PostgreSQL/ONNX integration, complete service shutdown/rollback/start ordering and a verified cutover remain required. This work does not establish an independently profitable strategy, alter the daily-to-hourly approximation or change five-minute rebalancing.

The implementation uses the pinned ZIP library's documented writer interface and SQLx PostgreSQL COPY interfaces: [ZIP writer documentation](https://docs.rs/zip/9.8.1/zip/write/struct.ZipWriter.html), [SQLx PostgreSQL connection documentation](https://docs.rs/sqlx/0.8.6/sqlx/postgres/struct.PgConnection.html). Exact dependency sources and the tested Rust lockfile are retained by the checkpoint archive.

## Additional Linux verification prerequisites

The Windows localhost test cluster is not reachable from the local WSL environment. A separate, user-owned PostgreSQL 18.7 cluster was therefore prepared inside WSL. Its server/client packages and libpq come from the [official PostgreSQL Ubuntu repository](https://www.postgresql.org/download/linux/ubuntu/), with the repository signing key fingerprint and InRelease signature verified before package hash verification. The missing liburing dependency was extracted locally from the existing authenticated Ubuntu APT package metadata. No system package or system service was installed. The cluster binds only WSL TCP loopback on port 5543 and runs no application worker.

`linux-postgres-prerequisites/manifest.json` seals the setup/start/test orchestration copies, signed repository metadata, package identities, linker check and cluster receipts. The initial index-format, missing-library and long-Unix-socket setup failures remain documented; the cluster uses TCP exclusively. The deployed API image declares Python 3.12, so these additional database differentials use the original Python functions under the existing pinned Linux Python 3.12 reference environment. Their results must be evaluated independently from the Windows Python 3.14 reference and the ordinary Linux component gate. Full Linux integration remains unverified until those suites finish and any differences are resolved.

The additional database run finished with seventeen suites passing and one failing (`research_api`). Its first incomplete attempt stopped at a missing Pillow reference dependency; after installing the matching Pillow 12.3.0, the complete run exposed a real chart encoding difference. `linux-database-first-run/manifest.json` preserves both attempts and the original failed oracle. No expectations were changed.

The Linux and Windows chart PNGs have identical RGB pixels and identical filtered rows. Windows Pillow uses zlib-ng compatibility compression; Linux Pillow uses classic zlib 1.3. `linux-chart-classic-zlib-proof.json` reproduces the Linux IDAT stream exactly with the original Pillow level/window/memLevel/strategy and row calls. The current Rust codec reproduces the Windows stream. That byte difference correctly fails the archived image hash guard, so the remaining Linux research API suite is not accepted. A native, explicitly tested compatibility implementation must preserve both original corpora and the strict archive guards. No trading policy change or model retraining is involved.
