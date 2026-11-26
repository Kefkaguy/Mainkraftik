# Native multi-strategy implementation — November 22, 2026

**Implemented locally and evaluated. No profitable advantage validated; no strategy activated or service deployed.** The existing two-account PAPER image, model, credentials and trading policy remain unchanged. No broker API requests were made during this work.

## Implemented system

New native modules are in `apps/backend-rust/src/multistrategy/`. They reuse the existing calendar, market-data transport, cost assumptions, numerical training and immutable artifact storage. The legacy trading engine is preserved.

- `regime.rs`: closed, session-anchored 1m/5m/15m/1h frames from actual one-minute candles; incomplete constituents and forming candles are excluded. Uses price structure, EMA 8/20, RSI 14, MACD 12/26/9, session VWAP, ATR 14, momentum and relative volume against prior same-time-of-day observations. Identifies bullish, bearish, sideways and high-volatility conditions. Requires 35 complete bars per timeframe and continuous recent minute history. Historical reconstruction and feed identity are explicit.
- `signals.rs`: five separate strategies, versioned signal identities, features, entry/no-trade reasons, stops, targets, structural invalidation and holding limits. Stops are bounded at 2% and targets at 3%; sizing never increases leverage to meet a profit goal.
- `learning.rs`: native Ridge estimates **net strategy-outcome returns**; Logistic estimates **probability that the specific strategy trade succeeds after costs**. Probability calibration and an empirical downside-residual bound are separate. The model does not simply label future stock direction. Fixed alpha 10; at least 100 training labels, 30 calibration labels, both outcome classes and 20 training examples for the strategy being scored. Only completed outcomes are learned. Training outcomes overlapping calibration are purged. Entry also requires positive estimated net return, success probability above 50% and positive residual lower estimate. No automatic promotion.
- `execution.rs`: independent strategy books and a shared-capital portfolio, cash, position ownership, partial fills, costs, immutable fill identities, restart recovery, once-only observations and exits independent of ML entry admission. Decision batches freeze pre-decision marks and capital reservations; later fills cannot influence another simultaneous allocation. Entry remainders are canceled after the modeled fill opportunity; partial exit remainders stay owned and are monitored again. These are hypothetical historical fills, not broker fills.
- `attribution.rs`: separate reconciled PAPER-fill ledger, exactly two configured account identities, immutable order-to-strategy attribution, duplicate/conflicting execution protection, partial-fill accounting, realized/unrealized net P/L, observed costs, win rate, realized drawdown and timestamped marked drawdown. Content-addressed checkpoints enforce account identity on restore. Missing marks/cost references remain unknown. **No existing untagged orders were relabeled, and no actual new-strategy PAPER fills exist.** The ledger is not connected to a newly activated policy.
- `quotes.rs` and new `market.rs::point_quote`: bounded, feed-explicit, resumable historical requests for real quotes immediately before and after each declared decision point. This avoids downloading millions of intervening ticks. Quote timestamps, sizes, feed, point identity and artifact hashes are checked; indices are atomically published. No missing quote or fill is synthesized.
- `study.rs` and CLI: `multi-quote-declare`, `multi-quote-collect`, `multi-evaluate`; chronological, purged strategy-outcome training and common-window rule-only/ML comparisons. Separate plans may be joined with retained provenance and overlap rejection. No broker transport or activation is exposed by these commands.

## Strategy contracts

All strategies reject high-volatility regimes, missing/stale quotes, spreads above 30bps, invalid entry geometry and entries within the final 65 minutes of the session. Bearish regimes reject new longs; historical borrow availability is unverified, so short entries are disabled. Rule-only baselines also require that the target covers estimated transaction costs. Cash is allowed.

| Strategy | Entry | Structural invalidation | Exit |
|---|---|---|---|
| Trend Following | Bullish alignment, 5m EMA trend and higher structure, above VWAP, 1m RSI 50–70 | Below issuance VWAP | Stop, target or 60 minutes |
| Breakout | Above the previous 20 completed 5m bars' high, relative volume ≥1.2, above VWAP | Below broken range high | Stop, target or 30 minutes |
| Mean Reversion | Sideways regime, RSI <35, below VWAP by at least one 1m ATR, observable rebound | Below entry reference minus one 1m ATR | VWAP target, stop or 30 minutes |
| Pullback | Bullish 1h/5m EMA structure, pullback near 5m EMAs, RSI 35–55 and rebound | Below issuance 5m slow EMA | Stop, target or 60 minutes |
| Momentum | 5m momentum above normalized ATR, positive MACD confirmation, relative volume ≥1.2, RSI 55–75 | Below entry reference minus one 1m ATR | Stop, target or 30 minutes |

The shared research book starts with $1,000, permits at most 40% correlated gross exposure and 20% per stock, and uses no leverage. Each standalone strategy receives the same assumptions; their results must not be added together as if independently funded inside the combined portfolio.

## Quote access and coverage

The first declared engineering pilot covered March 24–26, 2025: **1,386 paired points / 2,772 SIP quotes / 100% declared-grid coverage**. It produced 152 closed outcome labels in its original implementation but refused the original training split. That report remains unchanged.

A separately recorded extension added March 27–28 without changing strategy thresholds, costs, capital or risk. Joined coverage is **2,310 paired points / 4,620 real SIP quotes / 100%** over all six monitored stocks, March 24–28. Before/after observations use the same declared feed. Both archives resumed entirely from cache: **2,310 cached points, zero new API requests, unchanged index bytes**, with real credentials unavailable to that verification process.

This resolves the prior tick-pagination failure for the **declared five-minute pilot grid**. It does not establish continuous intrabar quotes or fill the previous 42-session hourly experiment's missing execution data. Stops between observed points cannot be assigned invented crossing/fill prices. Historical publication latency is explicitly reconstructed as one second.

Current-access probe at 10:23 UTC November 22: recent SIP requests still return **HTTP 403**; IEX requests are authorized but return no recent Sunday bars. Historical SIP permission is not current SIP permission. No feed fallback, purchase, credential change or additional account was used. Quote request semantics and historical size units follow the [Alpaca quote specification](https://docs.alpaca.markets/us/reference/stockquotesingle-1).

## Financial evaluation

The final implementation generated **194 signals and 181 completed quote-backed labels**. Thirteen signals did not produce a fully closed eligible label and were excluded, not recorded as invented wins/losses. Regime observations: 240 bullish, 403 bearish, 1,487 sideways; the pilot did not supply high-volatility-regime financial evidence.

The chronological calibration rule uses the latest 30 matured labels before each test-session open, preserving ties and purging training outcomes whose known times overlap the first calibration issuance. This count-based correction and its source hashes were recorded in an addendum before the extension's financial results were inspected. Minimum sample counts and economic gates were unchanged. The refused-fit engineering report is preserved.

March 26 refused fitting with 83 training / 30 calibration labels. March 27 fitted with 120/30; March 28 fitted with 134/30. Models were calibrated before their respective test-session opens. The comparison window is **March 26–28**, using identical capital/risk assumptions. These are historical development walk-forward observations; this exposed development window is not independent confirmation.

Standalone rule-only results in that comparison window:

| Strategy | Net P/L | Closed trades | Win rate | Maximum drawdown | Execution costs |
|---|---:|---:|---:|---:|---:|
| Trend Following | $0.00 | 0 | Undefined | 0% | $0.00 |
| Breakout | $0.00 | 0 | Undefined | 0% | $0.00 |
| Mean Reversion | **−$5.66** | 22 | 18.18% | 0.606% | $3.09 |
| Pullback | **−$1.14** | 3 | 33.33% | 0.114% | $0.56 |
| Momentum | **−$1.58** | 9 | 22.22% | 0.219% | $1.08 |

The **shared-capital rule-only portfolio lost $7.7002**, entirely realized, across **32 closed trades**, with **21.875% win rate**, **0.8094% maximum drawdown**, **$4.5272 execution costs**, 8.9519 turnover and no open positions at the end. Net daily P/L: March 26 −$5.7667; March 27 −$1.0912; March 28 −$0.8423. Both adverse price movement and costs contributed. Reaching 30 trades is a screening count, not evidence of an edge.

The ML variant held cash: **$0 net P/L, zero fills, zero completed trades**. Two selectors fitted, but only seven supported Pullback outcomes were scored; other active strategies lacked sufficient per-strategy training support. The seven predicted success probabilities averaged 9.56%, versus one success out of seven; Brier score 0.12361. Every scored opportunity failed the probability/downside gates. Regression net-return estimates were positive while downside bounds were negative, which illustrates why the calibrated risk gates remain necessary.

**Cash also returned $0. ML abstention is not demonstrated profitable trading or validated ML superiority.** Zero test-period trades for Trend Following/Breakout do not establish that those strategies are safe or profitable. Sharpe and sustainable weekly-return estimates remain unavailable for the short comparison. The $35 average net weekly target has not been established. No model, entry rule or threshold was retuned to make this result positive.

Costs include observable spread, 2bps slippage, 1bps execution uncertainty, a fee allowance and the existing prospective planning schedule. Fills are limited to 1% of displayed liquidity times a 50% modeled fill fraction; displayed liquidity does not guarantee execution. Historical prices use explicit prospective fee assumptions from the [published fee schedule](https://files.alpaca.markets/disclosures/BrokFeeSched.pdf), not claimed historical invoices or actual PAPER fees.

## Evidence, verification and readiness

- Four focused strategy checks pass on Windows and Linux, covering timeframe causality and regime states, all five rule sets, partial-fill recovery/duplicates and risk exits with unavailable ML, same-time capital causality, matured/purged training, PAPER attribution isolation, immutable restore and observed drawdown. Strict Clippy and optimized builds pass on both platforms; Windows formatting passes. Broad accepted migration suites were not repeated.
- Final offline native evaluation: **11.08s wall, 10.69s CPU, 150.78MiB peak Windows working set**. This is a bounded five-session study, not complete production-service load validation.
- Preservation: 40 frozen Phase 7, 38 research, 27 compressed Phase 8 and 106 original Python files unchanged. Against the previous native source freeze, only `lib.rs`, `main.rs` and `market.rs` changed; original market code is byte-identical after removing the additive point-quote method, and all existing CLI handlers including `paper-service` are unchanged. New strategy code is separate.
- Read-only deployed observation confirmed the original image, frozen bundle hash and two existing account identities unchanged. Native release/rollback packet: `.local/rust-paper-packets/20261122T103241018038Z/manifest.json`; native Linux binary SHA256 `daa93d8547d0c537773d4c33df1d28fd2443293aebb678beb51e7e3b2053db02`. Receipt remains unapproved.
- **Not deployment-ready:** no independently validated profitable strategy; current SIP access denied; incomplete continuous/intrabar execution evidence; no high-volatility financial validation; only seven scored outcomes; no prospective confirmation. The local Docker daemon remains unavailable, so full image health/resource/shutdown/rollback gates remain unverified. The new strategy modules are not wired into an active brokerage policy. The existing engine continues unchanged.

Final native result: `.local/multistrategy-final-evaluation/20261122T103339568334Z/report-fbbba7223a1ba905e555c378e8d9281615c2772a88b4c160ace899eb42b83297.json`. It preserves signal decisions, labels, selectors, scores, fills, strategy books and complete negative results. The adjacent checkpoint contains the source ZIP, data/quote provenance and reproducible evidence hashes. Earlier models/reports remain immutable. No Shadow Mode, persistent production collector, live-money execution or unrelated VPS service change was introduced.
