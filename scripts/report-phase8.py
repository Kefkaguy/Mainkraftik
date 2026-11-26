"""Report frozen horizon results; add descriptive SPY context without fitting."""

import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import parse_bars
from chart_intelligence.horizon_forward import summary
from chart_intelligence.phase8_accounting import split_pnl
from chart_intelligence.phase8_evidence import read_compressed, verify
from intelligence.util import digest


def main():
    out = ROOT / "reports/phase8"
    root = ROOT / ".local/phase8"
    small = json.loads((out / "research-results.json").read_text())
    full = read_compressed(small["report_id"], root)
    history = ROOT / ".local/mainkraft/history"
    markets = {}
    for ref8 in map(json.loads, (history / "coverage.jsonl").read_text().splitlines()):
        with gzip.open(history / (ref8["hash"] + ".json.gz"), "rt") as f:
            raw = json.load(f)
        if digest(raw) != ref8["hash"]:
            raise ValueError("Accounting price snapshot hash mismatch")
        markets[ref8["symbol"]] = parse_bars(
            [r for r in raw["bars"] if r[0][:10] <= "2025-04-02"]
        )
    attribution = {}
    for h, result in full["results"].items():
        forecast = read_compressed(
            full["forecast_records"][h + ":historical_mean"], root
        )
        end = max(datetime.fromisoformat(r["exit_time"]) for r in forecast["rows"])
        marks = {
            s: float(bars[max(t for t in bars if t <= end)][3])
            for s, bars in markets.items()
        }
        attribution[h] = {}
        for method, candidate in result["candidates"].items():
            attribution[h][method] = {}
            for name, scenario in candidate["scenarios"].items():
                values = split_pnl(scenario["orders"], marks)
                if abs(values["net_pnl"] - scenario["net_pnl"]) > 1e-6:
                    raise ValueError("Frozen order ledger did not reconcile")
                attribution[h][method][name] = values
    account = {
        "source_report_id": small["report_id"],
        "attribution": attribution,
        "note": "Separates partial realized sales from remaining unrealized inventory. Frozen net P/L, orders, candidates and selections are unchanged.",
    }
    account["record_id"] = store.put("partial_fill_attribution_addendum", account, root)
    (out / "partial-fill-accounting.json").write_text(json.dumps(account, indent=2))
    ref = next(
        r
        for r in map(json.loads, (history / "daily.jsonl").read_text().splitlines())
        if r["symbol"] == "SPY"
    )
    with gzip.open(history / (ref["hash"] + ".json.gz"), "rt") as f:
        spy = json.load(f)
    if digest(spy) != ref["hash"]:
        raise ValueError("SPY context hash mismatch")
    prices = {r["session_date"]: r for r in spy["bars"]}
    passive = {}
    for h, result in full["results"].items():
        dates = sorted(result["risk_matched_passive"]["daily_net_pnl"])
        path = [prices[d] for d in dates if d in prices]
        entry = float(path[0]["open"]) * 1.0005 * 1.0005
        qty = 25 / entry
        final = float(path[-1]["close"]) * 0.9995 * 0.9995 * qty
        pnl = final - 25 - 0.0001 * (25 + final)
        equity = [600, *(575 + qty * float(r["close"]) - 0.0025 for r in path)]
        peak = 600
        dd = 0
        for value in equity:
            peak = max(peak, value)
            dd = max(dd, peak - value)
        passive[h] = {
            "net_pnl": pnl,
            "net_return": pnl / 600,
            "marked_daily_drawdown": dd,
            "notional": 25,
            "cash_reference": 575,
            "first": dates[0],
            "last": dates[-1],
            "scope": "Descriptive $25 SPY buy/hold plus cash. Daily open/close differs from model endpoints; excludes dividends and measured quotes; not a matched alpha test.",
        }
    addendum = {
        "source_report_id": small["report_id"],
        "spy_context": passive,
        "model_selection_unchanged": True,
    }
    addendum["record_id"] = store.put("passive_context_addendum", addendum, root)
    (out / "passive-context.json").write_text(json.dumps(addendum, indent=2))
    prospective = summary()
    (out / "prospective-status.json").write_text(json.dumps(prospective, indent=2))
    lines = [
        "# Phase 8 — Trading Edge Discovery",
        "",
        "**No profitable trading advantage has been validated.** All five horizons failed the preregistered development nomination gates. There is no untouched historical confirmation set in the available evidence.",
        "",
        "## Boundaries and design",
        "",
        "Phase 7 files, models, forecasts, reports and enrollment are frozen by a SHA-256 inventory. Existing prospective ledger rows are also hash-checked; new observations may append without changing them. No rejected candidate was promoted. Phase 8 fits new targets and produces separate model identities.",
        "",
        "The fixed search contains five horizons × five methods = 25 candidates: historical mean, zero return, the existing Mainkraft ridge/logistic and histogram-boosting implementations, and one small regularized neural sequence model. There is no adaptive hyperparameter search. Inputs include causal OHLCV sequences and SPY/XLK context. Historical news is excluded because acquisition-safe versions do not cover this development period.",
        "",
        "Only data through April 2, 2025 enters development. The April-2025–January-2026 Phase 7 audit and the previously exposed later 2026 evaluation are excluded from feature/label construction and selection. Older development prices were already used in earlier research; changing horizon does not turn them into independent confirmation.",
        "",
        "Training origins end April 11, 2024; calibration begins April 22 and ends October 3; selection begins October 14. Five-session gaps separate origin partitions, and every training/calibration label end precedes the next partition decision. Swing rows whose outcomes extend beyond the development boundary are rejected.",
        "",
        "One-hour forecasts use 12:00/14:00 New York cutoff-safe intraday sequences. Daily/swing forecasts use completed-session candles and market context at 20:30, with next-session 09:35 entry and the final target-session close-minus-15-minute exit. Two-, three- and five-session targets are separately fitted. Availability delay remains fifteen minutes; historical actual delivery and adjustment revisions remain limitations.",
        "",
        "## Complete base-cost screening results",
        "",
        "All values below are hypothetical, on a $600 reference with $25 entries. Qualified signals precede execution/risk rejection. Zero trades are not positive evidence. Drawdown marks the shared portfolio; unresolved positions are included at their last observed price and separately counted.",
        "",
        "| Horizon | Method | Qualified | Closed trades | Net P/L | Marked max drawdown | Direction accuracy | Brier |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for h, result in small["results"].items():
        for name, c in result["candidates"].items():
            b = c["scenarios"]["base"]
            p = c["prediction"]
            lines.append(
                f"| {h} | {name} | {b['qualified_opportunities']} | {b['closed_trades']} | ${b['net_pnl']:.2f} | ${b['marked_max_drawdown']:.2f} | {100 * p['direction_accuracy']:.1f}% | {p['brier']:.4f} |"
            )
    lines += [
        "",
        "## Benchmarks and uncertainty",
        "",
        "| Horizon | Risk-matched always-long P/L / closed trades | $25 SPY + cash context P/L |",
        "|---|---:|---:|",
    ]
    for h, result in small["results"].items():
        p = result["risk_matched_passive"]
        lines.append(
            f"| {h} | ${p['net_pnl']:.2f} / {p['closed_trades']} | ${passive[h]['net_pnl']:.2f} |"
        )
    lines += [
        "",
        "Cash returns $0 with no exposure. The risk-matched always-long benchmark shares entry times, capital, concurrency, stops, exit rules, costs and fill scenarios. The SPY comparison is supplemental daily-price buy/hold context, not a timing-matched or dividend-inclusive alpha test.",
        "",
        "The least-loss active base result was five-session histogram boosting: 63 closed trades, -$0.25 net, about $5.35 marked maximum drawdown. Its family-adjusted average daily P/L interval is approximately [-$0.1243,+$0.1137]. It beats a losing risk-matched always-long benchmark in point P/L but fails to establish positive returns versus cash. It was not nominated. One-day models generated no qualified opportunities; one-hour neural activity produced only 13 closed trades and lost about $0.96.",
        "",
        "Intervals use 5,000 circular twenty-session block resamples and alpha=.05/25 (99.8% point intervals for the declared 25-candidate family). All no-trade days stay in denominators. Selection-period reuse, serial dependence and scenario assumptions still prevent these intervals from becoming independent confirmation. Thirty trades is only a screening floor; the declared fresh confirmation requirement is 126 dates and 100 closed trades, positive cost-adjusted lower bounds versus cash and risk-matched passive, observed quote coverage and stress survival. No horizon passed even the preliminary gates.",
        "",
        "One two-session linear run retains an unresolved position at the historical boundary. Its mark is included in net P/L but is not treated as a closed trade or liquidatable quote. This independently blocks acceptance; no forced end-of-data liquidation was invented. A separate partial-fill-accounting.json addendum corrects the attribution of partial realized sales versus remaining unrealized inventory; original net P/L and selections are unchanged. Future replay code uses this tested attribution. Full conservative OHLC drawdown, order states and daily net P/L remain in the immutable full report.",
        "",
        "## Costs, liquidity and execution coverage",
        "",
        "Base round-trip assumptions total about 22bp (10bp full spread, 5bp slippage each leg, 1bp fee allowance each leg); stress scenarios use 42bp and 82bp. These are not measured broker fees or slippage. All 75 candidate/cost combinations have nonpositive net P/L. Tightened cost gates sometimes suppress all trades; zero exposure is not evidence of profitability.",
        "",
        "Research limits remain $25 entries, at most two positions, $100 total exposure, no averaging down, no borrowing, one attempt per stock/session and a $12 daily loss latch. Historical liquidity checks use only available dollar volume at 0.1% participation. Partial entries cancel the remainder; partial/rejected exits are retried at subsequent observed opens, with three attempts per session. Stops/targets remain 2%/3%; completed-bar observations schedule next-open execution instead of receiving perfect intrabar fills. Overnight gaps and unsettled end positions are marked rather than hidden.",
        "",
        "Strict executable-quote coverage is unavailable for historical candidates. Actual quote observations are audited separately for age, bid/ask validity and feed. IEX is a single venue, not NBBO, depth or a fill guarantee. Missing/stale observations never become executable fills. Quoted spread can be observed; realized slippage, queue position and market impact are still unmeasured.",
        "",
        "## Calibration and market conditions",
        "",
        "All five neural fits converged within their fixed budgets, so failure is not explained solely by optimizer warnings. Neural direction accuracy ranges from about 47.0% to 51.7%; Brier ranges from .2502 to .2547. Convergence and classification accuracy did not earn the complex model a trading nomination. Interval coverage, class counts, calibration bins, return and downside errors for every method are retained in research-results.json.",
        "",
        "Regime P/L uses causal stock volatility and SPY direction buckets, never future regime labels. Counts and P/L are retained for all four buckets and every candidate/cost scenario. Those reused development buckets do not establish stability through unseen market conditions. A model with no trades supplies no regime evidence.",
        "",
        "## Prospective collection and paper separation",
        "",
        "The original Phase 7 enrollment remains unchanged. A separate append-only Phase 8 enrollment records baseline diagnostic forecasts because no strategy was nominated; it does not promote or deploy a candidate. Tests verify timestamps before targets, issuance deadlines, stale input rejection, restarts, missing-bar coverage, and exact hourly/one-to-five-session endpoint resolution. Long-horizon resolutions use intraday endpoints, never substitute daily close for 09:35/pre-close labels.",
        "",
        "A real six-stock collection was performed successfully, but outside the preregistered issuance windows. No past forecast was backdated. Prospective counts and actual fresh/stale quote counts are in prospective-status.json and quote-coverage.json. Future independent confirmation remains pending. New-horizon individual hypothetical quote outcomes require timely post-prediction quotes and are distinct from any allocated portfolio or broker fill.",
        "",
        "No persistent service was installed or silently started. The explicit local watcher is scripts/watch-phase8.py; --watch opts into polling. It retains evidence and stops at storage/free-space limits instead of deleting research. The current one-shot check is recorded in forward-run-*.json.",
        "",
        "A read-only VPS application-ledger observation on November 18 found the existing swing trial holding roughly $800 of UBS, with $0 realized P/L and approximately -$6.78 unrealized P/L. One entry intent and six order versions were present. That observation is separate from Phase 8 simulations and was not a direct broker-history audit. No entry thresholds, policy, capital, credentials, orders, Minecraft or unrelated VPS service were modified.",
        "",
        "## Deployment blockers and reproducibility",
        "",
        "No strategy is nominated. Blockers include negative or zero net screening outcomes, lack of untouched independent confirmation, lack of historical executable quotes and measured slippage, revised/survivor data, synthetic fill assumptions and the unresolved historical position in one run. No automatic promotion or new deployment is authorized.",
        "",
        "- preregistration.json: fixed candidate count, splits, risk, costs and acceptance gates.",
        "- research-results.json: all 25 candidates and all cost scenarios, including negative/zero outcomes.",
        "- .local/phase8/compressed: immutable full data, forecasts, rejection ledger, orders/trades, daily marks and results.",
        "- phase7-freeze.json: frozen Phase 7 hashes and pre-existing prospective row identities.",
        "- passive-context.json, prospective-status.json, quote-coverage.json and paper-observation.json: separately labeled addenda.",
        "- full-tests.xml and acceptance.json: automated checks and final source/evidence hashes.",
        "",
        "**Final research determination: no profitable trading advantage has been validated at any tested horizon.**",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    verify(json.loads((out / "phase7-freeze.json").read_text()))
    print(
        json.dumps(
            {
                "prospective": prospective,
                "passive_context_record": addendum["record_id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
