"""Validate frozen research identities and final local engineering evidence."""

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.phase7 import AUDIT_END, CANDIDATES, predict


def main():
    out = ROOT / "reports/phase7"
    root = ROOT / ".local/phase7"
    summary = json.loads((out / "research-results.json").read_text())
    report = store.read(summary["report_id"], root)["payload"]
    declaration = store.read(report["declaration_id"], root)["payload"]
    store.read(declaration["source_archive"], root)
    frozen = store.read(report["frozen_selection_id"], root)["payload"]
    data = store.read(report["dataset_id"], root)["payload"]
    forecasts = store.read(summary["forecasts_id"], root)["payload"]
    assert max(r["date"] for r in data["rows"]) <= AUDIT_END
    assert len(frozen["artifacts"]) == len(CANDIDATES) == 8
    for name, key in frozen["artifacts"].items():
        artifact = store.read(key, root)["payload"]
        expected = forecasts["predictions"][name][:3]
        actual = predict(artifact, forecasts["rows"][:3])
        assert all(
            np.allclose([a[k] for k in sorted(a)], [e[k] for k in sorted(a)], rtol=0, atol=1e-10)
            for a, e in zip(actual, expected)
        )
    tests = {}
    for name in ("full-tests.xml", "final-focused-tests.xml"):
        suites = list(ET.parse(out / name).getroot().iter("testsuite"))
        tests[name] = {
            key: sum(int(s.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        assert tests[name]["failures"] == tests[name]["errors"] == 0
    assert tests["full-tests.xml"]["tests"] >= 209
    assert "Generating static pages" in (out / "web-build.txt").read_text()
    assert report["acceptance"]["deployable"] is False
    files = list((ROOT / "apps/api/chart_intelligence").glob("*.py"))
    files += [
        ROOT / p
        for p in (
            "scripts/run-phase7-research.py",
            "scripts/enroll-phase7-prospective.py",
            "scripts/report-phase7.py",
            "scripts/observe-phase7-paper.py",
            "apps/web/components/Phase7Research.tsx",
            "apps/web/components/PaperDashboard.tsx",
        )
    ]
    manifest = {
        "verified_at": datetime.now(UTC).isoformat(),
        "tests": tests,
        "frontend_build": "PASSED",
        "frozen_report_id": summary["report_id"],
        "no_exposed_evaluation_rows": True,
        "model_export_parity": "PASSED",
        "research_acceptance": "FAILED_NO_CREDIBLE_COST_ADJUSTED_ADVANTAGE",
        "deployment_authorized": False,
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
        },
        "artifacts_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in out.iterdir()
            if p.is_file() and p.name != "acceptance.json"
        },
        "dependency_lock_sha256": hashlib.sha256(
            (ROOT / "apps/api/requirements.lock.txt").read_bytes()
        ).hexdigest(),
    }
    (out / "acceptance.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if not k.endswith("sha256")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
