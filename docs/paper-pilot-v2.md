# News-screened paper pilot v2

Declared 2026-11-14 before any v2 orders. This replaces the v1 fixed six-stock
execution selection, while preserving all prior evidence and the frozen MSI/AXON
research protocol. It does not establish a validated forecasting edge.

## Daily schedule (America/New_York)

- 07:30–09:00: acquire recent Alpaca news across the active US equity catalog,
  filter liquid fractional stocks, ask Groq for at most 20 cited research picks,
  retrieve each stock's mature daily history, and freeze the eligible selection.
  This starts 04:30 Pacific and stops by 06:00 Pacific.
- 09:15–09:25: issue genuine timestamped per-stock 1-/5-session numerical forecasts.
  Information must have been acquired before 09:15 (06:15 Pacific).
- 09:35–10:00: submit qualifying entries only during the verified regular session.
- Exit on 2% cost-basis loss, 3% gain, stock exposure breach, $12 daily loss,
  a current estimated positive-return probability at or below 30%, or 15 minutes
  before the actual exchange close. Emergency disable stops submissions and cancels
  owned pending orders; existing holdings require review.

## Selection and model separation

The universe is independent of the research watchlist. Query at most 1,000 news
articles over 72 hours, preserving created/update/acquisition timestamps and source
IDs. Deduplicate article IDs, retain the actually acquired version, exclude future
revisions, and explicitly record pagination truncation. Screen at most 60 names by
news activity; require 20 completed daily bars, price >= $5 and average daily dollar
volume >= $20m. Exclude named funds/ETFs/leveraged products. Groq receives at most
40 candidates in a 12,000-character bounded prompt and selects at most 20, citing
provided news only. Unsupported symbols/citations and invented confidence fields
are rejected. Fewer than 20, including zero, is a valid outcome.

Use Groq openai/gpt-oss-120b with strict structured output, low reasoning effort,
2,500 maximum completion tokens, no order tools, and no broker/account credentials
in its prompt. Limit actual attempts to three per New York day and 60 per month;
failed attempts consume the budget. No automatic paid fallback or model downloads.

Groq stance is a news screen, not a return probability. Existing portable ridge
and logistic heads use fourteen numerical technical/SPY features: alpha=100,
C=0.1, at least 504 mature stock-specific outcomes, final 20% for calibration,
five-session purge. No new tuning or fresh-validation claim. Financial information
is displayed when supported and is not silently added to the frozen feature vector.
Both 1-/5-session forecasts are logged; entries use the one-session estimate.

BUY requires estimated positive-return probability >=70%, expected return >0.15%,
bullish cited news, annualized volatility <=80%, a fresh IEX quote (<=15 seconds),
spread <=0.5%, tradable fractional stock status, and all original account/cash gates.
70% is a model estimate, not a demonstrated 70% win rate. Never force 20 trades.

## Execution limits and evidence

$25 per entry, at most 20 concurrent stock positions, $100 per-stock market-value
cap, and $600 total exposure including reservations. Actual available cash must
cover orders. Long-only paper endpoint, no borrowing, derivatives or live execution.
One entry and ordinary exit per stock/session. Durable deterministic order IDs and
restart reconciliation remain unchanged. Recheck account identity, available long
inventory/cash and open reservations immediately before broker mutations. Historical
owned symbols remain eligible for risk-reducing exits after selection changes.

Selections, research runs, API call reservations, source versions, decisions and
outcomes are retained. Scheduled selection is append-only; previews are explicitly
nonexecutable and cannot become a historical/live selection. Missed cutoffs skip.
Model records identify v2 separately; no promotion on a short paper trial.

Original KefTrade services and Minecraft are outside this change. Deployment uses
local image builds, checked off-host restores and projected 10 GiB disk reserve.
