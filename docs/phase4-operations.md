# Phase 4 operations

The independent application uses `/opt/keftrade-intelligence/releases/phase4` and
the Compose project `keftrade-intelligence`. No host ports are published. The
existing nginx container alone joins `intelligence-ingress`. Its original routes
are preserved, with an additional `/intelligence` route. Docker DNS is resolved at
request time so an unavailable Intelligence upstream cannot prevent nginx startup.
If nginx is recreated by a later unrelated deployment, reconnect that replacement
container to `intelligence-ingress`; a normal container restart retains membership.

Research and paper execution have separate PostgreSQL 18 services, passwords,
volumes, and internal networks. The paper worker has no research database password.
The research API has no execution keys. It can call only the narrow private paper
bridge with an independent token after session/CSRF validation. Frontend assets
contain no provider secrets. App containers run as UID 10001, the frontend as the
Node image's unprivileged user, without host/Docker sockets or elevated capabilities.

Secret files under `/etc/keftrade-intelligence` are mode 600, owned by the dedicated
`kefintelligence` service account. The paper file initially has empty key/secret and
account-ID values, a pinned forbidden old-account ID, and execution permission false.
An API key change alone does not prove separate account ownership. Before enabling,
verify the actual dedicated account ID differs, starts with no positions/orders,
has active tradable/fractionable assets for all six symbols, and passes cash and
clock/calendar reconciliation. Never replace these with old trader credentials.

Limits: API 512 MiB/1 CPU, research worker 768 MiB/1 CPU, paper worker 512 MiB/1 CPU,
web 384 MiB/0.5 CPU, research PostgreSQL 384 MiB/0.5 CPU, paper PostgreSQL 256 MiB/0.5
CPU. Docker logs cap at two 5 MiB files per service. Each database has a 256 MiB
application growth gate; research reserves a further 32 MiB before ingestion. The
paper loop disables execution at that gate or below 10 GiB filesystem free space.
PostgreSQL WAL targets 128 MiB per database. WAL settings and application growth
gates are operational controls, not filesystem quotas; PostgreSQL and external
services can still consume disk. Monitor actual free space. Ingestion stops rather
than deleting evidence. Vision remains unavailable pending licensing and deployment
clearance, with a production download ceiling of 64 MiB.

The initial disk projection uses measured union filesystem sizes: API 649,084,928
bytes, frontend approximately 329,793,536 bytes, PostgreSQL 312,389,632 bytes (shared
image, two clusters). The conservative installation/growth envelope is 2,352,037,888
bytes, plus 579,912,328 bytes of transfer/working allowance. Run preflight again
immediately before image loading; it must retain 10 GiB and a successful off-host
restore receipt. No old data cleanup or Docker pruning is part of this procedure.

Backups use `python -m opsbackup export PATH --kind research|paper`. Exports use a
repeatable-read consistent binary COPY snapshot with a manifest and SHA-256 hash.
The receiving PC runs `python -m opsbackup verify-local PATH --kind research|paper`
against its independent PostgreSQL 18 cluster on 5543. Restore creates a fresh
database and verifies all row counts. `restore-new` refuses any existing target
tables, requires an explicit database-name acknowledgement, and checks major version
and migration schema. Transport plus restore was checked before deployment; a new
VPS export must also be retrieved and restored after startup. Off-host backups need
periodic operator retrieval while the PC is available; no additional cloud backup
subscription or always-on off-host destination is configured.

Rollback: run `deploy/rollback.sh` from the new release. It restores only the saved
nginx configuration in place, validates nginx before reload, and stops the new app
processes. It preserves both new databases and the renewed TLS certificate. This
does not liquidate any paper position. No existing trading service or protected game
resource is involved. An emergency stop cancels only owned pending orders; positions
remain open. Review partial exits, cancellations without confirmation, and unknown
acknowledgements before further execution. One exit intent per stock per session
means a canceled partial exit requires operator review rather than a blind retry.

The pilot's forecasts are experimental. Its record is separate from the immutable
MSI/AXON shadow protocol. Premarket cutoff is 09:15 New York, issuance ends 09:25,
entry is 09:35–10:00, and ordinary exit is 15 minutes before the verified exchange
close. Missing pre-cutoff readiness records SKIP. A late setup is never backdated.
IEX-only execution marks and delayed historical SIP features are labeled separately.
Balances/P&L are unavailable without dedicated account verification; $600 is a
configured allocation, not an invented broker balance. Forecast outcomes resolve on
a later calendar day after complete source bars arrive. A profitable paper trade is
not validation, and the frozen minimum-evidence requirements are unchanged.

The initial daily-loss limit and timeout can be tightened with
`INTELLIGENCE_PAPER_DAILY_LOSS_LIMIT` (0 < limit <= $12) and
`INTELLIGENCE_PAPER_ORDER_TIMEOUT_SECONDS` (5–90 seconds). Initial values remain
$12 and 90 seconds. Actual settings are recorded with each decision. Changing
model parameters or relaxing risk limits requires a new reviewed protocol version.

Run `.venv/Scripts/python.exe scripts/backup-vps.py` on the receiving PC while its
independent local database is running. It exports the live VPS databases, retrieves
them, restores both to fresh local databases, and retains the off-host archives and
receipts under `.local/off-host`. Only this invocation's server transport copies
are removed after successful verification; the database evidence remains intact.
SSH passwords can be entered interactively and need not be stored in the project.

The scoped `keftrade-intelligence-tls.timer` checks the existing hostname's certificate
weekly. Its root-owned helper under `/opt/keftrade-intelligence-ops` renews only that
certificate with a pinned Certbot image and validates nginx before reload. No app
container receives Docker access or certificate keys. The timer's first manual check
completed successfully. The previous expired certificate was renewed to February 11,
2027 before the new HTTPS route was installed.
