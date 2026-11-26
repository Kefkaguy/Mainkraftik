#!/bin/sh
# Revert only the Intelligence route and stop its application processes.
# Existing databases, evidence, certificate, and unrelated containers remain.
set -eu
test -f /opt/keftrade-intelligence-ops/nginx-before-phase4.conf
sudo python3 - <<'PY'
from pathlib import Path
source=Path('/opt/keftrade-intelligence-ops/nginx-before-phase4.conf')
target=Path('/opt/keftrade/deploy/production/nginx/keftrade.conf')
# Preserve the inode of the existing read-only bind mount.
target.write_bytes(source.read_bytes())
PY
sudo docker exec production-nginx-1 nginx -t
sudo docker exec production-nginx-1 nginx -s reload
cd /opt/keftrade-intelligence/releases/phase4
sudo docker compose --env-file .env -f compose.yml stop paper worker api web
printf '%s\n' 'Intelligence route rolled back; application stopped. Paper positions, if any, require explicit review.'
