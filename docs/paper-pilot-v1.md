# Six-stock paper pilot v1 — declaration before execution

Universe: AMD, MSFT, GOOG (Class C), AMZN, TSM (US ADR), META. MSI/AXON and their
frozen shadow protocol are unchanged. This pilot is not validation of that experiment.

Only `https://paper-api.alpaca.markets` can receive orders. Separate credentials,
a pinned account ID different from the old trader's ID, zero initial positions/open
orders, active account status and actual cash checks are prerequisites. Execution is
disabled until account isolation and deployment acceptance pass. No live endpoint,
options, crypto, shorts, borrowing, averaging down or discretionary LLM authority.

Premarket input cutoff: 09:15 America/New_York. Signals may be created from 09:15
to 09:25 using only actual pre-cutoff acquisitions and completed prior sessions.
Missed windows are skipped, not backfilled. Alpaca's clock/calendar must agree with
the exchange calendar. November 13 is attempted only if every gate was ready before
cutoff; otherwise use a later eligible session. The frozen research's evening forecasts
are never reused as premarket signals.

Fixed per-stock experimental ridge/logistic heads reuse the existing portable model
implementation: alpha=100, C=0.1, fourteen technical/SPY features, no tuning. At least
504 mature historical outcomes are required; the final 20% is disjoint calibration,
with a five-session purge. Both 1-/5-session outputs are logged; entry uses the 1-day
open-to-close estimate. Actual fill-to-exit P/L is a different execution outcome.

BUY requires expected return >0.15%, calibrated probability >=55%, 20-session
annualized volatility <=80%, verified tradable/fractionable asset, fresh regular-session
IEX quote (<=15 seconds), spread <=0.5%, sufficient cash, no existing position, no
unresolved intent, no open order, and available risk budget. No forced buys.
SIP daily-history features and IEX execution quotes are separately labeled; no claim
of full-market live quotes or silent feature-feed switching.

Entry window: 09:35–10:00 New York, regular hours only. Each symbol can buy once
per session. Buy notional starts at $90, bounded by $100 per symbol and $600 total
including reservations and current market value. Price moves can breach a valuation
limit after a fill; reconciliation detects this and requests a risk-reducing exit.
Buying-power checks never authorize borrowing: available cash is also required.

Exit at 2% cost-basis loss, 3% gain, exposure breach, or 15 minutes before the actual
session close (including shortened sessions). Daily-loss circuit breaker: -$12 or -2%
of the $600 strategy budget, whichever is reached first; block entries and exit owned
positions while enabled. Maximum six buys and six ordinary exits per session, one
entry/exit intent per symbol/side/session. Pending orders expire after 90 seconds.
Partial fills are reconciled; cancels do not assume fills or positions disappeared.

An intent and deterministic client ID are committed before POST. A lost acknowledgement
is queried by client ID and never blindly resubmitted. Unknown order state blocks buys.
Only orders with this pilot's prefix may be canceled; never bulk-cancel the account.
The emergency control disables automatic submissions and requests cancellation of
owned pending orders. Existing positions remain open and visible; this is not liquidation.

The dashboard distinguishes broker account balance from the $600 pilot budget,
broker P/L from strategy-ledger P/L, and live IEX marks from stale/missing marks.
Deposits/withdrawals/resets are not profit. Inconsistent cash flows or unexplained
positions halt entries. Strategy realized P/L is computed from reconciled fills;
benchmarks begin only with genuine inception marks and never invented historical trades.
No model promotion follows a few profitable paper trades.
