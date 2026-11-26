
# Mainkraft paper pilot

Mainkraft is the website brand at the existing /intelligence route. It uses the
existing isolated research and paper databases and dedicated Alpaca paper account.

## Strategy and measurement

| Policy | Maximum entries per session | Term | Estimated net-positive probability floor |
|---|---:|---|---:|
| Day AI | 4 | Current session | 50% |
| Swing AI | 2 | Up to 5 exchange sessions | 55% |
| Challenger AI | Virtual Day and Swing slots | Same terms as matched simulations | Same per-strategy floors |

Both broker policies use $25 per entry, $100 per-stock and $600 combined exposure
limits, a $12 daily loss trigger, and at most 20 concurrent positions. At most two
Swing positions can carry overnight, including reserved entries. Day ranks first.
Each stock can receive one entry attempt per session. Candidate forecasts use a
9:15 a.m. New York data cutoff, with an issuance deadline of 9:25 a.m. The first
minute beginning at 9:35 a.m. is the entry window. Calendar-aware exits begin
15 minutes before actual close on the entry session for Day and the fifth
session for Swing. Positions may exit sooner under the 2% stop, 3% take profit,
or portfolio risk rules. Software stops during regular trading can miss a price
during a gap, halt, or outage. Unresolved order acknowledgements block entries.

Each buy saves its strategy signal before submission. A remaining sell
quantity can be attempted up to three times per session, after recorded terminal
state and inventory reconciliation, with a new stable client order ID. An
unknown sell acknowledgement blocks retry until reconciled. The dedicated
paper host, account, cash and long inventory checks apply immediately before
broker mutations.

The Challenger service receives bounded numeric features and loads the frozen
bundle from a read-only mount. Its private network carries no market or broker
credentials. It emits scores only. Matched virtual selections use the same
eligibility rules and IEX quote observation for both model families. Virtual
fills use the observed midpoint plus a conservative cost allowance and start
checking five-minute SIP price paths at the next bar to avoid replaying a
partially elapsed bar. Virtual Swing slots reserve their full five-session
window for this comparison. Real orders use broker records, and execution
timing and costs can differ from the simulator.

## Initial historical evidence

The declared exploratory cohort is AMD, MSFT, GOOG, AMZN, TSM and META. It has
472,139 newly acquired regular-session five-minute SIP bars across those stocks
from November 2022 through November 13, 2026. One expected TSM bar was missing.
The SPY intraday request hit the pagination ceiling. SPY and XLK daily coverage
was available and used for features alongside stock prices, volatility and
volume. XLK represents broad technology context rather than a stock-specific
sector assignment. Live research saves timestamped news evidence and counts.
Historical article versions were insufficient for training a numeric news feature.

The chronological 60/20/20 split used five-session purges. For each strategy,
10 Optuna trials, eight feature-group ablations, and two final fits stayed within
20 attempts. Both strategies retained 2%/3% after nine training-only exit tests.
Removing the SPY/XLK market group hurt the development score in all four
strategy and model combinations. Dropping volatility improved the development
score in this run. The experiment kept the frozen feature set for evaluation.

| Strategy | Evaluation dates | Historical mean MAE / Brier | Ridge + logistic MAE / Brier | Boosted MAE / Brier |
|---|---:|---:|---:|---:|
| Day | 203 | 1.2600% / 0.24622 | 1.2642% / 0.24608 | 1.2851% / 0.24633 |
| Swing | 199 | 2.5157% / 0.24281 | 2.5249% / 0.24504 | 2.5394% / 0.24172 |

None of the candidate MAE or Brier comparisons showed improvement with the
predeclared 98.75% intervals. The price-only historical entry diagnostic
qualified zero stocks at the chosen gates. Its $0 is the result of zero
simulated entries. This historical cohort was selected from present-day
survivors and uses retrospectively acquired SIP bars. The results are
exploratory. Prospective evaluation compares the first 126 exchange sessions,
including days with zero virtual trades. Two thousand paired 20-session
bootstrap samples estimate uncertainty across dates. Missing paired evidence
leads to an inconclusive result. Model promotion requires a separate decision.

## Report and operation

The authenticated daily report is GET /api/v1/paper/daily-report with an optional
date query parameter in YYYY-MM-DD format. It shows provisional results during
trading and flags outstanding reconciliation after close. The report preserves
decisions, reasons, observed fills, positions and virtual outcomes. Estimated
net P/L subtracts five basis points per filled leg from broker paper P/L as an
additional allowance. It keeps the cost already represented by broker fill
prices intact.

The scripts fetch-mainkraft-history.py, fetch-mainkraft-daily.py and
train-mainkraft.py produce private, hashed artifacts under .local/mainkraft.
The paper and challenger services read the frozen bundle at
/models/mainkraft/bundle.json. The Mainkraft runtime flag defaults to off for
local setups. Production uses the independently verified paper account.

### Deployment verification

Mainkraft was deployed after the November 17, 2026 session. The API and web
images were built locally. The VPS import was verified against the local
archive hash and image layer identities. Approximately 60 GB remained free.
Observed VPS memory was 203 MiB of 512 MiB for the paper service and 137 MiB of
256 MiB for the challenger during the smoke check. Both databases were backed
up and restore-tested off-host before cutover.

The first cutover attempt restarted the previous paper container when a
second Python process exceeded its memory budget. The retry used the running
service's private HTTP interface for account checks and controls. End-to-end
checks verified the authenticated report, anonymous denial, paper-account
readiness, and both challenger scores. The Alpaca paper ledger contained zero
orders at activation.

The prior Compose configuration, environment, and image tags are retained for
rollback. Pause new entries and reconcile all orders and positions before
rolling back to a version that predates Mainkraft strategy ownership.

### Swing expectancy trial

The paper worker can run a 20-session Swing experiment starting on the date in
INTELLIGENCE_PAPER_SWING_TRIAL_START. It considers only candidates whose entire
standard signal rejection is the 55% probability threshold. The forecast must
still exceed a 5 bp modeled net margin, and the quote must clear the existing
spread and slippage gate. News, volatility, asset, account, cash, position and
resource checks remain part of admission. The policy gets one $25 buy attempt
per session within the existing Swing and combined caps. Other Swing signals
rank ahead of trial entries. Stop-loss, take-profit and five-session exits
apply to every filled lot.

Every trial decision and order retains its original forecast and its policy
identifier. The dashboard report labels trial decisions, and the report API
adds separately reconciled trial entries, open lots and realized P/L.
The paired virtual model comparison continues using the original trading
rules. Entries stop automatically at the end of the 20-session window.

The archived six-stock replay of the expected-value rule found 67 entries in
199 evaluation sessions. Net P/L at $25 per entry was -$4.33 at 25 bp modeled
cost and -$8.52 at 50 bp. The 95% date-block interval for average session P/L
was -$0.069 to +$0.031. This used existing historical evidence, a SIP-bar fill
proxy, and omitted historical news criteria. A replay with saved November 16
and 17 shortlists and observed opening IEX quotes found one candidate on
November 17. Its forecast was +0.262% expected net return with 41.4%
probability of positive net return. This documents entry eligibility.

Opening-gap continuation and pullback both lost money in development.
The less negative pullback rule lost $35.19 across 406 later sessions at
25 bp cost. Neither opening-gap rule enters the paper execution path.


### $800 Swing trial sizing and ten-session replay

The paper worker can set INTELLIGENCE_PAPER_SWING_TRIAL_NOTIONAL=800.
An $800 order requires its original frozen Swing trial signal, an exact
durable order intent, a flat paper book, and fresh broker verification
inside the entry window. It occupies the entire trial slot. Other entries
pause until the position closes. The trial position's market value cap is
$850. The $12 daily loss trigger still applies, and fills, gaps and costs
can put the realized loss beyond it. At the 3% take profit, $800 yields
$24 gross and $20 after a 0.5% round-trip cost assumption.

A replay for November 4–17, 2026 used the frozen expectancy signal, observed
IEX opening quotes, and SIP five-minute exit bars. The previous regular
session close served as the overnight daily-loss reference. One open
trial position per account was allowed. No entry was added on a day that
began with a position. The ten-date fixed six-stock test omitted historical
news, closed three positions, and returned +$35.49 net with two sessions
at or above $20. At a minimum 50 bp modeled cost the result was +$29.73.
Only November 16 and 17 had saved morning shortlists. Their replay closed
one NVDA position for -$14.00 net after the daily loss trigger. These are
counterfactual results and offer no evidence of dependable daily profit.

