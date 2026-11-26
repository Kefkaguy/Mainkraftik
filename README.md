# Mainkraft

The Day, Swing and Challenger policies and initial results are documented in [Mainkraft paper pilot](docs/mainkraft-paper-pilot.md).

An independent, private company-research workspace with an isolated experimental PAPER-only pilot. Next.js + FastAPI + PostgreSQL. **MSI and AXON** remain in frozen research; **AMD, MSFT, GOOG, AMZN, TSM and META** form the separate paper pilot, with SPY as a benchmark. The forecast ledger is prospective and append-only. Both requested chart models remain licensing-blocked.

The existing KefTrade repository and production services are not part of this application. **Never inspect, modify, delete, move, prune, or restart anything belonging to `/home/ubuntu/minecraft`, its services, volumes, containers, or backups.** No VPS cleanup is required or provided.

## Run locally on Windows

Use Python 3.12+ and Node 22+. Run from this new repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/api/requirements.lock.txt
npm ci
npm --prefix apps/web ci
.\.venv\Scripts\python.exe scripts/bootstrap.py --sec-email YOUR_CONTACT_EMAIL
```

Configure only `INTELLIGENCE_ALPACA_KEY` and `INTELLIGENCE_ALPACA_SECRET` in the new `.env` for market data. Do not copy a legacy `.env`, broker settings, or paper-execution credentials. The supplied SEC email is used only in the provider request User-Agent. New local credentials are generated in the ignored `.local/access.txt`; the password hash is in the ignored `.env`.

For the already configured working copy, start with:

```powershell
.\scripts\dev.ps1 -WithWorker
```

Or use separate terminals:

```powershell
# Terminal 1, repository root: independent local PostgreSQL on 127.0.0.1:5543
npm run db:local

# Terminal 2, apps/api:
..\..\.venv\Scripts\python.exe -m intelligence.cli migrate
..\..\.venv\Scripts\python.exe -m uvicorn intelligence.main:app --host 127.0.0.1 --port 8100

# Terminal 3, repository root:
npm --prefix apps/web run dev

# Terminal 4, apps/api:
..\..\.venv\Scripts\python.exe -m intelligence.cli worker
```

Open **http://127.0.0.1:3100/intelligence**. The local database is a separate embedded PostgreSQL cluster in `.local/postgres`; Docker is not needed for local development. Persistent data is retained when its process stops. Do not run another instance against the same cluster. `dev.ps1` does not stop occupied ports or unrelated processes.

Refresh data in the UI, or from `apps/api`:

```powershell
..\..\.venv\Scripts\python.exe -m intelligence.cli refresh --symbols MSI AXON SPY
..\..\.venv\Scripts\python.exe -m intelligence.cli evaluate --symbol MSI
..\..\.venv\Scripts\python.exe -m intelligence.cli evaluate --symbol AXON
..\..\.venv\Scripts\python.exe -m intelligence.cli issue
..\..\.venv\Scripts\python.exe -m intelligence.cli resolve
```

Provider failures are recorded; data is never silently substituted. Corporate actions and financial facts retain source documents. Supported financial ratios match reporting periods and units. An unavailable tag is shown as unavailable rather than inferred. Capital investment may include software/intangibles when the SEC tag is `PaymentsToAcquireProductiveAssets`.

## Forecasts and evaluation

For origin trading session t, forecast returns are `C[t+1]/O[t+1]-1` and `C[t+5]/O[t+1]-1`, using coherent split-adjusted daily eligible-trade prices, excluding dividends. Future opens are not feature inputs. Cutoff is **20:00 America/New_York** and issuance is **20:30**. The worker refreshes between 19:00 and 20:00. Missed cutoffs are recorded as skipped; there is no backdating option.

The baseline requires 252 completed historical outcomes and records historical mean return, positive-return frequency, and 10th/90th outcome percentiles. These are reference statistics, not a promise of future accuracy. Live outcomes resolve only after their target session has closed and new prices are ingested. SPY uses the same convention.

Numerical research uses ridge/logistic models with ten causal technical features, chronological 60/20/20 splits, five-session boundary purges, 2,000 paired 20-session block resamples, and 98.75% intervals for four primary comparisons. At least 126 evaluation dates are needed. Reports retain fitted parameters, baseline results, calibration, stock/year breakdowns, uncertainty, and explicit pass/fail/inconclusive states. An identical frozen dataset/code evaluation returns the existing report and logs access. Repeated historical evaluation is not fresh prospective evidence.

The **watchlist is not the training universe**. The pooled cohort remains blocked pending an independent historical-membership/delisting/feed audit. Single-company experiments remain exploratory.

To expand later: register each verified company identity with `python -m intelligence.cli register --symbol SYMBOL`. The `expand-watchlist` command uses the agreed 25 symbols, but requires MSI/AXON financial ingestion and a real resolved five-session prospective forecast for each first. It does not manufacture that evidence or modify the training universe. Watchlist API changes append a version; verified registry membership is required.

## Phase 2: bounded forecasting research

The original negative reports are preserved. Their exposed evaluation period is
sealed after the phase-2 plan is declared. The new development ledger freezes
MSI/AXON membership, the dataset, shared date boundaries, fourteen causal feature
definitions and nine candidate configurations. Historical-mean, shrunk-volatility,
original linear, calibrated linear ablations, small boosted trees, shallow forests
and a fixed ensemble completed 36 experiments with 7,092 retained predictions.
**No candidate established improvement or received promotion.** New validation
statuses remain inconclusive because development is not fresh confirmation.

From `apps/api`, use `python -m intelligence.cli audit`, `research`, or `monitor`
with this project's virtual environment. `research` reuses completed frozen slots;
it does not tune parameters or rerun a favorable search. Changed research code
requires a new explicit declaration rather than silently reusing the old identity.
Run `python scripts/export_research.py` from the root to export frozen reports.

Model lab shows comparisons, calibration bins, uncertainty, feature groups,
fitted parameters and year/regime slices. Forecast history remains prospective;
Activity reports worker freshness, partial provider results, skipped issuance,
and overdue outcomes. Worker refresh slots are 19:00, 19:30 and 19:50 New York,
with one pending refresh at a time, issuance at 20:30 and monitoring after 20:35.
Scheduled issuance and restart/idempotency behavior have integration tests.

Read [actual metrics and diagnosis](docs/research-phase-2-results.md) and
[source / training-cohort feasibility](docs/data-source-feasibility.md). New final
confirmation requires a predeclared frozen candidate and fresh prospective data;
Phase 3 below now enrolls an experimental shadow; independent validation remains future work.

## Phase 3: information features, bounded Optuna tuning, prospective shadow

New source ingestion covers 1,188 news articles, 312 filing events, nine observed
official IR announcements, and 1,308 XLK/XLI sessions each. Versioned features
respect publication/update availability; live use additionally requires actual
acquisition before cutoff. IR edit histories remain unverified and unavailable
for historical training. Historical news is reconstructed, not certified vintage data.

Eighty Optuna trials completed under a 96-slot predeclared maximum; sixteen IR
slots stayed blocked. Forty candidates were evaluated, eight group records stayed
blocked, and no corrected diagnostic interval demonstrated improvement. Nested
chronological selection, preprocessing, calibration and evaluation remain separate.

The frozen histogram-boosting / market-context shadow is enrolled with first
eligible origin **2026-11-16**. It runs beside the unchanged reference. It is
experimental, not validated. The first 126 paired dates and matured outcomes are
required for one final predeclared comparison; the computer/worker must stay on.

From `apps/api`, use this project's Python environment with `-m intelligence.cli`:
`information` (recent refresh), `information-backfill` (bounded historical news),
`tune` (frozen study replay), `nominate` (idempotent frozen enrollment), or
`shadow-status`. Normal scheduled refresh/issue/resolve jobs include these paths.
The original phase-2 ledger remains intact. Frozen implementation mismatches stop
shadow issuance instead of silently changing the experiment.

Run `python scripts/export_phase3.py` at the root to export read-only evidence.
Read [actual phase-3 results](docs/phase3-results.md),
[predeclaration](docs/phase3-protocol.md), and [source review](docs/phase3-sources.md).

## Vision models (still optional)

- `foduucom/stockmarket-pattern-detection-yolov8`, revision `31bbb9abc9843dc3d9c08349507c178cde56be31`.
- `foduucom/stockmarket-future-prediction`, revision `310919ed9d2d9cce9c1095cd50bb0a04de8c9e68`.

Both show **“Model unavailable — licensing review required.”** No weights or vision dependencies were downloaded. Detector mAP/confidence is not future-return accuracy or probability. After rights, exact weight hash, and isolated runtime validation are independently reviewed, `vision-approve` records the attestation. The optional CPU container has no network, database, or provider credentials. See `docs/architecture.md`; do not enable it merely to remove the blocked message.

## Verification and backups

```powershell
# Local PostgreSQL must be running. Tests create only intelligence_test_* databases on port 5543.
cd apps/api
..\..\.venv\Scripts\python.exe -m pytest -q
cd ../..
.\.venv\Scripts\python.exe -m ruff check apps/api
npm --prefix apps/web run typecheck
npm --prefix apps/web run build

# Create a new backup filename; an isolated restore database is retained for inspection.
.\.venv\Scripts\python.exe scripts/backup.py --output .local/backups/NEW_FILENAME.kfi.zip
```

Backups capture a repeatable-read PostgreSQL snapshot using binary COPY, verify all table counts in a fresh receiving cluster, and write a SHA256 receipt. They contain private data and must stay off Git. The helper never replaces an existing database. A local restore test deliberately records `off_host=false`; it does not satisfy the separate production off-host backup gate.

## Deployment status

Phase 4 is deployed at **https://keftrade.duckdns.org/intelligence** with single-user password authentication. Backend/database ports are private; independent research and paper workers run on the VPS. A new dedicated Alpaca paper account has been verified and the scheduled paper pilot is enabled. Activation completed after the November 13 market close; the first eligible session is Monday, November 16, 2026. No orders were submitted during setup. The $600 allocation is distinct from the actual broker balance.

The premarket pilot is separate from frozen MSI/AXON evening research. Read the [pilot protocol](docs/paper-pilot-v1.md), [operations guide](docs/phase4-operations.md), and [actual deployment report](docs/phase4-results.md). Local development and production use PostgreSQL 18. Both images were built locally and transferred. Live VPS exports were restored off-host. The supplied CI still tests PostgreSQL 16 compatibility and has not run on a hosted service.

Production login details are in ignored `.local/vps-login.txt`. Secrets live in protected server files under `/etc/keftrade-intelligence`. Never use old trader credentials for paper execution. Put dedicated credentials in `paper.env`, then verify the actual account differs from the pinned old account before permitting execution. Changing keys alone is insufficient.

The local root page now opens the paper dashboard. MSI/AXON remain at `/intelligence/companies/MSI` and `/intelligence/companies/AXON`. Local paper development additionally needs an independent `intelligence_paper` database, its URL/token, and the `paperpilot.service:app` server on port 8200. Execution defaults to disabled.

The research namespace has no execution integration. Only isolated `paperpilot` code can submit orders, and its finite client accepts the paper endpoint alone. No real-money execution or new paid subscription is included. Earlier research boundaries remain documented in `docs/architecture.md` and `docs/provenance.json`; Phase 4 operations/results supersede the original deployment-status statements in `docs/deployment.md`.

## Daily news-screened paper pilot v2

The separate paper pilot now supports a frozen daily selection of up to 20 liquid
US stocks beyond the research watchlist. Research begins at 04:30 Pacific, freezes
by 06:00, and numerical forecasts issue after the 06:15 cutoff. Entries are $25,
require a bullish sourced news assessment and estimated positive-return probability
of at least 70%, and retain the $600 total cash-backed exposure limit. It can make
zero trades. This is an experimental estimate, not demonstrated 70% accuracy.
See [the v2 declaration](docs/paper-pilot-v2.md). Groq credentials remain backend-only;
the model receives source excerpts and has no order tools.

## Hugging Face news evaluator

The v3 paper researcher uses a pinned, quantized DistilRoBERTa financial-news
classifier in an isolated CPU worker. It records source-linked sentiment from up
to three articles per candidate and supplies the assessment to Groq. Strongly
negative or unavailable evidence blocks new buys. Sentiment probabilities never
replace or increase numerical return probabilities; the 70% entry threshold and
all cash/risk limits remain. See [the v3 protocol](docs/hf-news-evaluator-v3.md).
The model is for this personal paper-research experiment; no validated return edge
or commercial-rights clearance is claimed.

## Reliability and research improvements v4

The paper researcher now balances source context across all40 screened candidates,
keeps up to3 cited headlines per name, preserves actual pre-cutoff readiness and
price versions, and exposes metrics by model version. An opt-in cost-planning gate
requires expected return to clear the observed IEX spread,10bp slippage allowance
and5bp net margin. Production enables that gate while retaining the70% numerical
threshold and existing cash limits. The fixed QQQ/IWM/TLT numerical challenger
failed its exploratory baseline comparison and remains disabled. See
[actual comparison and limits](docs/improvement-round-v4.md).
#   M a i n k r a f t i k  
 