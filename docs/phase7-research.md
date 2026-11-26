# Phase 7: bounded AI strategy research

The completed report is [Phase 7 results](../reports/phase7/README.md).
The engineering implementation passes automated tests; the research conclusion
is **no independently validated strategy**. No deployed policy or credentials
were changed.

## Reproducible commands

From the Intelligence project root, using its existing environment:

```powershell
.venv\Scripts\python.exe scripts/run-phase7-research.py
.venv\Scripts\python.exe scripts/enroll-phase7-prospective.py
.venv\Scripts\python.exe scripts/report-phase7.py
.venv\Scripts\python.exe scripts/audit-phase7-evidence.py
```

The research command returns the existing completed search after its first run.
Source edits must not silently create a second search on the exposed audit set.
Immutable model/data/declaration/forecast records are under `.local/phase7`.
The initial training sources are archived inside that record store. Later
reporting/accounting clarifications are separate addenda; original results
remain unchanged.

## Prospective collection

The frozen enrollment record is `reports/phase7/prospective-enrollment.json`.
It pins a forecast diagnostic reference in the existing local controller without
publishing a new default model or any trading policy. To run the existing watcher:

```powershell
.venv\Scripts\python.exe scripts/monitor-chart-research.py --report-id d709b24bcd5094e8df955e9fe32ba47761bef8233b7be55f8a19e2d0d1972931 --symbols AMD MSFT GOOG AMZN TSM META --watch
```

No watcher or new VPS service was installed by this task. One real collection
was verified successfully after correcting the delayed-SIP request boundary.
At that time the exchange was closed and outside research issuance windows;
zero prospective forecasts were recorded. Fresh future sessions remain required.

Research issuance stays at 12:00/14:00 New York with a two-minute deadline.
The controller records completion time after inference, refuses timestamps before
enrollment, and cannot rewrite an existing prediction after restart. Features
remain causal. Outcomes attach separately after source bars mature.

Hypothetical opportunities retain confidence, spread and cost-allowance gates.
An eligibility decision does not create a fill: quote-priced outcomes require a
subsequent recorded quote after prediction and a timely exit quote. Missing
quotes produce UNPRICED_ENTRY / UNPRICED_EXIT, not invented returns. These are
individual hypothetical opportunities, not an automatically allocated portfolio
or actual paper fills. The paper policy and broker account remain independent.

## Interfaces and tests

`GET /v1/chart-intelligence/phase7` is authenticated and read-only. The existing
Mainkraft dashboard has a Phase 7 panel that labels audit results and synthetic
P/L. It has no promotion or execution action.

Tests cover forbidden evaluation dates, prediction independence from label
inputs, all eight bounded model exports, delayed SIP requests, no broker-host
requests, post-inference timestamps, enrollment restrictions, immutable
forecasts, absent/stale quotes, deterministic rejection/partial-fill scenarios,
position/cash/daily-loss limits, and conservative portfolio accounting.
