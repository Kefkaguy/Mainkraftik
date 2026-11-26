# Mainkraft autonomous engineering — local implementation

All work is in the existing Intelligence application. No VPS services, credentials,
capital limits, entry thresholds, or deployed paper policies were changed.

## Stage results

| Stage | Local engineering determination | Evidence and limits |
|---|---|---|
| 1: reproduce/isolate lifecycle failures | PASS | Both original parameterized fixtures reproduce zero shares with asset-verification rejection. Engine time was November 17 while universe membership used host time. Freezing the universe clock fixes both; expected .25 shares and risk checks are unchanged. |
| 2: versioned decision traces | PASS | New immutable signal payloads and append-only events carry decision-trace-v1. PASSED/FAILED/NOT_EVALUATED are distinct; risk evaluation retains its original short circuit. Legacy history is untouched. |
| 3: local runtime/fill lifecycle | PASS for deterministic fixture exchange | Actual Engine ticks, a persistent fake exchange and isolated PostgreSQL exercise buy/fill/exit, early close, partial fills, unknown acknowledgement, duplicate stream versions, worker replacement, retries, cash/resource/window/permission/daily-loss restrictions. Independent Decimal ledger replay agrees on cash, inventory and realized P/L. This is not historical quote realism or full upstream LEAN algorithm certification. |
| 4: causal sequence research | PASS implementation; model validation FAIL | 5/15/60 minute OHLCV sequences, learned neural heads, immutable artifacts, purged time splits, untouched fitting/evaluation boundary, baselines and Mainkraft numerical refit. Negative results retained. |
| 5: offline controller and monitoring | PASS implementation | Explicit bounded universe; read-only market data; issuance/availability deadlines; immutable forecasts/outcomes; restart-safe semantic IDs; drift floors; bounded offline refits never replace pinned model. Fixture tests cover both failures and outcomes. |
| 6: existing dashboard integration | PASS build/API checks | Research panel, errors, model comparison and decision traces. Existing actual paper session report retains positions/orders/P&L. API authentication and private bridge access tested. No deployed or browser-session validation claimed. |

The prior external simulator artifact remains `component_reconciled_full_engine_pending`.
It was not overwritten or upgraded. The new tests reconcile the application Engine
against an independent ledger for the declared fixture scenarios. A future release
that requires full upstream LEAN algorithm validation must complete that separate
gate; these local checks do not authorize bypassing it or deploying a policy.

## Failure root cause

`Engine._tick` asks `managed_symbols()` for assets. The simulated session had an
AMD selection on November 17. `universe.selection()` used its own real clock,
which could already be November 18 or any later day, so no selected assets were
loaded. The unchanged asset gate denied the order. The fixture now freezes that
clock to the replay clock and retains the original .25-share assertion. Diagnostic
failure output includes the actual gate events. No expected fills were weakened.

## Trace semantics

`DecisionTrace` observes the existing booleans. It cannot authorize an order.
Signal traces retain all measured signal failures, source IDs and model/policy
identity. Entry traces record evaluated arbitration and risk checks, preserving
NOT_EVALUATED for subsequent checks after rejection. An entry signal is not an
order, and an acknowledged order is not necessarily a fill. Existing approved
trial policy overrides retain both the ordinary probability failure and the
separate policy identity. An absent old trace is NOT_RECORDED, never PASSED.
Off-window events are deduplicated per signal per worker to bound log growth.

## Chart experiment and scientific limits

The fixed experiment uses six existing hash-verified stock snapshots. At 12:00
and 14:00 New York, the model sees only wholly completed bars with the declared
15-minute availability delay. The input contains 12 five-minute, eight fifteen-
minute and two hourly candles. Price/volume normalization uses those causal
windows. There are no trend/momentum eligibility rules. The target is the next
30-minute return, direction, target-before-stop event and downside excursion.
Missing context or outcome candles are rejected without interpolation. Ambiguous
same-bar stop/target touches count as stop first and are explicitly recorded.

Train/calibration/evaluation dates are shared across symbols with one-session
boundary purges. Scaling is fitted only in training. Probability calibration and
residual ranges use calibration only. Neural JSON inference must reconcile to
sklearn before evaluation. A poisoning test changes evaluation labels and verifies
the fitted artifact is unchanged. Baselines are historical mean, zero return, and
the existing Mainkraft ridge/logistic implementation refitted on causal numerical
summaries for the same target. It is not the original deployed day/swing artifact.
Paired comparisons use 2,000 circular twenty-session block resamples.

The latest experiment has 12,077 observations, 55 rejected windows, and 2,436
evaluation observations across 203 dates. Neural return MAE is about .320%, versus
.274% for historical mean and .275% for the Mainkraft numerical refit. Both paired
MAE improvement intervals are negative. Neural Brier error is about .2515 versus
.2500 for the historical baseline. Both heads reached their fixed 150-iteration
budget without convergence. There is no model promotion or profit claim.
Target-first events are rare; class counts are reported so low Brier scores are
not mistaken for useful rare-event discrimination.

The historical suffix is held out from this model's fitting, but prior research
has used those calendar periods. Fresh independent prospective validation is
still necessary. Cost scenarios are hypothetical all-observation return diagnostics,
not a portfolio backtest or realized trading P/L. Artifact records archive source
text, dependencies, data identities, rejected windows, model weights, predictions,
and evaluation results under `.local/chart-intelligence/records`.

## Controller operation

Run from this repository using its existing virtual environment:

```powershell
.venv\Scripts\python.exe scripts/run-chart-research.py
.venv\Scripts\python.exe scripts/monitor-chart-research.py --report-id REPORT_ID --symbols AMD MSFT GOOG AMZN TSM META --watch
```

The report ID is printed by the research command. The monitor is an opt-in local
process, not an installed VPS service. It uses only the existing research market-
data settings and literal Alpaca **market data** endpoints. It never uses broker
execution credentials. Each one-minute poll collects new data; forecasts issue
only within two minutes of declared research decision times. Missing/stale data
records failures; late polls do not backdate forecasts. Mature outcomes attach
separately. SQLite transactions release their writer lock on process death;
semantic keys prevent repeated issuance after restart.

Drift checks require forty distinct dates, compare recent per-date errors with
the frozen reference, and record return-error, calibration and interval-coverage
alerts. They do not establish significance. After at least 220 mature dates,
an alert can trigger one frozen-budget offline refit per forty-date bucket.
That evaluation has its own chronological partitions. It stores a separate
candidate and report; neither the pinned model nor broker behavior changes.

The watcher was exercised with fixtures, not launched against current market
data during this implementation. Current production monitoring and orders
therefore remain the existing deployment's responsibility.

## Verification and deployment gates

```powershell
.venv\Scripts\python.exe -m pytest apps/api/tests -q --tb=short --junitxml=reports/autonomy-implementation/full-tests.xml
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
.venv\Scripts\python.exe scripts/audit-autonomy-evidence.py
```

The report folder contains full test/build evidence and an acceptance inventory
with source/artifact hashes. Dependency deprecation warnings and explicit neural
convergence warnings remain visible. No threshold was changed to turn a negative
experiment positive. Before any deployment: review local diffs and policy parity,
complete the release's external reconciliation requirements, validate the exact
candidate image against the paper bridge, and obtain explicit deployment approval.
The new research module has no execution or promotion route.
