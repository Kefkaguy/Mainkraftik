"""Verify local test/research evidence and write a reproducible release inventory.

This is an evidence audit, never a deployment authorization.
"""

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import identity


def main():
    folder = ROOT / "reports/autonomy-implementation"
    tree = ET.parse(folder / "full-tests.xml")
    suites = list(tree.getroot().iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["failures"] or counts["errors"] or counts["tests"] < 190:
        raise ValueError("Backend acceptance evidence failed")
    summary = json.loads((folder / "chart-evaluation.json").read_text())
    report = store.read(summary["report_id"])["payload"]
    model = store.read(report["model_id"])
    dataset = store.read(report["dataset_id"])["payload"]
    declaration = store.read(report["declaration_id"])["payload"]
    sources = store.read(declaration["source_archive_id"])["payload"]
    assert all(
        identity(content) == declaration["source_hashes"][name]
        for name, content in sources.items()
    )
    store.read(declaration["numerical_model_source_id"])
    store.read(declaration["dependencies_id"])
    assert not report["execution_authority"] and not report["promotion_authority"]
    assert dataset["declaration_id"] == report["declaration_id"]
    assert model["payload"]["version"] == report["version"]
    assert "Generating static pages" in (folder / "web-build.txt").read_text()
    files = [
        p
        for directory in ("apps/api/chart_intelligence",)
        for p in (ROOT / directory).glob("*.py")
    ]
    files += [
        ROOT / p
        for p in (
            "apps/api/paperpilot/decision_trace.py",
            "apps/api/paperpilot/risk.py",
            "apps/api/paperpilot/strategy_execution.py",
            "apps/api/paperpilot/engine.py",
            "apps/api/paperpilot/strategy_runtime.py",
            "apps/api/paperpilot/service.py",
            "apps/api/paperpilot/fill_replay.py",
            "apps/api/intelligence/main.py",
            "apps/api/intelligence/paper_bridge.py",
            "apps/web/components/AutonomyPanel.tsx",
            "apps/web/components/PaperDashboard.tsx",
        )
    ]
    inventory = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    }
    artifacts = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.iterdir()
        if p.is_file() and p.name != "acceptance.json"
    }
    result = {
        "backend": counts,
        "frontend_build": "PASSED",
        "chart_report_id": summary["report_id"],
        "source_sha256": inventory,
        "artifact_sha256": artifacts,
        "local_engineering_stages": {str(i): "PASSED" for i in range(1, 7)},
        "model_validation": "FAILED_TO_ESTABLISH_IMPROVEMENT",
        "deployment_authorized": False,
        "limitations": [
            "No real broker orders submitted in verification",
            "Historical executable-quote realism remains unverified",
            "Existing external LEAN component report is preserved; full LEAN algorithm acceptance is not certified by these tests",
            "Neural optimizer hit fixed budgets; independent prospective validation required",
        ],
    }
    (folder / "acceptance.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"source_sha256", "artifact_sha256"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
