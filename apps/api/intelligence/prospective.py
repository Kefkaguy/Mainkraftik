"""One frozen experimental shadow, paired prospective collection, one final verdict."""

from uuid import uuid4

import numpy as np

from intelligence.calendar import EASTERN, cutoff_for, future_sessions, issuance_window
from intelligence.db import connection
from intelligence.point_features import GROUPS, NAMES, VERSION, build_features, load_context, persist_panel
from intelligence.portable_models import fit, predict
from intelligence.research import paired_block_interval
from intelligence.storage import jsonb, save_manifest
from intelligence.util import digest, now

PROTOCOL_ID = "information-shadow-v3-126"
MODEL_ID = "information-shadow-v3"


def nominate():
    from intelligence.tuning import PLAN, implementation_hash

    instant = now()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194042)")
        existing = conn.execute("SELECT * FROM shadow_protocols WHERE id=%s", (PROTOCOL_ID,)).fetchone()
        if existing:
            return {
                "enrolled": True,
                "reused": True,
                "start_session": existing["start_session"],
                "model_id": existing["model_id"],
            }
        plan = conn.execute("SELECT * FROM tuning_plans WHERE id=%s", (PLAN,)).fetchone()
        if not plan or plan["code_hash"] != implementation_hash():
            raise ValueError("Matching frozen tuning declaration required")
        if (
            conn.execute("SELECT count(*) n FROM candidate_results WHERE plan_id=%s", (PLAN,)).fetchone()["n"]
            != 48
        ):
            raise ValueError(
                "Complete the entire bounded study, including blocked/failed groups, before nomination"
            )
        trials = conn.execute(
            "SELECT * FROM tuning_trials WHERE plan_id=%s AND status='complete'", (PLAN,)
        ).fetchall()
        choices = {}
        for t in trials:
            choices.setdefault((t["family"], t["feature_group"], t["horizon"]), []).append(t)
        best = {
            key: min(values, key=lambda t: (t["objective"], t["number"])) for key, values in choices.items()
        }
        candidates = [
            (np.mean([best[(f, g, h)]["objective"] for h in (1, 5)]), f, g)
            for f, g, _ in best
            if all((f, g, h) in best for h in (1, 5))
        ]
        if not candidates:
            raise ValueError("No eligible completed inner-development candidate")
        _, family, group = min(candidates)
        streams, info, coverage, facts, events = load_context(conn, instant)
        artifacts = []
        feature_ids = []
        for symbol in ("MSI", "AXON"):
            panel = build_features(
                streams,
                symbol,
                info,
                coverage,
                [f for f in facts if f["symbol"] == symbol],
                [e for e in events if e["symbol"] == symbol],
            )
            feature_ids.extend(persist_panel(conn, symbol, panel, "historical"))
            for h in (1, 5):
                rows = [
                    {
                        **r,
                        "y": float(streams[symbol][r["index"] + h]["close"])
                        / float(streams[symbol][r["index"] + 1]["open"])
                        - 1,
                        "end": streams[symbol][r["index"] + h]["session_date"],
                    }
                    for r in panel
                    if r["index"] + h < len(streams[symbol])
                ]
                a = int(len(rows) * 0.8)
                cal_start = rows[a]["date"]
                train = [r for r in rows[: a - 5] if r["end"] < cal_start]
                cal = rows[a:]
                artifact = fit(train, cal, GROUPS[group], family, best[(family, group, h)]["parameters"], h)
                artifacts.append({"symbol": symbol, "horizon": h, "artifact": artifact})
        specification = {
            "family": family,
            "feature_group": group,
            "feature_version": VERSION,
            "feature_names": NAMES,
            "code_hash": implementation_hash(),
            "tuning_plan": PLAN,
            "selection": "Minimum mean inner objective across horizons; outer evaluation unused",
            "symbols": ["MSI", "AXON"],
            "minimum_dates": 126,
            "bootstrap_samples": 2000,
            "block_sessions": 20,
            "confidence": 0.9875,
            "comparisons": ["1d_mae", "1d_brier", "5d_mae", "5d_brier"],
            "formal_analysis": "Once after first 126 issued paired origins fully mature",
            "status": "experimental_inconclusive",
            "promotion": "none",
            "feature_ids_hash": digest(feature_ids),
            "cutoff": "20:00 America/New_York",
            "issue": "20:30 America/New_York",
            "news_lag_minutes": 60,
        }
        start = future_sessions(instant.astimezone(EASTERN).date(), 1)[0]
        artifact_hash = digest(sorted(artifacts, key=lambda a: (a["symbol"], a["horizon"])))
        conn.execute(
            "INSERT INTO model_versions(id,kind,specification) VALUES(%s,'experimental_shadow',%s) ON CONFLICT DO NOTHING",
            (MODEL_ID, jsonb(specification)),
        )
        conn.execute(
            "INSERT INTO shadow_protocols VALUES(%s,%s,%s,%s,%s,%s)",
            (PROTOCOL_ID, instant, start, MODEL_ID, jsonb(specification), artifact_hash),
        )
        for item in artifacts:
            conn.execute(
                "INSERT INTO shadow_artifacts VALUES(%s,%s,%s,%s)",
                (PROTOCOL_ID, item["symbol"], item["horizon"], jsonb(item["artifact"])),
            )
        conn.execute(
            "INSERT INTO shadow_events(protocol_id,event,payload) VALUES(%s,'enrolled',%s)",
            (
                PROTOCOL_ID,
                jsonb({"family": family, "group": group, "start": start, "artifact_hash": artifact_hash}),
            ),
        )
    return {
        "enrolled": True,
        "model_id": MODEL_ID,
        "family": family,
        "feature_group": group,
        "start_session": start,
        "status": "experimental_inconclusive",
    }


def issue_shadow():
    from intelligence.tuning import implementation_hash

    instant = now()
    origin, blocked = issuance_window(instant)
    if blocked:
        return {"status": "skipped", "reason": blocked, "issued": []}
    cutoff = cutoff_for(origin)
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194043)")
        protocol = conn.execute("SELECT * FROM shadow_protocols WHERE id=%s", (PROTOCOL_ID,)).fetchone()
        if not protocol or origin < protocol["start_session"]:
            return {
                "status": "skipped",
                "reason": "Shadow not enrolled or start session not reached",
                "issued": [],
            }
        if protocol["specification"]["code_hash"] != implementation_hash():
            raise ValueError(
                "Shadow feature/model code differs from frozen protocol; reference remains available"
            )
        artifacts = conn.execute(
            "SELECT symbol,horizon,artifact FROM shadow_artifacts WHERE protocol_id=%s ORDER BY symbol,horizon",
            (PROTOCOL_ID,),
        ).fetchall()
        if digest(artifacts) != protocol["artifact_hash"]:
            # Enrollment order and query order are intentionally canonicalized by symbol/horizon.
            raise ValueError("Frozen artifact identity mismatch")
        streams, info, coverage, facts, events = load_context(conn, cutoff, True)
        targets = future_sessions(origin)
        issued, skipped = [], []
        run_id = uuid4()
        conn.execute(
            "INSERT INTO forecast_runs VALUES(%s,%s,%s,NULL,'running',%s)",
            (run_id, origin, instant, jsonb({"kind": "prospective_shadow", "protocol": PROTOCOL_ID})),
        )
        for symbol in ("MSI", "AXON"):
            if not streams[symbol] or streams[symbol][-1]["session_date"] != origin:
                skipped.append({"symbol": symbol, "reason": "Origin prices not acquired before cutoff"})
                continue
            baseline = conn.execute(
                "SELECT 1 FROM forecasts WHERE symbol=%s AND origin_session=%s AND model_id='historical-baseline-v1'",
                (symbol, origin),
            ).fetchone()
            if not baseline:
                skipped.append({"symbol": symbol, "reason": "Paired reference not issued"})
                continue
            panel = build_features(
                streams,
                symbol,
                info,
                coverage,
                [f for f in facts if f["symbol"] == symbol],
                [e for e in events if e["symbol"] == symbol],
                True,
                latest_only=True,
            )
            if not panel or panel[-1]["date"] != origin:
                skipped.append({"symbol": symbol, "reason": "Incomplete cutoff-safe feature origin"})
                continue
            row = panel[-1]
            group = protocol["specification"]["feature_group"]
            if (
                (group in {"news", "all"} and not row["lineage"]["news_coverage"])
                or (group in {"market", "all"} and any(x is None for x in row["x"][14:22]))
                or (group in {"sec", "all"} and row["x"][26] is None)
            ):
                skipped.append(
                    {"symbol": symbol, "reason": "Required information group unavailable at cutoff"}
                )
                continue
            feature_id = persist_panel(conn, symbol, [row], "prospective")[0]
            dataset_id = save_manifest(
                conn,
                "prospective_shadow",
                streams[symbol],
                feature_id=feature_id,
                protocol=PROTOCOL_ID,
                cutoff=cutoff,
            )
            for a in artifacts:
                if a["symbol"] != symbol:
                    continue
                mean, p, lo, hi = predict(a["artifact"], [row])
                h = a["horizon"]
                identity = uuid4()
                inserted = conn.execute(
                    """INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,
                    entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,
                    expected_return,probability_positive,lower_return,upper_return,sample_count,provenance)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'split',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(symbol,origin_session,horizon,model_id) DO NOTHING RETURNING id""",
                    (
                        identity,
                        run_id,
                        symbol,
                        origin,
                        h,
                        instant,
                        cutoff,
                        targets[0],
                        targets[h - 1],
                        streams[symbol][0]["feed"],
                        streams[symbol][0]["adjustment_asof"],
                        dataset_id,
                        MODEL_ID,
                        VERSION,
                        float(mean[0]),
                        float(p[0]),
                        float(lo[0]),
                        float(hi[0]),
                        a["artifact"]["training_dates"],
                        jsonb(
                            {
                                "kind": "prospective_shadow",
                                "status": "experimental_inconclusive",
                                "protocol": PROTOCOL_ID,
                                "artifact_hash": protocol["artifact_hash"],
                                "feature_id": feature_id,
                                "interval_method": a["artifact"]["interval_method"],
                            }
                        ),
                    ),
                ).fetchone()
                if inserted:
                    issued.append(str(inserted["id"]))
        status = "completed" if issued else "skipped"
        conn.execute(
            "UPDATE forecast_runs SET status=%s,completed_at=now(),detail=%s WHERE id=%s",
            (
                status,
                jsonb(
                    {
                        "kind": "prospective_shadow",
                        "protocol": PROTOCOL_ID,
                        "issued_count": len(issued),
                        "skipped": skipped,
                    }
                ),
                run_id,
            ),
        )
        conn.execute(
            "INSERT INTO shadow_events(protocol_id,event,payload) VALUES(%s,'issuance',%s)",
            (PROTOCOL_ID, jsonb({"origin": origin, "issued": issued, "skipped": skipped})),
        )
    return {"status": status, "issued": issued, "skipped": skipped}


def progress_and_verdict(finalize=False):
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194044)")
        protocol = conn.execute("SELECT * FROM shadow_protocols WHERE id=%s", (PROTOCOL_ID,)).fetchone()
        if not protocol:
            return {"status": "not_enrolled", "paired_dates": 0}
        existing = conn.execute(
            "SELECT * FROM shadow_verdicts WHERE protocol_id=%s", (PROTOCOL_ID,)
        ).fetchone()
        if existing:
            return {"status": existing["status"], "final_verdict": existing["report"]}
        rows = conn.execute(
            """SELECT f.origin_session,f.symbol,f.horizon,f.expected_return,f.probability_positive,
            b.expected_return AS baseline_return,b.probability_positive AS baseline_probability,
            o.realized_return,bo.realized_return AS baseline_truth
            FROM forecasts f JOIN forecasts b ON b.symbol=f.symbol AND b.origin_session=f.origin_session
             AND b.horizon=f.horizon AND b.model_id='historical-baseline-v1'
            LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true
            LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=b.id ORDER BY resolved_at DESC LIMIT 1) bo ON true
            WHERE f.model_id=%s AND f.origin_session>=%s ORDER BY f.origin_session,f.symbol,f.horizon""",
            (MODEL_ID, protocol["start_session"]),
        ).fetchall()
        dates = sorted(
            {
                r["origin_session"]
                for r in rows
                if sum(q["origin_session"] == r["origin_session"] for q in rows) == 4
            }
        )
        selected = dates[:126]
        paired = [r for r in rows if r["origin_session"] in selected]
        matured = len(selected) == 126 and all(
            r["realized_return"] is not None and r["baseline_truth"] is not None for r in paired
        )
        if not matured:
            return {
                "status": "inconclusive",
                "paired_dates": len(dates),
                "required_dates": 126,
                "start_session": protocol["start_session"],
                "resolved_records": sum(r["realized_return"] is not None for r in paired),
                "protocol": protocol["specification"],
            }
        if not finalize:
            return {"status": "ready_for_one_final_analysis", "paired_dates": 126, "required_dates": 126}
        comparisons = {}
        integrity = all(
            np.isclose(r["realized_return"], r["baseline_truth"], rtol=1e-10, atol=1e-10) for r in paired
        )
        for h in (1, 5):
            block = [r for r in paired if r["horizon"] == h]
            for metric in ("mae", "brier"):
                deltas = [
                    abs(r["realized_return"] - r["baseline_return"])
                    - abs(r["realized_return"] - r["expected_return"])
                    if metric == "mae"
                    else (r["baseline_probability"] - (r["realized_return"] > 0)) ** 2
                    - (r["probability_positive"] - (r["realized_return"] > 0)) ** 2
                    for r in block
                ]
                interval = paired_block_interval([r["origin_session"] for r in block], deltas)
                comparisons[f"{h}d_{metric}"] = interval
        status = (
            "passed"
            if integrity and all(v["lower"] > 0 for v in comparisons.values())
            else "failed"
            if integrity
            else "inconclusive"
        )
        report = {
            "status": status,
            "first_origin": selected[0],
            "last_origin": selected[-1],
            "dates": 126,
            "comparisons": comparisons,
            "integrity": integrity,
            "promotion": "none; final selected-stock research verdict only",
        }
        conn.execute(
            "INSERT INTO shadow_verdicts(protocol_id,status,report) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (PROTOCOL_ID, status, jsonb(report)),
        )
        return {"status": status, "final_verdict": report}
