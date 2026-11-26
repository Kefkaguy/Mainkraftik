# Native hourly implementation and measured results — November 21, 2026 (Pacific)

The local strategy corrections are implemented and evaluated. **No profitable trading advantage has been validated. No strategy or service was deployed.** The existing Python two-account PAPER service remains unchanged. Full migration and deployment acceptance remain incomplete.

## Implemented

- `src/intraday/allocation.rs`: a separate cost-aware allocator, cash holdings, positive net-edge entry admission, incremental rather than sunk transaction costs, existing-position reservations, concentration/group limits and optional calibrated probability sizing. No exponential normalization. Rejected/missing entry predictions still allow stop, holding-period and exposure management of owned positions. Original allocation/model/learning/market sources are preserved in the immutable baseline manifest below.
- `src/intraday/features.rs` and the new `market.rs` methods: explicit 1m/5m Alpaca candle acquisition and causal hourly features: 5/15/30/60m returns, EMA, MACD, RSI, VWAP, ATR, intraday volatility/momentum, time-of-day relative volume, spread/liquidity, SPY/XLK context and timestamp-valid sentiment. Missing history/news/quotes are explicit. No daily-return division by 6.5 in the new strategy. Historical availability reconstruction is labeled; it is not prospective evidence.
- `src/intraday/training.rs` and `models.rs`: bounded native Rust Ridge, Logistic and histogram gradient boosting, training/calibration separation, return and probability calibration, residual uncertainty intervals and feed/feature-version-pinned artifacts. The planner consumes exactly these research model artifacts. The legacy trainer retains its alpha=1 default; configurable regularization is additive and its existing numerical parity tests passed.
- `src/intraday/learning.rs`: incremental sufficient statistics, rolling windows, exponential recency weighting, duplicate prevention, serialized recovery, matured/valid outcomes only and bounded corrections. The research comparison includes all-history, rolling 256/1024, recency 60/240 minutes and regime-aware 256 observations, each at alpha 1/10/100. Adoption requires forecast and paired net-return evidence; none was adopted.
- `src/intraday/engine.rs`: continuous position checks separated from session-anchored 5/15/30/60m entry/rebalance cadence, two pinned account identities and owned-symbol retention on feature/model failure. **This hourly planner is not connected to active brokerage submission.** The new service entrypoint executes only the preserved original policy.
- `src/intraday/evaluation.rs`, `research.rs`, private `study.rs`: event-driven hypothetical portfolios, future executable quotes, latency, partial fills, pending-order reservations, TTL/duplicate handling, spread, slippage, fees and borrow-cost support. Reports include daily/weekly net P/L, marked versus realized results, drawdown, Sharpe/Sortino, profit factor, win rate, turnover, cost components, calibration and execution coverage. Unknown historical borrow availability blocks new short entries.
- `runtime.rs`, CLI `paper-service` and `check-paper-health`: explicit existing configuration, original frozen model, exactly two existing account identities, existing-ledger checks, private submission authority and supervised shutdown. Startup requires a hash-pinned receipt matching binary, model, database and accounts, all mandatory release gates and explicit approval. An unapproved receipt cannot start trading.
- `deploy/rust-paper.*` and `scripts/package-rust-paper-release.py`: candidate image/override and rollback packet replacing only the existing PAPER service. The packet does not deploy or approve itself. `scripts/evaluate-rust-hourly-study.py` provides offline native-study reproduction; Python performs orchestration and resource measurement only.

## Real-data experiments

The bounded search was declared before fitting. The historical development window is February 3–April 2, 2025. April 3, 2025 through declaration is excluded from selection, preserving the exposed Phase 7/8 audits. These are development walk-forward results, **not untouched independent confirmation**. There are 20 training, five calibration and five test sessions per fold, with overlapping labels purged by actual outcome-availability time. Four folds produced 48 fitted artifacts, 12 prediction sets, 96 strategy configurations, 18 adaptive comparisons and three additional cost stresses. There were no training failures in the SIP run.

1. **IEX arm: failed data screen.** 145,950 downloaded 1m/5m candles produced only 56 valid hourly samples across six sessions under the strict contiguous-bar/context requirements. No models were fitted. The failed run is preserved at `.local/intraday-study-runs/20261122T065747223104Z/`.
2. **Separately declared historical SIP research arm:** 288,251 1m/5m candles, 42 sessions, 13,224 usable samples and 5,406 out-of-fold predictions per configuration. SIP was explicitly selected; no current-feed fallback was made. Report: `.local/intraday-study-runs/20261122T070817098923Z/report-79d2004f493fe9016f66f4bfeddf79ca858cf5688f7ce06b41cba37fac51c6bf.json`.

Best hourly forecast MAE within each family (a development selection, not evidence for promotion):

| Forecast | MAE, basis points | Direction accuracy | Brier | Interval coverage |
|---|---:|---:|---:|---:|
| Zero return / 50% probability | 41.73 | 51.26% | 0.25000 | 92.19%* |
| Historical hourly mean | 42.43 | 50.37% | 0.25324 | 92.19%* |
| Ridge, alpha 1, price/volume/context | 42.36 | 50.04% | 0.25335 | 92.19% |
| Logistic, alpha 1, price/volume/context | 42.11 | 50.04% | 0.25335 | 92.43% |
| Histogram boosting, alpha 10, price/volume/context | 42.24 | 50.06% | 0.25629 | 92.36% |

*Reference forecasts reuse the selected candidate's intervals; these interval figures are not independently fitted baseline intervals. The zero-return reference classifies a 50% probability as up when computing direction accuracy; it has no learned directional skill. All three model families failed to beat its MAE or Brier score. More indicators and boosting have not earned activation.

## Execution and profitability: failed qualification

Only one historical hour of IEX quote evidence was collected, with an XLK page-limit failure retained. There are 419 indexed quote samples including context; executable coverage over all monitored session minutes is **0.9025%**, versus the required 95%. No artificial spread or executable quote was filled in.

- Highest marked development result: Ridge, alpha 1, price/volume/context, 15m cadence, unweighted probability sizing: **+$0.9623 on $1,000**, entirely unrealized, **zero closed trades**, three open positions, four partial fills and $0.3910 estimated execution costs. Its 444 unobserved-risk events and missing exit quotes disqualify it. No executable winning strategy is selected.
- Across all 96 configurations: raw marked P/L **−$6.1596 to +$0.9623**; maximum completed trade count **one**, far below 30. The worst raw result included −$6.3657 realized, +$0.2061 unrealized and $4.2382 costs. Complete negative results are retained.
- The highest-mark candidate's raw drawdown was 0.0978%, turnover 0.2822 and mechanically calculated Sharpe 3.9686. **These are not qualified performance estimates:** missing quotes produce long flat periods and stale marks. Profit factor and win rate are undefined for that candidate; no reliable net weekly return or confidence bound is established. Its raw block-bootstrap weekly mean is $0.2830, range $0–$1.1321, explicitly `inference_permitted=false`.
- Cadence maxima were +$0.7967 / +$0.9623 / +$0.5235 / $0 for 5/15/30/60m, respectively. These incomplete marked results do not justify choosing a cadence. Probability weighting has no demonstrated net advantage.
- Slippage stress on the highest-mark candidate: 1bps +$0.9905, 5bps +$0.8777, 10bps $0 (no qualifying entries); all fail execution coverage. No acceptance criteria or exposure were changed.
- All 18 adaptive variants failed the development update guard. Even their lowest MAE, 43.32bps, was worse than the frozen selected Ridge's 42.36bps. No adaptation was accepted.
- Selected Ridge directional accuracy varied from 45.80% to 54.08% across four market/volatility regimes. Regime-level executable P/L remains unavailable; forecast labels are not substituted for trade returns.
- Cash returned $0. SPY gross price context was +0.9385% / +$9.3851 at the same capital; this is not an executable net benchmark and excludes dividends. The original frozen model was trained in November 2026, so applying it to March 2025 would introduce future information. Its net comparison requires prospective evidence.

**The $35 average net weekly objective is unproven. No candidate passes.**

## Focused verification and mandatory release gates

- Five new strategy tests passed on Windows and Linux: causal features/news, all three model families and artifact round-trips, cash/cost/risk management after rejected forecasts, incremental versus batch learning/maturity/adoption guards, and quote-backed partial-fill/fee replay. These are synthetic correctness tests, not profitability evidence.
- Two startup/receipt safety tests passed on both platforms. The additional explicit-feed declaration test passed: risk/cost tampering and protected-period expansion are rejected. Existing numerical trainer parity: two tests passed. Accepted broad migration suites were not repeated.
- Final Windows formatting and strict Clippy passed. Final Linux focused declaration check, strict Clippy and optimized release build passed. Prior accepted order/restart/0.25-share and Python↔Rust handoff evidence is referenced, not rerun.
- The complete offline SIP research process took **68.23s wall / 66.17s CPU**, peak Windows working set **765.37MiB**. This is training/research measurement, not VPS service performance. It exceeds the existing 512MiB PAPER container budget; no resource limit was raised and training was not placed in that service.
- Native Linux binary requires glibc at most 2.34; the exact running original image reports glibc 2.41/x86_64. Linux CLI help and compose override syntax pass. These checks do not prove full container health or resource behavior.
- Release packet: `.local/rust-paper-packets/20261122T071409483479Z/manifest.json`. Linux binary SHA256: `73801602a8dbc377e83519c0f80c3e975923ce31003841f9ad978e929c65a2f8`. It pins the existing two account identities and original image, contains an unapproved receipt and preserves rollback instructions. It does not enable the new hourly strategy.
- **Deployment blocked:** local Docker daemon unavailable; candidate image build, actual container health/resource/shutdown checks and complete container rollback/restore rehearsal are not verified. The Python worker remains running; no replacement was started. New hourly broker selection/activation and prospective confirmation are also incomplete.

Current data probe: recent SIP requests return HTTP 403. IEX requests are authorized, but fresh open-market availability was not established on Saturday. Historical SIP access does not grant current SIP permission. No delayed-data substitution was made. Historical news coverage is absent, historical borrow availability is unknown, exposure uses a conservative shared correlation group rather than an empirical correlation matrix, and independent confirmation still requires genuinely fresh observations and at least 60 sessions. No new persistent watcher, Shadow Mode or account was created.

## Preservation and reproduction

Original baseline: `strategy-baseline-20261122T014921267486Z/manifest.json`. Accepted migration checkpoint: `checkpoints/20261122T044616179363Z/manifest.json`; accepted recovery supplement: `recovery-combined-checkpoints/20261122T050710246272Z/manifest.json`. They remain immutable and are not represented as tests of the changed strategy.

Final preservation check: **40 Phase 7, 38 research, 27 compressed Phase 8 and 106 original Python files unchanged**. Source ZIP, new evidence hashes, complete configuration summary and original experiment input hashes are in the adjacent intraday checkpoint manifest. Model artifacts and forecasts remain in the content-addressed native report; historical predictions were not overwritten.

Offline reproduction (new output directory required; repeating development is not independent confirmation):

```powershell
.venv/Scripts/python.exe scripts/evaluate-rust-hourly-study.py --binary apps/backend-rust/target/release/mainkraft.exe --feed sip --dataset-directory .local/intraday-research/20261122T061658859788Z --quote-dataset .local/intraday-research/20261122T061035773281Z/dataset-bca639c8ab60363708b8da3508d6242f271e1b6c67bfc014470af81a5e88c5af.json --output .local/intraday-reproduction-new
```

Quote units follow the [Alpaca historical quote specification](https://docs.alpaca.markets/us/reference/stockquotesingle-1). Planning fees use the [2026 published schedule](https://files.alpaca.markets/disclosures/BrokFeeSched.pdf); they are explicitly prospective assumptions applied to historical prices, not claims about historical invoices or actual PAPER charges.
