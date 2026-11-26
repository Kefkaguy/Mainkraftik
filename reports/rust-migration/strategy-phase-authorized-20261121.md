# Authorized strategy phase — gated on migration parity

The latest user direction prioritizes completion of sentiment/discovery verification, then the active two-account engine, online learning, execution safety, service recovery and final migration gates. Additional compatibility work is justified only when it blocks migration or changes observable behavior.

The subsequent strategy phase must stay separate from migration. Existing allocation, market scaling, residual learning and model code are frozen for comparison in `strategy-baseline-20261122T014921267486Z/manifest.json`. The current allocation defect is intentionally retained in that baseline: zero economic score still gets positive exponential weight. The coupled engine test demonstrates it without changing the deployed policy.

Required implementation and evaluation after parity:

- Economically justified no-trade/cash allocation, incremental transaction costs, existing-position inertia, concentration/correlated exposures and calibrated directional probabilities; no forced buying-power utilization.
- Native Rust 60-minute models and causal historical/current 1m/5m OHLCV, 5/15/30/60-minute returns, VWAP/RSI/EMA/MACD/ATR, time-of-day relative volume, volatility/momentum, spread/liquidity, SPY/XLK context and timestamp-safe news/sentiment. Verify actual data permissions and identify delays explicitly. Test feature ablations.
- Native Rust Ridge, Logistic Regression and Histogram Gradient Boosting training/inference with hourly calibration/intervals and measured bounded resource changes. No Python production training fallback.
- Matured-only rolling/recency-weighted/regime-aware residual learning, alternative regularization and incremental-update comparisons. Keep accuracy and execution profitability separate and reject unstable updates.
- Compare 5/15/30/60-minute rebalancing at identical capital/risk, including spreads, slippage, turnover, latency, partial fills, fees and short costs. Monitoring does not imply an order.
- A separate purged chronological walk-forward protocol with genuinely unseen confirmation data, complete net daily/weekly P&L, Sharpe/Sortino, drawdown, profit factor, win rate, turnover, execution costs, accuracy and calibration. Preserve all sealed Phase 7/8 evidence. Reject inadequate evidence.

Only the two existing PAPER accounts may be used. No additional accounts, live money, new Shadow Mode, simultaneous Python/Rust execution against those accounts, unrelated VPS changes or safety-gate bypass. Preserve credentials, histories, databases, models, archives and rollback. Do not activate an unvalidated strategy.

The objective is average net $35/week without increasing leverage, exposure or risk to manufacture that result. It is neither a guarantee nor a model acceptance criterion. This requirements record is not a profitability finding or deployment approval.
