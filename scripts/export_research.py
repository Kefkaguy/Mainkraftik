"""Export frozen public-data research reports. Never train or alter evidence."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from intelligence.db import connection  # noqa: E402
from intelligence.util import canonical  # noqa: E402


def export():
    with connection() as conn:
        plan = conn.execute("SELECT * FROM research_plans WHERE id='bounded-development-v2'").fetchone()
        if not plan:
            raise ValueError("Run the declared research suite first")
        experiments = conn.execute(
            "SELECT * FROM research_experiments WHERE plan_id=%s ORDER BY symbol,horizon,candidate_id",
            (plan["id"],),
        ).fetchall()
        audit = conn.execute("SELECT report FROM research_audits ORDER BY created_at DESC LIMIT 1").fetchone()
        count = conn.execute("SELECT count(*) n FROM research_predictions").fetchone()["n"]
    (ROOT / "docs/research-phase-2-results.json").write_text(
        canonical({"plan": plan, "experiments": experiments, "prediction_count": count}) + "\n"
    )
    lines = [
        "# Phase 2: actual forecasting results",
        "",
        "Nine configurations, two selected stocks and two horizons; 36 frozen experiments. "
        f"{count:,} development predictions are retained. No candidate was promoted.",
        "",
        "Development test origins start 2025-01-30. One-day targets end by 2025-11-13; "
        "five-day origins stop earlier so their targets also end before the sealed 2025-11-14 period. "
        "There are 199 one-day / 195 five-day test dates, approximately nine 20-session blocks "
        "and 39 non-overlapping five-day windows. These counts do not prove independence.",
        "",
        "All new validation statuses are inconclusive because this is previously available development "
        "history. None of the 98.75% diagnostic improvement intervals had a positive lower bound. "
        "The four-comparison procedure does not correct selection across nine candidates. "
        "Fresh prospective confirmation is required; there is no supported forecasting breakthrough.",
        "",
        "## Original model diagnosis",
        "",
        "| Stock | Horizon | MAE | Reference MAE | Brier | Reference Brier | Interval coverage |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for h in audit["report"]["reconstructed_latest_heads"]:
        lines.append(
            f"| {h['symbol']} | {h['horizon']} | {h['test']['mae'] * 100:.4f}% | {h['baseline']['mae'] * 100:.4f}% | {h['test']['brier']:.5f} | {h['baseline']['brier']:.5f} | {h['interval_coverage'] * 100:.1f}% |"
        )
    lines += [
        "",
        "MSI’s point estimates were worse on all four declared comparisons. AXON had small "
        "point improvements but all corrected intervals crossed zero. This fails the improvement "
        "gate without proving that no signal could exist. Original logistic output was uncalibrated; "
        "its probabilities did vary over time. Observed interval coverage of 67–71% undercovered "
        "the nominal middle 80%.",
        "",
        "Train-to-test degradation is consistent with weak generalization and distribution changes, "
        "but alone cannot distinguish overfitting from regime shift. AXON’s one-day in-sample Brier "
        "was 0.24566 versus 0.24258 in evaluation, while MAE rose from 1.547% to 2.405%; it is "
        "incorrect to attribute every failure to overfitting. MSI one-day Brier rose from 0.24604 "
        "in sample to 0.26528 in evaluation. These are descriptive diagnostics, not causal findings.",
        "",
        "The audit reconstructed forecasts from stored fitted parameters and reconciled the original "
        "MAE. Tests reconcile the original heads against scikit-learn, validate train-only scaling, "
        "purged label containment, coherent feed/snapshot selection and causal features. "
        "No future open enters a feature. The reference remains mean return / positive-return "
        "frequency; the new report also records the training-median MAE diagnostic.",
        "",
        "## New bounded experiments",
        "",
        "| Stock | Horizon | Configuration | MAE | Brier | Log loss | Direction accuracy | Interval coverage |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for e in experiments:
        r = e["report"].get("candidate")
        if r:
            lines.append(
                f"| {e['symbol']} | {e['horizon']} | {e['candidate_id']} | {r['mae'] * 100:.4f}% | {r['brier']:.5f} | {r['log_loss']:.5f} | {r['directional_accuracy'] * 100:.1f}% | {r['interval_coverage'] * 100:.1f}% |"
            )
    lines += [
        "",
        "Adding volatility and market features did not establish incremental directional or "
        "return information. Calibrated price-only linear heads sometimes reduced development "
        "Brier versus the original heads, but calibrated candidates did not establish improvement "
        "over the reference. Fixed volatility-scaled intervals improved some coverages and still "
        "undercovered other slices. The raw JSON includes year/regime slices, probability "
        "dispersion, bias, RMSE, calibration bins, interval score and complete comparison intervals.",
        "",
        "Decision: retain the scheduled mean/base-rate reference, preserve all negative studies, "
        "and do not repeat this search on the exposed evaluation. The next technical milestone "
        "is a frozen prospective shadow protocol for one nominated candidate and at least 126 "
        "new eligible origin dates, with matured labels and one declared four-comparison verdict. "
        "That protocol is not enrolled in this delivery; only the reference baseline is scheduled. "
        "Separately audit timestamped earnings/filing events if seeking new information rather "
        "than additional transformations of the same prices.",
        "",
        "See [research declaration](research-phase-2.md), [source feasibility](data-source-feasibility.md), "
        "[raw results](research-phase-2-results.json), and [original audit](original-model-audit.json).",
    ]
    (ROOT / "docs/research-phase-2-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"experiments": len(experiments), "predictions": count, "promoted": 0}))


if __name__ == "__main__":
    export()
