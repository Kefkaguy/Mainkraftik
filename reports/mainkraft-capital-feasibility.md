# Mainkraft capital-feasibility review

Report: `c11636e3146051b83be96f3a34e3fd3fed1bbd8bc33ec0d9e1597b9a8df9eac6`  
Protocol: `45d7e290b63f71b85a8fbfb0b7d4fc9e9918e464750459be148355f53157072f`

The $20 average daily paper-return objective is **not supported**. No automatic
promotion, capital increase, prospective nomination, or VPS deployment is authorized
by this research result. Existing paper execution remains on its existing policy.

## Scope and results

36 current-survivor stocks; May 4–November 10, 2026; 133 evaluation dates,
including no-trade and losing dates. Earlier data supports monthly development.
All these historical dates have already been examined and remain exploratory.

32 base rules × 3 news overlays × 3 sizes = 288 portfolio scenarios, evaluated
at two cost/delay assumptions = 576 cost scenarios, plus
monthly development windows and one frozen rule-versus-existing-AI comparison.
Overlay and sizing comparisons count in the registry; the study is not 32 total trials.
Approved ceiling: $5,000 cash, two positions, $800/$1,500/$2,500 entries,
$50 daily loss trigger and $500 drawdown review. Fees consume cash, so two
$2,500 entries cannot both fit in exactly $5,000 without a cash reserve.

| Portfolio | Net P/L | Per date | Drawdown | Entries | Dates | $20 dates |
|---|---:|---:|---:|---:|---:|---:|
| Monthly earlier-date selection | $-223.96 | $-1.68 | $506.03 | 101 | 133 | 34 |
| Frozen rule, 25bp | $-491.28 | $-3.69 | $510.91 | 27 | 133 | 10 |
| Frozen rule + AI, 25bp | $-36.09 | $-0.27 | $194.71 | 15 | 133 | 12 |
| Frozen rule, 50bp + delayed exit | $-468.45 | $-3.52 | $527.76 | 15 | 133 | 6 |
| Frozen rule + AI, 50bp + delayed exit | $-154.47 | $-1.16 | $288.34 | 14 | 133 | 10 |
| Best observed table row (selected retrospectively) | $392.81 | $2.95 | $376.20 | 115 | 133 | 38 |

Paired AI-minus-rule average daily P/L 98.75% moving-block bootstrap interval:
`[-2.4837047402819836, 7.617459784056309]` dollars per date. The interval includes
zero if its lower bound is non-positive; that does not establish improvement.
2,000 replicates, 20-session blocks, all-stock portfolio dates sampled together.
133 dates provide only 6.65 nominal 20-session blocks, not 133 independent observations.

The best table row is an after-the-fact winner among many comparisons and is not
independent confirmation. Profit factor uses closed trades; net P/L also marks
open inventory. The complete machine-readable registry retains costs, turnover,
monthly totals, stock concentration, losing streaks, skipped entries and trade details.

That retrospective row loses $-458.60 under
50bp costs and delayed exits. Its 25bp daily-P/L 98.75% block interval is
`[-6.601336944157762, 9.232992529683246]` dollars and is not adjusted for winner selection.

## Measurement and gates

Five-minute trade-bar approximation, not historical executable quote fills.
Half the round-trip cost floor is charged per side exactly once. Stop/target
ambiguities use stop-first triggers, then execution uses the next observed open;
gaps and delayed exits can exceed the loss trigger. Quotes and bars are not mixed
or presented as the same fill evidence.

Pinned LEAN revision: `949671e21f1258d8cc7f2472f5f5d75111a8cb32`.
Audit status: **component_reconciled_full_engine_pending**. Full deployment gate:
**False**.
Five compiled LEAN quote-fill fixtures reconcile cash, quantities and inventory.
Partial fills are explicit child slices, not proof of asynchronous partial-fill
notifications. The full LEAN lifecycle and shared live/replay integration remain
required before simulator acceptance or a new deployed paper policy.

Monthly selection uses only earlier dates, a fresh development wallet, 60 entry
sessions and five extra sessions to mature labels without new entries. Test-month
portfolios retain inventory and drawdown pauses across month boundaries.

## Coverage and limitations

News uses available version timestamps; later revisions are excluded from earlier
cutoffs. Missing original revisions are unavailable, never reconstructed. News
coverage does not support the earliest development windows, so those windows
exclude news overlays and event-rule selection. Missing news is neutral in ranking.
Deterministic event categories retain cited article text; they are not verified
earnings surprises or analyst expectations. Generic event wording can produce
classification errors. The fixed current-survivor universe has selection bias.

This does not retrain the existing model heads or alter their forecasting acceptance
rules. The AI comparison reuses existing date-safe return/adverse-move predictions;
there is no claim that additional model libraries or greater sizing create an edge.

## Decision and next experiment

Do not nominate a candidate for the $20 objective from these results. Complete
full simulator lifecycle parity first. Then freeze a single low-turnover candidate
and cost specification before collecting new quote-based forward observations.
Any eventual prospective protocol must freeze model, sizing, rules and review date
for 126 new sessions; no automatic tuning or promotion. This report is a failed
capital-feasibility result, not a daily-income forecast.

No VPS or Minecraft access was performed for this implementation.
