"""Fixed root-owned operational job: calculate only the new pilot's report."""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path('/opt/keftrade-intelligence')
OPS=Path('/opt/keftrade-intelligence-ops')
reports=ROOT/'reports'
reports.mkdir(mode=0o700,exist_ok=True)
if sum(p.stat().st_size for p in reports.iterdir() if p.is_file())>=64*1024**2:
    raise RuntimeError('Own report directory reached 64 MiB; archive off-host before further reports')
if shutil.disk_usage(ROOT).free-8*1024**2<10*1024**3:
    raise RuntimeError('Filesystem reserve prevents new report; no data deleted')
source=(OPS/'paper-report.py').read_bytes()
digest=hashlib.sha256(source).hexdigest()
command=['docker','compose','--env-file','.env','-f','compose.yml','exec','-T','paper',
         'python','-','--persist','--source-sha',digest]
result=subprocess.run(command,cwd=ROOT/'releases/phase4',input=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
if result.returncode:
    raise RuntimeError(result.stderr.decode()[-2000:])
payload=json.loads(result.stdout)
assert payload['version']=='paper-session-report-v1' and payload['source_sha256']==digest
name='report-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ')+'.json'
target=reports/name
with target.open('xb') as output:
    output.write(result.stdout)
target.chmod(0o600)
print(json.dumps({'saved':str(target),'session':payload['session'],'account_data_available':payload['account_data_available'],
                  'order_count':len(payload['orders']),'source_sha256':digest}))
