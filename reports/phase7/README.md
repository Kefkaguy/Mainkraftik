# Phase 7 — research and validation results

**Conclusion: no candidate demonstrates a credible advantage after costs.** Engineering tests pass; model/strategy acceptance fails. Nothing was deployed or promoted.

## Experimental separation

Eight candidates were declared before fitting. Training ran from November 2022 to April 11, 2024; calibration from April 15 to October 3, 2024; selection from October 7, 2024 to April 2, 2025. Shared dates and one-session purges separate the splits. The one-time diagnostic audit spans April 3, 2025 to January 23, 2026: 2,418 observations across 203 dates. It was already a calibration period in older research, so it is out of these fits but **not independent final confirmation**. The previously examined January–November 2026 evaluation period was excluded from feature/label construction and selection.

No development policy met the predeclared minimum 30-trade requirement. Historical mean was retained as a forecast diagnostic because it had the lowest selection MAE. It is not a nominated trading strategy. Once the audit was exposed, there was no refitting or parameter selection.

## Audit results

All trading columns below use the fixed 30-minute, assumed-cost scenario. P/L and conservative OHLC-envelope drawdown are dollars on a $600 reference portfolio with $25 entries. Zero trades do not establish profitability.

| Candidate | Return MAE | Brier | Direction accuracy | Trades | Net P/L | Conservative drawdown |
|---|---:|---:|---:|---:|---:|---:|
| historical_mean | 0.241% | 0.2506 | 48.7% | 0 | $0.00 | $0.00 |
| zero_return | 0.242% | 0.2506 | 48.7% | 0 | $0.00 | $0.00 |
| mainkraft_linear | 0.247% | 0.2506 | 48.7% | 0 | $0.00 | $0.00 |
| mainkraft_histboost | 0.248% | 0.2506 | 48.3% | 0 | $0.00 | $0.00 |
| sequence_ridge | 0.250% | 0.2515 | 47.9% | 0 | $0.00 | $0.00 |
| sequence_original150 | 0.303% | 0.2515 | 47.9% | 6 | $-0.93 | $1.16 |
| sequence_adam400 | 0.249% | 0.2515 | 47.9% | 0 | $0.00 | $0.00 |
| sequence_lbfgs250 | 0.289% | 0.2515 | 47.9% | 1 | $-0.16 | $0.16 |
| Fixed always-long benchmark | — | — | — | 806 | $-38.91 | $39.39 |
| Cash benchmark | — | — | — | 0 | $0.00 | $0.00 |

Best forecast: historical mean (0.2414% MAE). Best AI numerical forecast: existing Mainkraft ridge/logistic implementation refitted to this target (0.2466%). Best improved neural return forecast: smaller regularized Adam (0.2492%). Neither beat the simple mean. Directional accuracy was below 50%; audit positive-outcome frequency was 51.3%. This is not evidence of directional skill.

## Convergence diagnosis

The original-style 24/12 return network exhausted 150 iterations; training MAE was 0.1960% versus 0.3108% on calibration. The generalization gap indicates overfitting, so increasing training duration alone is not justified. A smaller 16-unit network, stronger regularization, per-output target scaling and a lower learning rate converged after 199 of 400 iterations: training/calibration MAE 0.2437%/0.2652%. Its audit error improved over the old network but still trailed the mean. The eight-unit LBFGS alternative exhausted 250 iterations and also failed to improve the baseline. Warnings and loss tails remain in the artifacts.

The bounded comparison also tested ridge shrinkage, the existing Mainkraft histogram boosting implementation, train-only clipped sequence scaling, and separate logistic probability heads with calibration-only Platt scaling. The original-style return-head comparator is not a byte-for-byte rerun of the original neural classifier. Learned price/volume windows remain causal and have no preset trend eligibility rule.

## Costs, execution and risk

Base assumed round-trip cost is approximately 22bp: 10bp full spread, 5bp adverse slippage per leg, and 1bp fee allowance per leg. Stress scenarios use 42bp and 82bp. These are declared assumptions, not measured broker fees or fill costs. Admission requires probability >=55% and expected return exceeding assumed cost plus 5bp. Liquidity uses only completed, available dollar volume; participation is capped at 0.1%.

Entries are long-only, $25 or half-sized after a deterministic partial-fill scenario; at most two positions, $100 combined exposure and a $12 daily loss block. No risk was increased. The exchange scenarios include deterministic 5% rejection and 10% half-fill/remainder-cancel assumptions. Stop/target are fixed at 2%/3%. Software observes a completed five-minute bar and exits at the next available bar price; no perfect intrabar stop execution is assumed. Holding periods 15 and 30 minutes were declared in advance; the 15-minute variant uses the same 30-minute forecast and is a diagnostic, not a separately calibrated forecast.

Historical executable quotes were unavailable. Strict quote mode therefore establishes no executable trading advantage. Original zero-opportunity coverage booleans were vacuous; the separate accounting addendum records NOT_EVALUATED or UNAVAILABLE without rewriting the frozen report. Synthetic outcomes cannot replace this evidence.

## Uncertainty, drawdown and conditions

The original-style neural policy lost $0.9307 (-0.1551% of reference capital) across six audit trades. Its 98.75% paired twenty-date circular bootstrap interval for average daily net P/L is [-$0.01315, +$0.000183]; the approximate fixed-203-date cumulative return interval is [-0.445%, +0.0062%]. That includes zero and is based on only six trades. LBFGS lost $0.1622 across one trade; its daily mean interval is [-$0.00320, $0]. These sparse results cannot validate an edge. Zero-trade intervals [0,0] describe no exposure and have no positive evidential meaning.

The original-style policy lost $0.49 with 15-minute holding and $0.93 with 30 minutes. The 42bp scenario lost $0.58 across four trades; at 82bp no trades qualified. The always-long benchmark lost $38.91 (-6.48%), with a daily net P/L interval [-$0.2252,-$0.1584] and descriptive daily Sharpe -13.43. The original-style policy Sharpe was -2.05; sparse activity makes it unstable. Cash outperformed the active policies.

Pointwise intervals are descriptive and not a simultaneous correction for all eight models, two holding variants and three cost scenarios. They do not establish significance after search selection. Conservative OHLC-envelope drawdown marks combined positions, separately from realized-exit accounting; unknown intrabar ordering means the true tradable drawdown cannot be certified.

The six neural trades lost $0.25 in high-volatility/down local conditions and $0.70 in high-volatility/up conditions, earned $0.019 in low-volatility/up conditions, and had no low-volatility/down trades. These causal stock-local buckets are not a macro regime model. Counts are too small for a stable regime conclusion. All four always-long buckets lost money.

Forecast reliability also remains limited: the mean reference interval covered 91.2% of audit outcomes; neural alternatives covered about 90.3–90.7%. Calibration-bin counts and probabilities are in research-results.json. Only two target-before-stop outcomes occurred in the audit; a tiny target Brier score mostly reflects rarity and cannot prove useful target prediction.

## Prospective validation and paper observation

Enrollment was frozen at 2026-11-18 06:18 UTC (November 17 Pacific). A real collection for all six stocks succeeded at 06:19 UTC after fixing a delayed-SIP 403: requests must end outside the recent 15-minute subscription window. The SIP feed and existing market-data credentials were preserved. The controller records actual completion time after inference and requires post-prediction quotes to price hypothetical entries and timely observed exit quotes. Missing quotes produce unpriced outcomes.

No research issuance window occurred during this work: zero genuinely prospective forecasts, zero resolved prospective outcomes. No historical observation was relabeled prospective. At least 126 fresh dates remain required for independent confirmation. The watcher is implemented and tested but was not installed or left running as a service. See docs/phase7-research.md for the existing local watcher command.

Read-only VPS observations during this task still show 35 legacy decisions, zero new strategy signals, zero order intents/versions, no positions, and $0 realized/unrealized paper P/L. The November 18 trial’s first entry window had not occurred at observation time. This is the application ledger, not an independent direct broker-history audit. No controls, credentials, limits, deployed policies or unrelated services were changed.

## Verification and artifacts

Full backend suite: 209 passed. Final focused regression checks and frontend build/type checking passed. Evidence hashes and replay/export checks are in acceptance.json. The dashboard’s Phase 7 panel distinguishes assumed-cost scenarios from actual Alpaca Paper results.

- research-results.json: compact frozen model, prediction and strategy results.
- accounting-addendum.json: conservative portfolio marks and quote-coverage clarification.
- prospective-enrollment.json / prospective-status.json: enrollment and actual prospective availability.
- paper-observation-*.json: timestamped projected VPS observations.
- full-tests.txt / full-tests.xml / final-focused-tests.xml / web-build.txt: automated verification.
- .local/phase7/records: immutable declarations, data, model artifacts, audit forecasts and full trade/order ledgers.

**Acceptance: FAIL — no credible, independently validated cost-adjusted strategy.** Engineering improvements are implemented; fresh validation and executable-cost evidence remain outstanding. No model promotion or deployment is authorized.
