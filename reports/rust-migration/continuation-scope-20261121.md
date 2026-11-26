# Current full Mainkraft continuation scope

Continue the existing Rust migration from its accepted checkpoint; do not rebuild completed components or the frontend. The active two-account Alpaca PAPER engine, fixed-model inference, online residual learning, execution/reconciliation and research/training workflows take priority. Existing frozen models and Phase 7/8 negative evidence remain authoritative baselines.

The requested final system includes the complete migration, meaningful whole-service CPU/RAM/latency/throughput benchmarks, recovery and rollback verification, and a prepared final deployment. Local component passes do not constitute full migration parity or deployment approval.

After migration parity, implement and test a separate direct-intraday research phase:

- Acquire causally available one-minute and five-minute Alpaca candles and retain their actual availability/acquisition provenance.
- Train directly against one-hour outcomes, replacing the daily-return/6.5 approximation only after separate validation. Keep the original daily frozen models as baselines.
- Evaluate RSI, MACD, EMA, VWAP, ATR, relative volume, momentum and market-context features. Fit preprocessing and calibration on training/development partitions only. Features are candidates, not mandatory hand-written trend gates.
- Compare faithful Ridge, Logistic Regression and HistGradientBoosting implementations on chronological, purged partitions and untouched confirmation data. Do not reuse the exposed Phase 7/8 audit period for selection.
- Compare 5/15/30/60-minute rebalancing under the same risk/capital assumptions. Include observable spreads, slippage, turnover, applicable costs, liquidity, rejected/partial/unfilled orders and unavailable executable quotes. Missing execution evidence must remain explicit.
- Measure prediction error/calibration and cost-adjusted trading outcomes separately. Evaluate whether residual learning and skipping low-value trades improve net results; improved accuracy alone does not qualify a strategy.

Use only the existing two PAPER accounts for authorized prospective evaluation. There are no additional accounts, new Shadow Mode or live-money endpoints. Record prospective predictions before their outcomes; preserve original histories and account identities. Modified strategies can activate only after both migration parity and independent strategy validation. There is no automatic model promotion or risk escalation to meet a target.

The average weekly profit objective is **$35**, not a guaranteed return or a model-acceptance threshold. Report weekly net outcomes and uncertainty against cash/passive/frozen-model benchmarks at fixed comparable risk. Do not tune capital, costs, trade counts or acceptance criteria to manufacture the target. Thirty trades remains a screening minimum rather than proof of an edge. If an improvement fails, preserve and report the negative result and keep it out of deployment.

The currently deployed paper policy and brokerage credentials remain unchanged during the migration. Minecraft, older KefTrade and unrelated VPS services are outside scope. Never run Python and Rust trading engines against the same accounts simultaneously. Preparing deployment is authorized; this document does not claim that deployment prerequisites have passed.
