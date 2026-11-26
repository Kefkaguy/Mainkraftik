# Mainkraft walk-forward results

**Evaluation period:** May 4 through November 10, 2026, across 133 exchange
sessions and a fixed 36-stock universe. Training and calibration use only
outcomes that matured before each month's first forecast. The latest fold
uses prior history only. Five-minute SIP bars resolve price paths and
historical IEX quotes check the 9:35 entry gate. The quote check has 123
observations across 127 requested date/symbol pairs.

Each arm has the same underlying forecasts, one $800 exclusive position,
and a $12 daily loss trigger. Overnight risk uses the previous regular
session close as the observed reference. A gap fills the stop proxy at
the adverse open. Drawdown uses marked end-of-session equity. Profit
factor is the sum of closed net gains divided by closed net losses.

| Policy, with IEX checks | Trades | Net P/L | Max drawdown | Profit factor | $20+ sessions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Technical only | 16 | -$62.15 | $92.10 | 0.59 | 3 |
| News filtered | 10 | +$5.09 | $56.37 | 1.06 | 4 |
| News ranked | 17 | -$46.14 | $76.09 | 0.71 | 5 |
| Technical with extra entry and exit guards | 1 | +$22.09 | $0 | Unavailable, zero losing trades | 1 |

The news-filtered result falls to -$13.30 if modeled costs are at least
50 basis points per round trip using the same simulated entries. Its
paired 98.75% daily P/L improvement interval versus technical only is
-$0.39 to +$1.37. The news-ranked interval is -$0.13 to +$0.55.
The extra guards generated a single winning trade. The results leave
the model's profitability unverified.

Without historical IEX gating, the three primary arms ended at
-$141.90, -$137.21, and -$16.19, respectively, under the common 25 basis
point cost assumption. The sign of the news-filtered outcome is sensitive
to execution assumptions.

| Model target | Model error | Historical baseline error |
| --- | ---: | ---: |
| Profit target before a stop | 0.2138 Brier | 0.2105 Brier |
| Price moves at least 2% against entry over five sessions | 0.2373 Brier | 0.2494 Brier |
| One-session price move | 1.122% MAE | 1.120% MAE |
| Three-session price move | 2.597% MAE | 2.607% MAE |
| Five-session price move | 3.412% MAE | 3.425% MAE |
| Swing return after modeled costs | 1.877% MAE | 1.883% MAE |

The average target-hit estimate of 29.28% was close to the observed
29.85% rate. The probability buckets show weak separation across stocks,
and the overall Brier score is worse than the simple historical rate.
The adverse-move score improved on its historical baseline and remains an
experimental risk signal. Five-session targets overlap across dates.

The historical stock list was deliberately fixed from currently known
liquid listings, so it carries survivorship and selection limitations.
The sentiment model scored 8,511 distinct headlines. News used only
articles whose preserved creation and revision timestamps fit the
historical cutoff. Original Groq judgments and vendor revisions are
unavailable for this period. The historical quote check uses the exact
9:35 snapshot and the SIP bar-open execution proxy. It cannot reproduce
actual Alpaca fills.

Reproduce acquisition and evaluation from the repository root using
`scripts/fetch-mainkraft-walk-forward.py` and
`scripts/evaluate-mainkraft-walk-forward.py`. The report, quotes, model
folds, and full predictions are saved under `.local/mainkraft/walk-forward-v1`.
The comparison and three-stage scan run as research. The tested alternatives
have no order authority.

