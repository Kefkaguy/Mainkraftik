"""One predeclared prospective comparison per frozen Mainkraft bundle."""

from datetime import timedelta

import numpy as np

from intelligence.calendar import EASTERN, schedule
from intelligence.util import now
from paperpilot.db import connect, jb

MIN_DATES = 126
CONFIDENCE = 0.9875
BLOCK = 20
SAMPLES = 2000
BOOTSTRAP_SEED = 20261117


def interval(series):
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    values = np.asarray(series)
    starts = rng.integers(0, len(values) - BLOCK + 1, (SAMPLES, (len(values) + BLOCK - 1) // BLOCK))
    indices = (starts[:, :, None] + np.arange(BLOCK)).reshape(SAMPLES, -1)[:, : len(values)]
    return np.quantile(values[indices].mean(axis=1), [(1 - CONFIDENCE) / 2, (1 + CONFIDENCE) / 2]).tolist()


def compare_latest(persist=False):
    with connect() as conn:
        frozen = conn.execute(
            """SELECT bundle_id,min(session_date) AS first_date FROM pilot_strategy_sessions
            GROUP BY bundle_id ORDER BY max(session_date) DESC LIMIT 1"""
        ).fetchone()
        if not frozen:
            return {"status": "waiting", "elapsed_dates": 0, "required_dates": MIN_DATES}
        identity, first = frozen["bundle_id"], frozen["first_date"]
        old = conn.execute(
            "SELECT payload FROM pilot_strategy_verdicts WHERE bundle_id=%s", (identity,)
        ).fetchone()
        if old:
            return old["payload"]
        window = list(schedule(first, first + timedelta(days=250)))
        dates = window[:MIN_DATES]
        today = now().astimezone(EASTERN).date()
        # Allow the five-session target and two more sessions for frozen evidence to settle.
        review_date = window[MIN_DATES + 5]
        report = {
            "bundle_id": identity,
            "first_date": first,
            "review_date": review_date,
            "required_dates": MIN_DATES,
            "elapsed_dates": sum(day < today for day in dates),
            "status": "collecting",
            "automatic_promotion": False,
        }
        if today <= review_date:
            return report
        rows = conn.execute(
            """SELECT s.session_date,s.symbol,s.strategy,s.role,s.payload AS prediction,
             o.payload AS outcome,v.payload AS virtual_outcome,s.id,sh.picks
            FROM pilot_strategy_signals s
            JOIN pilot_strategy_sessions ps ON s.session_date=ps.session_date AND ps.bundle_id=%s
            LEFT JOIN pilot_strategy_outcomes o ON o.signal_id=s.id
            LEFT JOIN pilot_virtual_outcomes v ON v.signal_id=s.id
            LEFT JOIN pilot_shadow_sessions sh ON sh.session_date=s.session_date
            WHERE s.session_date=ANY(%s) ORDER BY s.session_date,s.strategy,s.symbol,s.role""",
            (identity, dates),
        ).fetchall()

    by_key = {}
    virtual = {
        (day, kind, role): 0.0
        for day in dates
        for kind in ("day", "swing")
        for role in ("paper", "challenger")
    }
    missing_virtual, shadow_dates = 0, set()
    for row in rows:
        day, kind, role = row["session_date"], row["strategy"], row["role"]
        by_key.setdefault((day, kind, row["symbol"]), {})[role] = row
        if row["picks"] is not None:
            shadow_dates.add(day)
            if any(p["signal_id"] == row["id"] for p in row["picks"]):
                if row["virtual_outcome"] is None:
                    missing_virtual += 1
                else:
                    virtual[day, kind, role] += 25 * row["virtual_outcome"]["net_return"]
    report["metrics"] = {}
    all_pass = True
    for kind in ("day", "swing"):
        paired = {day: [] for day in dates}
        for (day, strategy, _), sides in by_key.items():
            if strategy != kind or set(sides) != {"paper", "challenger"}:
                continue
            a, b = sides["paper"], sides["challenger"]
            if (
                not a["outcome"]
                or not b["outcome"]
                or a["outcome"]["net_return"] != b["outcome"]["net_return"]
            ):
                continue
            y = a["outcome"]["net_return"]
            x, z = a["prediction"], b["prediction"]
            paired[day].append(
                [
                    abs(y - x["expected_net_return"]) - abs(y - z["expected_net_return"]),
                    ((y > 0) - x["probability_positive"]) ** 2 - ((y > 0) - z["probability_positive"]) ** 2,
                ]
            )
        available = [np.mean(values, axis=0) for values in paired.values() if values]
        net_paper = [virtual[day, kind, "paper"] for day in dates]
        net_shadow = [virtual[day, kind, "challenger"] for day in dates]
        enough = len(available) == MIN_DATES and len(shadow_dates) == MIN_DATES and not missing_virtual
        metrics = {
            "paired_dates": len(available),
            "paired_rows": sum(map(len, paired.values())),
            "day_block_count_approx": len(available) / BLOCK,
            "paper_model_virtual_net_dollars": sum(net_paper),
            "challenger_virtual_net_dollars": sum(net_shadow),
        }
        if enough:
            mae, brier = np.asarray(available).T
            metrics.update(
                mae_improvement_ci=interval(mae),
                brier_improvement_ci=interval(brier),
                net_improvement_ci=interval(np.asarray(net_shadow) - net_paper),
                challenger_net_ci=interval(net_shadow),
            )
            metrics["status"] = (
                "passed"
                if all(
                    metrics[key][0] > 0
                    for key in (
                        "mae_improvement_ci",
                        "brier_improvement_ci",
                        "net_improvement_ci",
                        "challenger_net_ci",
                    )
                )
                else "failed"
            )
        else:
            metrics["status"] = "inconclusive"
        all_pass &= metrics["status"] == "passed"
        report["metrics"][kind] = metrics
    report["status"] = (
        "passed"
        if all_pass
        else "inconclusive"
        if any(m["status"] == "inconclusive" for m in report["metrics"].values())
        else "failed"
    )
    report["comparison_basis"] = "Same dates, simulated quote and bar outcomes, includes zero-entry sessions"
    report["missing_virtual_outcomes"] = missing_virtual
    if persist:
        with connect() as conn:
            conn.execute(
                "INSERT INTO pilot_strategy_verdicts VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (identity, now(), jb(report)),
            )
    return report
