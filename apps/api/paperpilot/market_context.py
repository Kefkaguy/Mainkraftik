"""Fixed cross-market numerical challenger; pure research, no execution authority.

The six new variables are universal traded-market context, not historical sector
membership. Every value is calculated from completed bars at or before its row's
session. Existing frozen research modules are imported without modification.
"""

import math

from intelligence.calendar import schedule
from intelligence.experiments import enriched_panel
from intelligence.forecasting import continuity
from intelligence.portable_models import fit, predict
from intelligence.research_models import FEATURES as TECHNICAL_FEATURES

CONTEXT_SYMBOLS = ("QQQ", "IWM", "TLT")
FEATURE_VERSION = "technical14-cross-market6-v1"
MODEL_VERSION = "experimental-cross-market-ridge-logistic-v1"
REFERENCE_MODEL_VERSION = "fixed-technical14-comparator-v1"
FEATURES = [
    *TECHNICAL_FEATURES,
    "qqq_minus_spy_return_5",
    "qqq_minus_spy_return_20",
    "iwm_minus_spy_return_5",
    "iwm_minus_spy_return_20",
    "tlt_return_5",
    "tlt_return_20",
]
PARAMETERS = {"alpha": 100, "C": 0.1}
PURGE_SESSIONS = 5


def align_inputs(bars, spy, contexts):
    """Trim leading older bars to a common start, never fill or remove middle gaps.

    Callers may use these returned arrays for both panel() and labeled_rows().
    panel() also handles this internally and preserves indexes into its original
    passed stock array. No trailing bars are silently dropped.
    """
    if set(contexts) != set(CONTEXT_SYMBOLS):
        raise ValueError("Exactly QQQ, IWM and TLT context streams are required")
    streams = {"stock": bars, "SPY": spy, **contexts}
    for name, stream in streams.items():
        valid, reason = continuity(stream)
        if not valid:
            raise ValueError(f"{name}: {reason}")
    policies = {
        (r["feed"], r["adjustment"], r["adjustment_asof"]) for stream in streams.values() for r in stream
    }
    if len(policies) != 1:
        raise ValueError("Stock and context feeds/adjustment snapshots must match")
    start = max(stream[0]["session_date"] for stream in streams.values())
    trimmed = {name: [r for r in stream if r["session_date"] >= start] for name, stream in streams.items()}
    if any(not stream for stream in trimmed.values()):
        raise ValueError("No overlapping context coverage")
    return trimmed["stock"], trimmed["SPY"], {name: trimmed[name] for name in CONTEXT_SYMBOLS}


def panel(bars, spy, contexts):
    """Append six historical-only variables; indexes still reference passed bars."""
    original_size = len(bars)
    bars, spy, contexts = align_inputs(bars, spy, contexts)
    offset = original_size - len(bars)
    streams = {"stock": bars, "SPY": spy, **contexts}
    stock_dates = {r["session_date"] for r in bars}
    for name, stream in streams.items():
        if not stock_dates.issubset({r["session_date"] for r in stream}):
            raise ValueError(f"{name}: Missing aligned stock sessions; no forward fill")
    indexes = {name: {r["session_date"]: i for i, r in enumerate(stream)} for name, stream in streams.items()}
    records = enriched_panel(bars, spy)
    for row in records:
        values = {}
        for name in ("SPY", *CONTEXT_SYMBOLS):
            i = indexes[name][row["date"]]
            if i < 20:
                raise ValueError(f"{name}: Missing completed context lookback")
            stream = streams[name]
            values[name] = {h: float(stream[i]["close"]) / float(stream[i - h]["close"]) - 1 for h in (5, 20)}
        additions = [values[name][h] - values["SPY"][h] for name in ("QQQ", "IWM") for h in (5, 20)] + [
            values["TLT"][h] for h in (5, 20)
        ]
        if not all(math.isfinite(v) for v in additions):
            raise ValueError("Nonfinite cross-market feature")
        row["x"] = [*row["x"], *additions]
        row["index"] += offset
    return records


def labeled_rows(records, bars, horizon):
    """Labels are next-open to horizon-close; future opens never enter panel()."""
    if horizon not in (1, 5):
        raise ValueError("Only 1-/5-session horizons are declared")
    out = []
    for row in records:
        i = row["index"]
        if bars[i]["session_date"] != row["date"]:
            raise ValueError("Feature row/bar session mismatch")
        if i + horizon >= len(bars):
            continue
        y = float(bars[i + horizon]["close"]) / float(bars[i + 1]["open"]) - 1
        if not math.isfinite(y):
            raise ValueError("Nonfinite return label")
        out.append({**row, "end": bars[i + horizon]["session_date"], "y": y})
    return out


def shared_boundaries(dates):
    """Freeze shared 60/20/20 calendar boundaries before inspecting model losses.

    Missing observed feature dates do not compress the five-exchange-session
    purges. Pass the same returned mapping to every stock and both horizons.
    """
    observed = sorted(set(dates))
    if len(observed) < 100:
        raise ValueError("At least 100 shared dates are needed for partition construction")
    days = list(schedule(observed[0], observed[-1]))
    if not set(observed).issubset(days):
        raise ValueError("Partition dates must be exchange sessions")
    a, b = int(len(days) * 0.6), int(len(days) * 0.8)
    return {
        "training_start": days[0],
        "train_end_exclusive": days[a - PURGE_SESSIONS],
        "calibration_start": days[a],
        "calibration_end_exclusive": days[b - PURGE_SESSIONS],
        "evaluation_start": days[b],
        "evaluation_end_inclusive": days[-1],
        "shared_dates": len(observed),
        "exchange_sessions": len(days),
        "purge_sessions": PURGE_SESSIONS,
    }


def split_rows(rows, boundaries):
    """Use common boundaries and discard every boundary-crossing target label."""
    groups = {"train": [], "calibration": [], "evaluation": []}
    for row in rows:
        day, end = row["date"], row["end"]
        if end <= day:
            raise ValueError("Target end must follow its feature origin")
        if (
            boundaries["training_start"] <= day < boundaries["train_end_exclusive"]
            and end < boundaries["calibration_start"]
        ):
            groups["train"].append(row)
        elif (
            boundaries["calibration_start"] <= day < boundaries["calibration_end_exclusive"]
            and end < boundaries["evaluation_start"]
        ):
            groups["calibration"].append(row)
        elif (
            boundaries["evaluation_start"] <= day <= boundaries["evaluation_end_inclusive"]
            and end <= boundaries["evaluation_end_inclusive"]
        ):
            groups["evaluation"].append(row)
    return groups


def fit_candidate(train, calibration, horizon, with_context=True):
    """Fixed per-stock model; deliberately no hyperparameter or threshold search."""
    if horizon not in (1, 5):
        raise ValueError("Only 1-/5-session horizons are declared")
    for name, rows in (("training", train), ("calibration", calibration)):
        dates = [r["date"] for r in rows]
        if not rows or dates != sorted(set(dates)):
            raise ValueError(f"{name} rows must have unique chronological origins")
        if any(len(r["x"]) != len(FEATURES) or not math.isfinite(r["y"]) for r in rows):
            raise ValueError(f"{name} feature/label shape is invalid")
    if max(r["end"] for r in train) >= min(r["date"] for r in calibration):
        raise ValueError("Training outcomes overlap calibration origins")
    columns = list(range(20 if with_context else 14))
    artifact = fit(train, calibration, columns, "linear", dict(PARAMETERS), horizon)
    artifact.update(
        {
            "model_version": MODEL_VERSION if with_context else REFERENCE_MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "feature_names": [FEATURES[i] for i in columns],
            "validated": False,
            "execution_authority": "none",
        }
    )
    return artifact


def predict_candidate(artifact, rows):
    if (
        artifact.get("model_version") not in (MODEL_VERSION, REFERENCE_MODEL_VERSION)
        or artifact.get("feature_version") != FEATURE_VERSION
    ):
        raise ValueError("Unrecognized cross-market model artifact")
    if not rows or any(len(r["x"]) != len(FEATURES) for r in rows):
        raise ValueError("Prediction rows require the complete declared feature vector")
    return predict(artifact, rows)
