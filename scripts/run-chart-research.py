"""Bounded offline experiment on existing hash-verified local evidence."""

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import Protocol, dataset, evaluate, identity
from intelligence.util import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / ".local/mainkraft/history/coverage.jsonl",
    )
    args = parser.parse_args()
    entries = [
        json.loads(line)
        for line in args.manifest.read_text().splitlines()
        if line.strip()
    ]
    snapshots = {}
    for row in entries:
        with gzip.open(
            args.manifest.parent / (row["hash"] + ".json.gz"), "rt", encoding="utf-8"
        ) as f:
            snapshot = json.load(f)
        if digest(snapshot) != row["hash"]:
            raise ValueError("Snapshot hash mismatch")
        snapshots[row["symbol"]] = snapshot
    source_bytes = {
        p.name: p.read_text()
        for p in (ROOT / "apps/api/chart_intelligence").glob("*.py")
    }
    source_id = store.put("source_archive", source_bytes)
    sources = {name: identity(content) for name, content in source_bytes.items()}
    declaration = {
        "protocol": Protocol().__dict__,
        "snapshots": entries,
        "source_hashes": sources,
        "source_archive_id": source_id,
        "numerical_model_source_id": store.put(
            "numerical_model_source",
            (ROOT / "apps/api/intelligence/portable_models.py").read_text(),
        ),
        "dependencies_id": store.put(
            "dependencies", (ROOT / "apps/api/requirements.lock.txt").read_text()
        ),
        "confirmation": "Historical held-out suffix; previously exposed by other studies. Fresh independent validation required",
    }
    declared = store.put("declaration", declaration)
    print("Declared", declared, flush=True)
    rows, rejections = dataset(snapshots)
    print("Dataset", len(rows), "rejections", len(rejections), flush=True)
    dataset_id = store.put(
        "dataset", {"rows": rows, "rejections": rejections, "declaration_id": declared}
    )
    artifact, report = evaluate(rows)
    model_id = store.put("model", artifact)
    report.update(
        {
            "model_id": model_id,
            "dataset_id": dataset_id,
            "declaration_id": declared,
            "rejections": len(rejections),
        }
    )
    report_id = store.put("evaluation", report)
    store.publish(report_id)
    folder = ROOT / "reports/autonomy-implementation"
    folder.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in report.items() if k != "predictions"}
    summary["report_id"] = report_id
    (folder / "chart-evaluation.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
