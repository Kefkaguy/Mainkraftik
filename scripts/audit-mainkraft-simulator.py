"""Offline, pinned LEAN quote-fill reconciliation; never contacts a broker."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from paperpilot import lab_registry as registry
from paperpilot.simulation_rules import bar_trigger, daily_change

COMMIT = "949671e21f1258d8cc7f2472f5f5d75111a8cb32"


def main():
    source = ROOT / ".local/lean-audit-source"
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != COMMIT:
        raise ValueError("LEAN revision differs from declared audit revision")
    fixtures = []
    for name, quantities, prices in [
        ("spread_once", [10, -10], [(99.9, 100.1), (102.9, 103.1)]),
        ("partial_slices_restart", [4, 6, -3, -7], [(99.9, 100.1), (100, 100.2), (101, 101.2), (102, 102.2)]),
        ("adverse_gap_exit", [10, -10], [(99.9, 100.1), (89.9, 90.1)]),
        ("stop_first_delayed_exit", [10, -10], [(99.9, 100.1), (97.8, 98)]),
        ("early_close_exit", [10, -10], [(99.9, 100.1), (100.9, 101.1)]),
    ]:
        fixtures.append({"name": name, "orders": [
            {"qty": qty, "bid": bid, "ask": ask,
             "time": f"2026-11-27T{('09:35:00' if i == 0 else '12:45:00')}"}
            for i, (qty, (bid, ask)) in enumerate(zip(quantities, prices, strict=True))]})
    path = registry.LAB / "lean-fixtures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixtures), encoding="utf-8")
    dotnet = ROOT / ".local/dotnet10/dotnet.exe"
    assembly = ROOT / "tools/lean-audit/bin/Release/net10.0/LeanAudit.dll"
    output = subprocess.check_output([str(dotnet), str(assembly), str(path)], text=True, cwd=ROOT)
    lean = json.loads(next(line.removeprefix("AUDIT_JSON=") for line in output.splitlines() if line.startswith("AUDIT_JSON=")))
    comparisons = []
    for fixture, result in zip(fixtures, lean, strict=True):
        cash, qty = 5000., 0.
        for step, fill in zip(fixture["orders"], result["fills"], strict=True):
            price = step["ask"] if step["qty"] > 0 else step["bid"]
            assert abs(fill["price"] - price) < 1e-8
            assert fill["quantity"] == step["qty"]
            cash -= step["qty"] * price
            qty += step["qty"]
        assert abs(cash - result["cash"]) < 1e-8 and qty == result["inventory"]
        comparisons.append({"name": fixture["name"], "matched": True, "cash": cash, "inventory": qty, "pnl": cash - 5000, "order_count": len(fixture["orders"])})
    assert bar_trigger(100, 104, 97, 100, .02, .03) == (98, "stop")
    assert bar_trigger(90, 94, 89, 100, .02, .03)[0] == 90
    assert daily_change(4950, 5000) == -50 and daily_change(4950, 4950) == 0
    report = {"status": "component_reconciled_full_engine_pending", "lean_commit": COMMIT,
              "assembly_sha256": hashlib.sha256(assembly.read_bytes()).hexdigest(),
              "fixtures_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "comparisons": comparisons, "unexplained_component_differences": 0,
              "deployment_gate_passed": False,
              "scope": "Actual compiled LEAN EquityFillModel market fills and explicit cash/inventory reconciliation. Partial fills are explicit child slices; calendar, idempotency and reset checks are Python tests. Not a complete LEAN algorithm/portfolio replay.",
              "remaining": ["Full LEAN algorithm order lifecycle including partial-fill notifications and restart persistence", "Shared live/replay integration acceptance across session schedules", "Historical study uses five-minute bar approximations, not historical executable quotes"],
              "dependency_note": "Pinned upstream audit build reports NuGet vulnerabilities; offline fixtures only, excluded from application deployment."}
    registry.put("simulator_component_audit", report)
    (registry.LAB / "simulator-audit.json").write_text(json.dumps(report, indent=2))
    latest = registry.latest()
    latest["simulator_audit"] = report
    print("Published", registry.publish(latest))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
