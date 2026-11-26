"""Local opt-in horizon watcher and quote audit. No service installation."""

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.horizon_forward import tick
from chart_intelligence.phase8_evidence import read_compressed, verify


def enroll():
    root = ROOT / ".local/phase8"
    path = root / "forward-enrollment.json"
    if path.exists():
        return json.loads(path.read_text())
    report = read_compressed(
        json.loads((root / "complete.json").read_text())["report_id"], root
    )
    models = {
        h: report["models"][h + ":" + (report["nominations"][h] or "historical_mean")]
        for h in report["nominations"]
    }
    value = {
        "not_before": datetime.now(UTC).isoformat(),
        "model_root": str(root),
        "models": models,
        "source_report_id": json.loads((root / "complete.json").read_text())[
            "report_id"
        ],
        "roles": {
            h: "SCREENED_SHADOW_ONLY"
            if report["nominations"][h]
            else "BASELINE_DIAGNOSTIC_NO_STRATEGY"
            for h in models
        },
        "execution_authority": False,
        "promotion_authority": False,
    }
    from chart_intelligence.core import identity

    value["id"] = identity(value)
    with path.open("x") as f:
        json.dump(value, f, indent=2)
    store.put("forward_enrollment", value, root)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument(
        "--symbols", nargs="+", default=["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]
    )
    args = parser.parse_args()
    bundle = enroll()
    root = ROOT / ".local/phase8/forward"
    while True:
        if (
            shutil.disk_usage(ROOT).free < 10 * 1024**3
            or sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
            > 2 * 1024**3
        ):
            raise RuntimeError("Research storage reserve reached; evidence retained")
        try:
            result = tick(bundle, args.symbols)
        except (ValueError, KeyError, OSError, httpx.HTTPError, sqlite3.Error) as exc:
            result = {
                "bundle_id": bundle["id"],
                "observed_at": datetime.now(UTC).isoformat(),
                "status": "DATA_OR_CONTROLLER_UNAVAILABLE",
                "reason": type(exc).__name__,
                "issued": 0,
                "execution_authority": False,
                "promotion_authority": False,
            }
        folder = ROOT / "reports/phase8"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (
            "forward-run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        with path.open("x") as f:
            json.dump(result, f, indent=2)
        verify(json.loads((folder / "phase7-freeze.json").read_text()))
        print(json.dumps(result), flush=True)
        if not args.watch:
            if result.get("status") == "DATA_OR_CONTROLLER_UNAVAILABLE":
                raise SystemExit(1)
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
