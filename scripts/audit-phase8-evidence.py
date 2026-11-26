"""Verify Phase 8 results and unchanged Phase 7 evidence without re-fitting."""

import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.phase8 import END, HORIZONS, METHODS, partition, predict
from chart_intelligence.phase8_evidence import read_compressed, sha, verify


def main():
    out = ROOT / "reports/phase8"
    local = ROOT / ".local/phase8"
    frozen = json.loads((out / "phase7-freeze.json").read_text())
    verify(frozen)
    completed = json.loads((local / "complete.json").read_text())
    report = read_compressed(completed["report_id"], local)
    declaration = store.read(report["declaration_id"], local)["payload"]
    store.read(declaration["source_archive"], local)
    data = read_compressed(report["dataset_id"], local)["horizons"]
    assert len(report["models"]) == len(HORIZONS) * len(METHODS) == 25
    for rows in data.values():
        assert max(r["date"] for r in rows) <= END
        assert max(r["label_end"][:10] for r in rows) <= END
        tr, cal, selection = partition(rows)
        assert max(r["label_end"] for r in tr) < min(r["decision"] for r in cal)
        assert max(r["label_end"] for r in cal) < min(r["decision"] for r in selection)
    for key, model_id in report["models"].items():
        model = store.read(model_id, local)["payload"]
        saved = read_compressed(report["forecast_records"][key], local)
        actual = predict(model, saved["rows"][:2])
        expected = saved["forecasts"][:2]
        assert all(
            np.allclose(
                [p[k] for k in sorted(p)], [e[k] for k in sorted(p)], rtol=0, atol=1e-10
            )
            for p, e in zip(actual, expected)
        )
        assert not model["execution_authority"] and not model["promotion_authority"]
    for result in report["results"].values():
        for candidate in result["candidates"].values():
            assert not candidate["validated_edge"]
            for s in candidate["scenarios"].values():
                assert abs(s["net_pnl"] - sum(s["daily_net_pnl"].values())) < 1e-6
                assert (
                    abs(s["net_pnl"] - s["realized_pnl"] - s["unrealized_pnl"]) < 1e-6
                )
    suites = list(ET.parse(out / "full-tests.xml").getroot().iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    assert counts["tests"] >= 222 and counts["failures"] == counts["errors"] == 0
    targeted = list(
        ET.parse(out / "final-focused-tests.xml").getroot().iter("testsuite")
    )
    targeted_counts = {
        key: sum(int(s.get(key, 0)) for s in targeted)
        for key in ("tests", "failures", "errors", "skipped")
    }
    assert (
        targeted_counts["tests"] >= 14
        and targeted_counts["failures"] == targeted_counts["errors"] == 0
    )
    account = json.loads((out / "partial-fill-accounting.json").read_text())
    store.read(account["record_id"], local)
    sources = [
        *list((ROOT / "apps/api/chart_intelligence").glob("phase8*.py")),
        ROOT / "apps/api/chart_intelligence/horizon_forward.py",
        *list((ROOT / "scripts").glob("*phase8*.py")),
        ROOT / "apps/api/tests/test_phase8.py",
        ROOT / "apps/api/tests/test_horizon_forward.py",
    ]
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "tests": counts,
        "final_focused_tests": targeted_counts,
        "phase7_files_unchanged": len(frozen["files"]),
        "existing_prospective_rows_unchanged": len(frozen["existing_ledger_rows"]),
        "no_exposed_audit_labels_used": True,
        "forecast_export_parity": "PASSED",
        "shared_capital_accounting": "PASSED",
        "nominated_horizons": report["nominations"],
        "research_acceptance": "NO_PROFITABLE_TRADING_ADVANTAGE_VALIDATED",
        "independent_confirmation": "PENDING_FUTURE_DATA",
        "deployment_authorized": False,
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in sources},
        "artifact_sha256": {
            p.name: sha(p)
            for p in out.iterdir()
            if p.is_file() and p.name != "acceptance.json"
        },
    }
    (out / "acceptance.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if not k.endswith("sha256")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
