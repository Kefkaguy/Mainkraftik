"""Export read-only experiment evidence, never tune or manufacture forecasts."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

def export():
    from intelligence.db import connection
    from intelligence.phase3_status import phase3_status
    from intelligence.research_models import metrics
    from intelligence.util import canonical

    summary = phase3_status()
    with connection() as conn:
        results = conn.execute(
            "SELECT * FROM candidate_results ORDER BY family,feature_group,symbol,horizon"
        ).fetchall()
        trials = conn.execute("SELECT * FROM tuning_trials ORDER BY trial_key").fetchall()
        plan = conn.execute("SELECT id,created_at,code_hash,specification FROM tuning_plans").fetchone()
        prices = conn.execute(
            "SELECT symbol,count(DISTINCT session_date) sessions FROM daily_bars GROUP BY symbol"
        ).fetchall()
        facts = conn.execute(
            "SELECT symbol,count(*) versions FROM financial_facts GROUP BY symbol"
        ).fetchall()
        info = conn.execute(
            "SELECT provider,count(*) versions,count(DISTINCT external_id) unique_records FROM information_versions GROUP BY provider"
        ).fetchall()
    periods = {}
    for r in results:
        grouped = {}
        for p in r["predictions"]:
            d = p["date"]
            grouped.setdefault(f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}", []).append(p)
        periods[r["id"]] = {
            q: {
                "dates": len(rows),
                "candidate": metrics(
                    [p["truth"] for p in rows],
                    [p["expected_return"] for p in rows],
                    [p["probability_positive"] for p in rows],
                    [p["lower_return"] for p in rows],
                    [p["upper_return"] for p in rows],
                ),
                "baseline": metrics(
                    [p["truth"] for p in rows],
                    [p["baseline_return"] for p in rows],
                    [p["baseline_probability"] for p in rows],
                ),
            }
            for q, rows in grouped.items()
        }
    artifact = {
        "plan": plan,
        "summary": summary,
        "trials": trials,
        "results": results,
        "descriptive_quarter_slices": periods,
        "prices": prices,
        "financial_facts": facts,
        "information_counts": info,
    }
    (ROOT / "docs/phase3-results.json").write_text(canonical(artifact) + "\n")
    lines = [
        "# Phase 3: source expansion, bounded tuning and frozen prospective shadow",
        "",
        "The reference model is preserved. No candidate established a predictive advantage. "
        "A single experimental shadow is enrolled; it has no validated status or trading authority.",
        "",
        "## Actual coverage",
        "",
        "| Provider | Stock | Unique records | Versions | First recorded date | Last recorded date |",
        "|---|---|---:|---:|---|---|",
    ]
    for s in summary["sources"]:
        lines.append(
            f"| {s['provider']} | {s['symbol']} | {s['articles']} | {s['versions']} | {str(s['first_created'])[:10]} | {str(s['last_created'])[:10]} |"
        )
    lines += [
        "",
        "The 1,188 unique news articles include overlapping stock tags (471 MSI, 725 AXON). "
        "All 24 response pages completed. Store creation/update times, article IDs, symbols, source/author, "
        "private headlines, source hashes and acquisition times, not full bodies. Historical features exclude "
        "versions updated after their cutoff; archive completeness/original vintages remain unproven.",
        "",
        "There are 312 as-filed SEC event records: 146 MSI and 166 AXON, including 39 and 43 "
        "8-K item 2.02 earnings-filing proxies respectively. These are not a fabricated earnings calendar or "
        "analyst-surprise dataset. Existing 4,405 financial-fact versions remain intact. XLK and XLI each "
        "have 1,308 SIP split-adjusted sessions, 2021-08-30 through 2026-11-12. They are fixed context "
        "proxies, not historical sector-membership evidence.",
        "",
        "Official IR has nine unique observed announcements (five AXON, four MSI). Exact MSI publication "
        "metadata was appended where available; unknown edit histories retain actual acquisition as availability. "
        "IR historical ablation is blocked. Coverage is a current-page capture, not a complete historical archive.",
        "",
        "## Features and tuning",
        "",
        "Thirty-six versioned fields cover fourteen existing technical variables; ETF/counterpart returns and "
        "volatility; lagged news counts/activity; as-filed revenue, operating-margin and operating-cash-flow "
        "changes; filing/earnings-filing timing; and conservative IR guidance/contract/acquisition flags. "
        "Unavailable values stay null in source snapshots. Models use training medians plus missing indicators. "
        "News windows end at 19:00 New York; observed live inputs must be acquired by 20:00.",
        "",
        "The declared maximum was 96 trials. Eighty completed across twenty studies; sixteen IR trial slots "
        "were blocked rather than searched. Ridge/logistic paired heads and histogram boosting each had four "
        "trials per horizon/eligible feature group. An inner 60/20/20 split entirely inside outer training "
        "selected parameters. Outer 60/20/20 blocks have five-session purges and contained labels. "
        "Nomination used only the inner objective, before outer-result inspection. Frozen replay preserved "
        "all eighty trials and forty-eight result records (forty fitted, eight blocked).",
        "",
        "All outer evidence is exposed historical development, before the sealed 2025-11-14 boundary. "
        "No development result is independent confirmation. No comparison interval had a positive lower "
        "improvement bound. There are 199 one-day / 195 five-day evaluation dates per stock, about nine "
        "20-session blocks and 39 non-overlapping five-day windows. Exact effective independent sample "
        "size is unknown. Four-comparison 98.75% intervals do not correct the whole model search.",
        "",
        "## Frozen nominee: histogram boosting with market context",
        "",
        "| Stock | Horizon | Reference MAE | Candidate MAE | Reference Brier | Candidate Brier | Candidate log loss | Interval coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    chosen = summary["protocol"]["specification"]
    for r in results:
        if (
            r["family"] == chosen["family"]
            and r["feature_group"] == chosen["feature_group"]
            and r["report"].get("candidate")
        ):
            m, b = r["report"]["candidate"], r["report"]["baseline"]
            lines.append(
                f"| {r['symbol']} | {r['horizon']} | {b['mae'] * 100:.4f}% | {m['mae'] * 100:.4f}% | {b['brier']:.5f} | {m['brier']:.5f} | {m['log_loss']:.5f} | {m['interval_coverage'] * 100:.1f}% |"
            )
    lines += [
        "",
        "The nominee is the best under the declared inner-selection rule, not a demonstrated improvement "
        "over the reference. Its Brier point estimates were worse on all four outer stock/horizon slices. "
        "Small AXON MAE gains were not statistically established.",
        "",
        "## All fitted feature comparisons",
        "",
        "| Family | Group | Stock | Horizon | MAE | Brier | Log loss | Coverage |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        m = r["report"].get("candidate")
        if m:
            lines.append(
                f"| {r['family']} | {r['feature_group']} | {r['symbol']} | {r['horizon']} | {m['mae'] * 100:.4f}% | {m['brier']:.5f} | {m['log_loss']:.5f} | {m['interval_coverage'] * 100:.1f}% |"
            )
    lines += [
        "",
        "News and SEC features did not demonstrate incremental predictive value under this budget. "
        "Some news-augmented boosted outputs were unchanged from price-only outputs. SEC-only additions "
        "often worsened point metrics. These are results for this selected sample, target and search budget; "
        "they do not prove the information is universally useless. IR could not be tested historically. "
        "Raw reports contain calibration bins, bias/RMSE, probability dispersion, fitted parameters, "
        "year breakdowns, all uncertainty intervals, and the separately derived descriptive quarter slices.",
        "",
        "## Prospective protocol and remaining evidence",
        "",
        f"The frozen protocol starts with origin session **{summary['protocol']['start_session']}**. "
        "Issuance is 20:30 America/New_York using only actual pre-20:00 acquisitions. The next open is "
        "an outcome input only. The unchanged reference runs alongside the experimental shadow. "
        "Missing required context skips the shadow; no retrospective insertion is allowed.",
        "",
        "The first 126 paired origin dates must include both stocks and both fully matured horizons. "
        "One final analysis uses 2,000 paired 20-session blocks, resampling stocks together, and four "
        "98.75% improvement intervals. All four must improve and integrity checks must pass. "
        "There is no sequential pass hunting or automatic promotion. Current paired-date count is zero.",
        "",
        "Recommended next experiment: collect the frozen prospective evidence without retuning. In parallel, "
        "archive original news revisions and timestamped official announcement metadata prospectively before "
        "considering guidance/text features. FRED/ALFRED remains a later group requiring vintage/release-time "
        "and series-rights review plus an authorized key. Pooled historical universes and YOLO remain blocked. "
        "The PC and worker must remain running. No VPS deployment or original trading/Minecraft access occurred.",
        "",
        "See [predeclaration](phase3-protocol.md), [source review](phase3-sources.md), and "
        "[complete numerical evidence](phase3-results.json).",
    ]
    (ROOT / "docs/phase3-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        canonical(
            {
                "trials": len(trials),
                "results": len(results),
                "shadow_start": summary["protocol"]["start_session"],
                "promoted": 0,
            }
        )
    )


if __name__ == "__main__":
    export()
