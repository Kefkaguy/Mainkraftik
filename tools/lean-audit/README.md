# Offline LEAN reconciliation adapter

This component audit is a deployment gate input, not proof of full engine parity.
It executes real LEAN `EquityFillModel` quote fills, then compares quantities,
cash and inventory with the explicitly specified fixture ledger. Partial fills
are child slices. Session schedules, worker restarts and loss resets are separate
Python tests. Full LEAN algorithm lifecycle reconciliation is still required.

From the repository root, with .NET 10 SDK available locally:

```powershell
git clone https://github.com/QuantConnect/Lean.git .local/lean-audit-source
git -C .local/lean-audit-source checkout 949671e21f1258d8cc7f2472f5f5d75111a8cb32
& ./.local/dotnet10/dotnet.exe build tools/lean-audit/LeanAudit.csproj -c Release -p:RunAnalyzers=false
.venv/Scripts/python.exe scripts/audit-mainkraft-simulator.py
.venv/Scripts/python.exe -m pytest apps/api/tests/test_strategy_lab.py -q
```

Install the official .NET 10 SDK into `.local/dotnet10` or adjust the audit script
path for your SDK. Network access is needed for source/package setup; the fixture
execution itself is offline and receives no credentials. The pinned upstream build
reports vulnerable transitive NuGet packages. Do not package the audit adapter,
source checkout, SDK or build artifacts into the application image. This harness
does not parse downloaded archives or accept external input.

Run the laboratory and export its review:

```powershell
.venv/Scripts/python.exe scripts/run-mainkraft-strategy-lab.py
.venv/Scripts/python.exe scripts/report-mainkraft-strategy-lab.py
```

Prior local dataset snapshots under `.local/mainkraft/walk-forward-v1` are required.
The laboratory does not acquire a replacement feed silently. Content-addressed
experiment records live in `.local/strategy-lab/records`; preserve this directory
with the source snapshots when backing up research. Reports remain exploratory.
