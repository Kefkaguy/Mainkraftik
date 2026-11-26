# Phase 8 — Trading Edge Discovery

**No profitable trading advantage has been validated.** All five horizons failed the preregistered development nomination gates. There is no untouched historical confirmation set in the available evidence.

## Boundaries and design

Phase 7 files, models, forecasts, reports and enrollment are frozen by a SHA-256 inventory. Existing prospective ledger rows are also hash-checked; new observations may append without changing them. No rejected candidate was promoted. Phase 8 fits new targets and produces separate model identities.

The fixed search contains five horizons × five methods = 25 candidates: historical mean, zero return, the existing Mainkraft ridge/logistic and histogram-boosting implementations, and one small regularized neural sequence model. There is no adaptive hyperparameter search. Inputs include causal OHLCV sequences and SPY/XLK context. Historical news is excluded because acquisition-safe versions do not cover this development period.

Only data through April 2, 2025 enters development. The April-2025–January-2026 Phase 7 audit and the previously exposed later 2026 evaluation are excluded from feature/label construction and selection. Older development prices were already used in earlier research; changing horizon does not turn them into independent confirmation.

Training origins end April 11, 2024; calibration begins April 22 and ends October 3; selection begins October 14. Five-session gaps separate origin partitions, and every training/calibration label end precedes the next partition decision. Swing rows whose outcomes extend beyond the development boundary are rejected.

One-hour forecasts use 12:00/14:00 New York cutoff-safe intraday sequences. Daily/swing forecasts use completed-session candles and market context at 20:30, with next-session 09:35 entry and the final target-session close-minus-15-minute exit. Two-, three- and five-session targets are separately fitted. Availability delay remains fifteen minutes; historical actual delivery and adjustment revisions remain limitations.

## Complete base-cost screening results

All values below are hypothetical, on a $600 reference with $25 entries. Qualified signals precede execution/risk rejection. Zero trades are not positive evidence. Drawdown marks the shared portfolio; unresolved positions are included at their last observed price and separately counted.

| Horizon | Method | Qualified | Closed trades | Net P/L | Marked max drawdown | Direction accuracy | Brier |
|---|---|---:|---:|---:|---:|---:|---:|
| one_hour | historical_mean | 0 | 0 | $0.00 | $0.00 | 51.6% | 0.2498 |
| one_hour | zero_return | 0 | 0 | $0.00 | $0.00 | 51.6% | 0.2498 |
| one_hour | mainkraft_linear | 0 | 0 | $0.00 | $0.00 | 51.6% | 0.2498 |
| one_hour | mainkraft_histboost | 0 | 0 | $0.00 | $0.00 | 51.6% | 0.2498 |
| one_hour | sequence_adam400 | 21 | 13 | $-0.96 | $0.96 | 50.2% | 0.2504 |
| one_day | historical_mean | 0 | 0 | $0.00 | $0.00 | 48.6% | 0.2505 |
| one_day | zero_return | 0 | 0 | $0.00 | $0.00 | 48.6% | 0.2505 |
| one_day | mainkraft_linear | 0 | 0 | $0.00 | $0.00 | 50.4% | 0.2500 |
| one_day | mainkraft_histboost | 0 | 0 | $0.00 | $0.00 | 48.9% | 0.2509 |
| one_day | sequence_adam400 | 0 | 0 | $0.00 | $0.00 | 51.7% | 0.2502 |
| two_sessions | historical_mean | 0 | 0 | $0.00 | $0.00 | 50.6% | 0.2504 |
| two_sessions | zero_return | 0 | 0 | $0.00 | $0.00 | 50.6% | 0.2504 |
| two_sessions | mainkraft_linear | 335 | 99 | $-4.03 | $10.28 | 50.1% | 0.2540 |
| two_sessions | mainkraft_histboost | 152 | 65 | $-1.23 | $6.06 | 51.7% | 0.2503 |
| two_sessions | sequence_adam400 | 1 | 1 | $-0.50 | $0.67 | 50.6% | 0.2502 |
| three_sessions | historical_mean | 0 | 0 | $0.00 | $0.00 | 47.8% | 0.2546 |
| three_sessions | zero_return | 0 | 0 | $0.00 | $0.00 | 47.8% | 0.2546 |
| three_sessions | mainkraft_linear | 434 | 114 | $-5.42 | $9.22 | 45.9% | 0.2611 |
| three_sessions | mainkraft_histboost | 280 | 77 | $-0.92 | $5.87 | 47.8% | 0.2539 |
| three_sessions | sequence_adam400 | 121 | 55 | $-4.38 | $9.09 | 47.8% | 0.2542 |
| five_sessions | historical_mean | 0 | 0 | $0.00 | $0.00 | 47.0% | 0.2554 |
| five_sessions | zero_return | 0 | 0 | $0.00 | $0.00 | 47.0% | 0.2554 |
| five_sessions | mainkraft_linear | 399 | 106 | $-7.57 | $11.76 | 46.0% | 0.2580 |
| five_sessions | mainkraft_histboost | 237 | 63 | $-0.25 | $5.35 | 48.2% | 0.2564 |
| five_sessions | sequence_adam400 | 152 | 47 | $-1.23 | $8.10 | 47.0% | 0.2547 |

## Benchmarks and uncertainty

| Horizon | Risk-matched always-long P/L / closed trades | $25 SPY + cash context P/L |
|---|---:|---:|
| one_hour | $-23.49 / 458 | $-0.77 |
| one_day | $-14.52 / 232 | $-0.91 |
| two_sessions | $-9.37 / 143 | $-0.91 |
| three_sessions | $-14.68 / 125 | $-0.91 |
| five_sessions | $-11.45 / 111 | $-0.91 |

Cash returns $0 with no exposure. The risk-matched always-long benchmark shares entry times, capital, concurrency, stops, exit rules, costs and fill scenarios. The SPY comparison is supplemental daily-price buy/hold context, not a timing-matched or dividend-inclusive alpha test.

The least-loss active base result was five-session histogram boosting: 63 closed trades, -$0.25 net, about $5.35 marked maximum drawdown. Its family-adjusted average daily P/L interval is approximately [-$0.1243,+$0.1137]. It beats a losing risk-matched always-long benchmark in point P/L but fails to establish positive returns versus cash. It was not nominated. One-day models generated no qualified opportunities; one-hour neural activity produced only 13 closed trades and lost about $0.96.

Intervals use 5,000 circular twenty-session block resamples and alpha=.05/25 (99.8% point intervals for the declared 25-candidate family). All no-trade days stay in denominators. Selection-period reuse, serial dependence and scenario assumptions still prevent these intervals from becoming independent confirmation. Thirty trades is only a screening floor; the declared fresh confirmation requirement is 126 dates and 100 closed trades, positive cost-adjusted lower bounds versus cash and risk-matched passive, observed quote coverage and stress survival. No horizon passed even the preliminary gates.

One two-session linear run retains an unresolved position at the historical boundary. Its mark is included in net P/L but is not treated as a closed trade or liquidatable quote. This independently blocks acceptance; no forced end-of-data liquidation was invented. A separate partial-fill-accounting.json addendum corrects the attribution of partial realized sales versus remaining unrealized inventory; original net P/L and selections are unchanged. Future replay code uses this tested attribution. Full conservative OHLC drawdown, order states and daily net P/L remain in the immutable full report.

## Costs, liquidity and execution coverage

Base round-trip assumptions total about 22bp (10bp full spread, 5bp slippage each leg, 1bp fee allowance each leg); stress scenarios use 42bp and 82bp. These are not measured broker fees or slippage. All 75 candidate/cost combinations have nonpositive net P/L. Tightened cost gates sometimes suppress all trades; zero exposure is not evidence of profitability.

Research limits remain $25 entries, at most two positions, $100 total exposure, no averaging down, no borrowing, one attempt per stock/session and a $12 daily loss latch. Historical liquidity checks use only available dollar volume at 0.1% participation. Partial entries cancel the remainder; partial/rejected exits are retried at subsequent observed opens, with three attempts per session. Stops/targets remain 2%/3%; completed-bar observations schedule next-open execution instead of receiving perfect intrabar fills. Overnight gaps and unsettled end positions are marked rather than hidden.

Strict executable-quote coverage is unavailable for historical candidates. Actual quote observations are audited separately for age, bid/ask validity and feed. IEX is a single venue, not NBBO, depth or a fill guarantee. Missing/stale observations never become executable fills. Quoted spread can be observed; realized slippage, queue position and market impact are still unmeasured.

## Calibration and market conditions

All five neural fits converged within their fixed budgets, so failure is not explained solely by optimizer warnings. Neural direction accuracy ranges from about 47.0% to 51.7%; Brier ranges from .2502 to .2547. Convergence and classification accuracy did not earn the complex model a trading nomination. Interval coverage, class counts, calibration bins, return and downside errors for every method are retained in research-results.json.

Regime P/L uses causal stock volatility and SPY direction buckets, never future regime labels. Counts and P/L are retained for all four buckets and every candidate/cost scenario. Those reused development buckets do not establish stability through unseen market conditions. A model with no trades supplies no regime evidence.

## Prospective collection and paper separation

The original Phase 7 enrollment remains unchanged. A separate append-only Phase 8 enrollment records baseline diagnostic forecasts because no strategy was nominated; it does not promote or deploy a candidate. Tests verify timestamps before targets, issuance deadlines, stale input rejection, restarts, missing-bar coverage, and exact hourly/one-to-five-session endpoint resolution. Long-horizon resolutions use intraday endpoints, never substitute daily close for 09:35/pre-close labels.

A real six-stock collection was performed successfully, but outside the preregistered issuance windows. No past forecast was backdated. Prospective counts and actual fresh/stale quote counts are in prospective-status.json and quote-coverage.json. Future independent confirmation remains pending. New-horizon individual hypothetical quote outcomes require timely post-prediction quotes and are distinct from any allocated portfolio or broker fill.

No persistent service was installed or silently started. The explicit local watcher is scripts/watch-phase8.py; --watch opts into polling. It retains evidence and stops at storage/free-space limits instead of deleting research. The current one-shot check is recorded in forward-run-*.json.

A read-only VPS application-ledger observation on November 18 found the existing swing trial holding roughly $800 of UBS, with $0 realized P/L and approximately -$6.78 unrealized P/L. One entry intent and six order versions were present. That observation is separate from Phase 8 simulations and was not a direct broker-history audit. No entry thresholds, policy, capital, credentials, orders, Minecraft or unrelated VPS service were modified.

## Deployment blockers and reproducibility

No strategy is nominated. Blockers include negative or zero net screening outcomes, lack of untouched independent confirmation, lack of historical executable quotes and measured slippage, revised/survivor data, synthetic fill assumptions and the unresolved historical position in one run. No automatic promotion or new deployment is authorized.

- preregistration.json: fixed candidate count, splits, risk, costs and acceptance gates.
- research-results.json: all 25 candidates and all cost scenarios, including negative/zero outcomes.
- .local/phase8/compressed: immutable full data, forecasts, rejection ledger, orders/trades, daily marks and results.
- phase7-freeze.json: frozen Phase 7 hashes and pre-existing prospective row identities.
- passive-context.json, prospective-status.json, quote-coverage.json and paper-observation.json: separately labeled addenda.
- full-tests.xml and acceptance.json: automated checks and final source/evidence hashes.

**Final research determination: no profitable trading advantage has been validated at any tested horizon.**
