# Implementation and research contract

This is a separate application and database, not an alternate mode of legacy KefTrade.

The browser talks to the Next.js backend-for-frontend under `/intelligence/api/v1`. The proxy forwards only cookies, origin, JSON content type and CSRF token to private FastAPI routes. FastAPI authenticates every data route independently. Sessions are opaque random tokens, stored only as SHA256 hashes in PostgreSQL. Argon2 hashes protect the single administrator password. Mutations require exact-origin and per-session CSRF checks. Login failures persist and throttle after eight failures per fifteen minutes. No signup, broker endpoints, execution integrations, or inherited legacy environment exist.

Only API-backed pages show actual financial values. Empty or missing data is not replaced with demo prices. Daily candles retain feed, split adjustment, adjustment snapshot date, source payload hashes and acquisition timestamps. One coherent adjustment snapshot is selected for an operation. Dividends are excluded from price-return targets. Alpaca daily eligible-trade OHLC is the price convention; these are not guaranteed official auction fill prices, and daily volume is provider-defined eligible volume rather than claimed regular-hours-only signed flow.

SEC Company Facts is stored in full source documents. Selected USD GAAP facts are append-only, preserving start/end dates, units, accession numbers, forms, original filed dates, acquisition times and reconstructed publication availability. Exact acceptance timestamps are used where present; otherwise availability is conservatively deferred until midnight New York following the filed date. The latter is a reconstruction, not observed delivery latency. Later restatements cannot enter an earlier `as_of` view. Ambiguous legacy debt totals are retained as raw evidence but excluded from current/noncurrent sums. PP&E payments and productive-asset payments are alternative capital-investment definitions, explicitly disclosed in source tags and formulas.

## Forecast lifecycle

On each exchange session, the scheduler queues refresh between 19:00 and 20:00 America/New_York. It queues issuance after 20:30. Inputs must have both market availability and actual acquisition no later than the 20:00 cutoff. Starting the worker too late does not permit backdating. A failed/missed ingestion produces a skipped issuance record.

For origin session t, the target starts at O[t+1] and resolves at C[t+h], h in {1,5}. The future open is an outcome input only. XNYS sessions handle holidays, DST and early closes. A forecast requires 252 complete historical outcomes. It stores a historical mean, empirical positive-return frequency, and historical 10th/90th percentiles. Those percentiles do not carry a coverage guarantee.

Issuance and outcome resolution have PostgreSQL advisory locks and unique identities. Retries cannot issue duplicates or resolve a forecast twice. Source evidence, model versions, manifests, reports, forecasts and outcomes have database UPDATE/DELETE rejection triggers. Corrections require appended evidence and explicit provenance. The outcome schema permits superseding records; no UI silently rewrites outcomes.

## Numerical experiments

Phase 2 adds a separate immutable development plan/candidate/experiment/prediction
ledger. Original technical-v1 reports stay intact, and their exposed date range is
sealed when the new plan is declared. The nine-slot configuration budget is fixed
before fitting; shared chronological boundaries, contained labels and five-session
purges apply to all stocks. New reports add calibration, volatility-scaled intervals,
RMSE/bias, probability dispersion and descriptive regime slices. They cannot confer
validation or promotion from development history. See `research-phase-2.md`.

`adjustment_asof` denotes a locally captured snapshot date. Alpaca's request
`asof` is ticker/entity mapping, not a historical revision/adjustment vintage.
Only actual archived acquisitions can support the live cutoff. Historical studies
remain reconstructed even when their adjusted OHLC ratios are internally coherent.

Job events are append-only. Worker polling writes a heartbeat; scheduled refresh
slots retry at 19:30 and 19:50 without duplicating an already pending refresh.
Scheduled issuance has priority over research jobs waiting in the queue. Provider
partial results and missed cutoffs have explicit job states. Serialization failures
are recorded as failed jobs so one invalid result cannot trap the worker in retries.

Per-company experiments fit fixed NumPy ridge and logistic heads on ten causal technical features. This avoids an unnecessary large ML dependency in the small CPU workload. Standardization is fitted only on training data. Chronological 60/20/20 blocks purge five origin sessions at boundaries and require labels to resolve within their block. Calibration residuals estimate diagnostic return intervals. Probability calibration is measured, not claimed as guaranteed.

Four primary comparisons are MAE and Brier improvement for two horizons. Confidence intervals use 2,000 paired 20-session moving-block resamples and 98.75% bounds. At least 126 distinct evaluation dates are needed. Whole dates, including all same-date observations, are resampled together. Dates are not assumed perfectly independent; non-overlapping horizon counts and approximate date-block counts are also reported. Year and stock breakdowns, calibration bins, log loss, fitted coefficients and baseline parameters remain in the report.

Dataset IDs include bar identities, protocol and implementation source hash. Repeating an identical evaluation returns its frozen report and logs access rather than silently creating another favorable result. New datasets do not erase past holdout exposure; lifetime search across stocks/reports is not certified by the four-comparison correction. Reports remain exploratory and grant no trading authority.

The watchlist and training universe are different immutable version streams. The independent pooled cohort starts blocked pending point-in-time membership, delisting and coverage evidence. Individual-company experiments do not silently bypass that gate to claim market-wide validity. Watchlist expansion is a separate operation after MSI/AXON have real resolved five-day forecasts and financial ingestion. Prospective waiting time is never manufactured by inserting historical forecasts.

## Optional vision

Both requested model repositories and revisions are registered, disabled. Enabling requires an operator license-review reference, exact weight SHA256 and successful isolated runtime verification. No weights are downloaded automatically. The 90-bar renderer uses a fixed 640×640 palette and no future bars or annotations. Input/weight hashes, cutoff, raw boxes, labels and detection confidence are recorded. Detection confidence is never called return probability.

The optional container has no network, database, broker/provider credentials or production mounts; its only writable input/output is the dedicated vision artifact volume plus bounded tmpfs. Imported model code and legacy pickle weights must be reviewed before execution. These old runtimes have not been validated here; the normal application remains useful when the panel says licensing review is required.

## Protected boundaries

No deployment/cleanup script enumerates `/home/ubuntu`, Minecraft directories, Minecraft containers, Minecraft volumes or backups. No deletion/pruning task is provided. Storage checks read only a filesystem free-space counter. PostgreSQL connection helpers reject legacy database names. Application data paths must remain within the new project's `.local` directory.

Local production-build verification is distinct from deployment. The local development cluster uses embedded PostgreSQL 18; the production Compose targets PostgreSQL 16 and the CI suite exercises that version. A local passing test is not a claim that Docker images or VPS rollout have been validated.
