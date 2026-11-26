"""Deploy the explicitly authorized two-account PAPER experiment, scoped to paper/web.

Requires local test reports and restored off-host backups. Never uploads credentials
in an image or logs them. The original paper environment and brokerage key stay intact.
"""
import base64
import hashlib
import io
import json
import shlex
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from vps_access import connect

ROOT=Path(__file__).resolve().parents[1]
STAGE='/opt/keftrade-intelligence/releases/phase4'
RELEASE='paper-learning-v1'
OUT=ROOT/'reports/paper-learning'


def command(client,text,timeout=180):
    _,stdout,stderr=client.exec_command(text,timeout=timeout)
    result=stdout.read().decode();error=stderr.read().decode()
    if stdout.channel.recv_exit_status():raise RuntimeError(error[-1600:] or result[-1600:])
    return result


def private(client,source):
    encoded=base64.b64encode(source.encode()).decode()
    return command(client,'docker exec keftrade-intelligence-paper-1 python -c '+shlex.quote(f'import base64;exec(base64.b64decode("{encoded}"))'))


HTTP="""
import json,os,urllib.request
headers={'X-Pilot-Token':os.environ['INTELLIGENCE_PAPER_INTERNAL_TOKEN'],'Content-Type':'application/json'}
def request(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8200/'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
"""


def main():
    for name in ('full-tests.xml','final-focused-tests.xml'):
        suites=ET.parse(OUT/name).getroot().findall('.//testsuite')
        assert suites and all(int(s.get('failures',0))+int(s.get('errors',0))==0 for s in suites)
    backup=sorted((ROOT/'.local/off-host').glob('*-mainkraft-before'))[-1]
    for kind in ('paper','research'):
        receipt=json.loads((backup/f'{kind}.receipt.json').read_text())
        assert receipt['sha256']==hashlib.sha256((backup/f'{kind}.zip').read_bytes()).hexdigest()
    frozen=json.loads((ROOT/'reports/phase8/phase7-freeze.json').read_text())
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha for path,sha in frozen['files'].items())
    files={f'paper/{name}':ROOT/'apps/api/paperpilot'/name for name in ('experiment.py','service.py','config.py')}
    files['paper/opsbackup.py']=ROOT/'apps/api/opsbackup.py'
    web=ROOT/'apps/web/.next/standalone'
    assert (web/'server.js').exists()
    files['web/server.js']=web/'server.js'
    for folder,destination in ((web/'.next','web/.next'),(ROOT/'apps/web/.next/static','web/.next/static')):
        for path in folder.rglob('*'):
            if path.is_file():files[destination+'/'+path.relative_to(folder).as_posix()]=path
    dockerfiles={
        'paper/Dockerfile':'FROM keftrade-intelligence-api:mainkraft-swing-sized-v2\nCOPY --chown=10001:10001 experiment.py service.py config.py /app/apps/api/paperpilot/\nCOPY --chown=10001:10001 opsbackup.py /app/apps/api/opsbackup.py\n',
        'web/Dockerfile':'FROM keftrade-intelligence-web:mainkraft-swing-sized-v1\nCOPY --chown=node:node .next /app/.next\nCOPY --chown=node:node server.js /app/server.js\n'}
    package=io.BytesIO()
    with tarfile.open(fileobj=package,mode='w:gz') as tar:
        for name,path in files.items():tar.add(path,arcname=name,recursive=False)
        for name,body in dockerfiles.items():
            blob=body.encode();member=tarfile.TarInfo(name);member.size=len(blob);tar.addfile(member,io.BytesIO(blob))
    manifest={'release':RELEASE,'backup':str(backup),'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()},
              'package_sha256':hashlib.sha256(package.getvalue()).hexdigest(),'phase7_files_unchanged':len(frozen['files'])}
    (OUT/'release-manifest.json').write_text(json.dumps(manifest,indent=2))
    override=f'''services:
  paper:
    image: keftrade-intelligence-api:{RELEASE}
    environment:
      INTELLIGENCE_PAPER_EXPERIMENT_ENABLED: 'true'
      INTELLIGENCE_PAPER_EXPERIMENT_ACCOUNTS_FILE: /run/mainkraft-experiment/account2.json
    volumes:
      - /etc/keftrade-intelligence/paper-learning/account2.json:/run/mainkraft-experiment/account2.json:ro
  web:
    image: keftrade-intelligence-web:{RELEASE}
'''
    with connect() as client:
        remote=command(client,'mktemp -d /tmp/mainkraft-paper-learning.XXXXXXXX').strip()
        assert remote.startswith('/tmp/mainkraft-paper-learning.')
        with client.open_sftp() as sftp:
            with sftp.file(remote+'/release.tar.gz','wb') as f:f.write(package.getvalue())
            sftp.put(str(ROOT/'.local/paper-experiments/account2.json'),remote+'/account2.json')
            sftp.chmod(remote+'/account2.json',0o600)
            with sftp.file(remote+'/override.yml','w') as f:f.write(override)
        print('Uploaded scoped paper/web release:',len(package.getvalue()),'bytes',flush=True)
        q=shlex.quote(remote)
        print(command(client,f'tar -xzf {q}/release.tar.gz -C {q} && docker build --network=none -t keftrade-intelligence-api:{RELEASE} {q}/paper && docker build --network=none -t keftrade-intelligence-web:{RELEASE} {q}/web'),flush=True)
        # Stop here on any build/test error: the old paper process is still intact.
        compose=f'cd {STAGE} && sudo docker compose --env-file .env -f compose.yml -f compose.paper-learning.yml '
        command(client,f'if test -e {STAGE}/compose.paper-learning.yml; then cmp {q}/override.yml {STAGE}/compose.paper-learning.yml; else cp -p {STAGE}/compose.yml {STAGE}/compose.before-paper-learning.yml; fi')
        command(client,f'sudo install -d -o root -g root -m 755 /etc/keftrade-intelligence/paper-learning && sudo install -o 10001 -g 10001 -m 400 {q}/account2.json /etc/keftrade-intelligence/paper-learning/account2.json && install -m 644 {q}/override.yml {STAGE}/compose.paper-learning.yml')
        # No brokerage settings, unrelated services, original env, or original tables are changed.
        activated=False
        try:
            command(client,compose+'config --quiet')
            print(command(client,compose+'up -d --no-build --no-deps --wait --wait-timeout 90 paper web'),flush=True)
            status=None
            for _ in range(15):
                status=json.loads(private(client,HTTP+"print(json.dumps(request('status')))"))
                if len(status.get('accounts',[]))==2:break
                time.sleep(2)
            assert status and status['experimental'] and status['paper_only'] and not status['enabled']
            assert {a['account_number'] for a in status['accounts']}=={'PA3NSJAY7','PA3D4GIWG'}
            assert all(a['snapshot'] and not a['snapshot']['market_open'] for a in status['accounts'])
            assert not any(a['orders'] for a in status['accounts'])
            # Verify private auth and public website before enabling either account.
            private(client,"""import urllib.request,urllib.error
try:urllib.request.urlopen('http://127.0.0.1:8200/status',timeout=5)
except urllib.error.HTTPError as exc:assert exc.code in (401,403)
else:raise RuntimeError('Private paper status did not require authorization')
""")
            command(client,"curl -fsS -L --max-time 20 https://keftrade.duckdns.org/intelligence -o /dev/null")
            private(client,HTTP+"assert request('control',{'enabled':True})['enabled']")
            activated=True
            for _ in range(12):
                status=json.loads(private(client,HTTP+"print(json.dumps(request('status')))"))
                if all(a['snapshot']['enabled'] for a in status['accounts']):break
                time.sleep(2)
            assert status['enabled'] and len(status['accounts'])==2
            (OUT/'deployed-status.json').write_text(json.dumps(status,indent=2))
            print(json.dumps({'enabled':status['enabled'],'accounts':[{'account_number':a['account_number'],'equity':a['snapshot']['account']['equity'],'market_open':a['snapshot']['market_open'],'orders':len(a['orders'])} for a in status['accounts']]}),flush=True)
        except Exception:
            if activated:
                private(client,HTTP+"request('control',{'enabled':False})")
                print('New submissions paused after failed verification. Positions preserved; reconcile before rollback.',flush=True)
            else:
                command(client,f'cd {STAGE} && sudo docker compose --env-file .env -f compose.yml up -d --no-build --no-deps --wait --wait-timeout 90 paper web')
            raise
        finally:
            command(client,f'rm -f -- {q}/account2.json')
        print('Activation verified. Use compose.paper-learning.yml for future paper/web restarts.',flush=True)


if __name__=='__main__':main()
