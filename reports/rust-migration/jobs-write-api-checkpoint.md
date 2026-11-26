# Durable research jobs and write API migration

The migration remains **ACTIVE and incomplete**. No production configuration, trading account, credential, position, entry threshold, allocation policy, or unrelated VPS service was changed. No Rust execution permit or deployable trading server was introduced.

## Implemented locally

Native authenticated `PUT /v1/watchlist` and `POST /v1/jobs` retain the original string/list validation, symbol normalization/deduplication, registry checks, vision-model availability checks, error ordering, queued payloads, and response states. Watchlist changes append a new version; they never rewrite earlier versions. Job creation inserts the queued job and its event in one transaction. Existing authentication, origin and CSRF middleware protect both routes.

The existing five-active-job API admission limit is now checked atomically using a research-only transaction advisory lock shared by enqueue paths. This closes the original check-then-insert race without changing the value five. Scheduled jobs retain the original exemption from the API admission cap. This is not a change to either paper account's capital, risk, positions, or trading policy.

The research worker uses the original session advisory lock 753194020, job priority, running-row recovery, attempt counter, completion classifications, sanitized failure handling, and append-only event ledger. It retains ownership across external workflow work. Cancellation of the requesting future does not abandon the owned task or let a second worker reclaim it. Graceful shutdown drains the owned job and stops claiming new work; a closed supervisor channel stops polling.

The scheduler uses original lock 753194021 and persistent session/task marks, retains the three evening refresh windows, issuance/monitor windows, and pending-refresh suppression. The new admission lock 753194022 serializes enqueue transactions; it does not replace either existing worker/scheduler lock or the separate paper-engine lease.

The actual native research workflow dispatcher is **not yet complete**. Execution is injected in the worker and tests use explicit offline fake workflows. This proves queue/lifecycle behavior, not native implementation of ingestion, issuance, evaluation, tuning or research workflows. No worker service was installed or started.

## Verification

- Thirty-seven unchanged Python HTTP validation/payload contracts matched for watchlist and job creation. Server-generated IDs/timestamps are verified against their actual stored rows; they are not compared to the fake Python connection's stand-in ID.
- Every request case also checks unauthenticated rejection and missing-CSRF rejection. Append-only watchlist versions and queued-event persistence are verified.
- Twelve concurrent API submissions admitted exactly five and returned seven queue-full responses. Unknown-company/model and unsupported-kind error ordering remains intact even with a full queue.
- Six original Python completion/failure fixtures matched, including partial refreshes, skipped issuance, successful jobs whose research result is negative, 500-character safe errors, and unexpected-error class messages. A failed strategy result is not relabeled as profitable or as a worker crash.
- Worker priority, cancellation ownership, simulated process-exit recovery, and attempt/event history passed against isolated PostgreSQL.
- An injected completion-transaction failure after an idempotent fake workflow side effect left the job recoverable and kept one immutable audit record after replay. Queue delivery is **at least once**; each real workflow must still prove its own idempotent writes before deployment.
- Twenty original scheduler boundary/holiday/DST cases matched. Eight concurrent scheduler calls created one mark/job, a pending refresh suppressed duplicates, and replay did not duplicate issuance/monitor marks.
- Graceful draining and closed-supervisor shutdown passed. Immutable job-event mutation was rejected.

Reproduce the targeted lifecycle check with `.venv/Scripts/python.exe scripts/test-rust-postgres.py --suite jobs_lifecycle`, or run the full checkpoint with `.venv/Scripts/python.exe scripts/checkpoint-rust-migration.py`. The combined checkpoint is `checkpoints/20261119T072149017830Z/`; its manifest records the authoritative pass/fail result, exact implementation/fixture hashes, commands, and log hashes.

The combined checkpoint passed **22 ordinary Rust tests, ten isolated PostgreSQL suites, and two explicit native sentiment suites**, as well as formatting and strict all-feature Clippy. Original Phase 7/8 preservation hashes and 106 audited Python source files were unchanged. All 7,300 existing content-addressed research records passed verification. These component checks do not establish complete service parity or authorize deployment.

## Remaining gates

Connect all actual native workflows and their resource checks; verify frontend/raw-HTTP edge contracts; complete deterministic chart/detection rendering, provider ingestion, research/training/controller workflows, native evidence publishing, backup/restore/rollback, metrics, Linux verification and full-service benchmarks. Discovery tie semantics and sentiment-enabled full-assembly parity remain pending. The daily-to-hourly approximation and five-minute turnover problem remain separate post-parity research tasks. Deployment remains disallowed.
