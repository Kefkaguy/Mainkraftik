# Phase 8 research and forward collection

The completed research report is [Phase 8 results](../reports/phase8/README.md).
No tested horizon validates a profitable trading advantage.

## Reproduction

From this Intelligence project, using the existing virtual environment:

```powershell
.venv\Scripts\python.exe scripts/freeze-phase8-boundary.py
.venv\Scripts\python.exe scripts/run-phase8-research.py
.venv\Scripts\python.exe scripts/report-phase8.py
.venv\Scripts\python.exe scripts/audit-phase8-evidence.py
```

The first command freezes once and verifies thereafter. Phase 7 artifacts and
the existing prospective enrollment stay untouched. The research command returns
the completed experiment rather than rerunning against exposed selection data.
Immutable compressed inputs, forecasts and full trade/mark ledgers live in
`.local/phase8/compressed`; models and declarations live in `.local/phase8/records`.
The declaration archives source and dependencies before model fitting.

## Local forward watcher

```powershell
.venv\Scripts\python.exe scripts/watch-phase8.py
.venv\Scripts\python.exe scripts/watch-phase8.py --watch
.venv\Scripts\python.exe scripts/phase8-quote-audit.py
```

The default is one collection pass. `--watch` explicitly opts into a local
foreground poller. No service was installed, deployed or left running by this
task. The poller stops if the local free-space reserve falls below 10 GiB or its
own evidence directory exceeds 2 GiB. It never deletes evidence. Retention and
continuous host availability therefore remain operational requirements for a
long confirmation study.

The separate frozen enrollment in `.local/phase8/forward-enrollment.json` points
to a baseline diagnostic for each horizon because no strategy qualified for
nomination. This is research forecasting, not strategy activation or promotion.
It never replaces the original Phase 7 enrollment. One-hour decisions issue at
12:00/14:00 New York; next-session and swing decisions issue at 20:30. A two-minute
issuance deadline is checked against actual completion time. Late polls never
backdate a forecast. Exchange calendars include holidays and early closes.

Features require completed, available OHLCV and known market context. Actual
source-acquisition timestamps are archived separately from reconstructed market
availability. Current quoted spreads are stored with source and acquisition
times. Outcomes resolve only after exact target bars become available. Thirty-five
calendar days of intraday history cover multi-session targets over holidays;
daily OHLC cannot substitute for the frozen 09:35 / close-minus-15-minute endpoints.
Missing paths stay unresolved and record coverage failures, with safe retry after
new data arrives. Existing prediction rows cannot be updated or deleted.

Quote-priced opportunities use a post-prediction quote, fresh bid/ask checks,
fixed confidence and cost gates and a timely exit quote. They are individual
hypothetical $25 opportunities, not a portfolio allocation or actual Alpaca fill.
Observed IEX spread is not measured slippage, depth, queue priority or NBBO.
Stale and malformed quotes remain visible but do not calibrate executable costs.

## Tests and boundaries

Automated tests cover causal feature windows, exposed-period rejection, label
purging, 25 bounded candidate identities, JSON inference, shared cash/positions,
cost drag, unavailable quotes, restarts, late/stale collection, hourly and
multi-session outcome resolution, and unchanged Phase 7 evidence. Full-suite
verification uses only the independent local PostgreSQL cluster on port 5543.
It does not connect to the production paper database for test mutations.

The research source imports no broker execution client. There is no real-money
route, model-promotion route, paper-policy update, credential change, or operation
on Minecraft or older KefTrade. The single read-only paper-ledger observation in
the report is kept separate from simulations and prospective research.
