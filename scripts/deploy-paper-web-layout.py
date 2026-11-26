"""Deploy the tested dashboard layout without touching the trading worker."""
import hashlib
import io
import json
import shlex
import tarfile
from pathlib import Path

from vps_access import connect

ROOT=Path(__file__).resolve().parents[1]
TAG='paper-learning-webfix-v2'


def command(client,cmd):
    _,out,err=client.exec_command(cmd,timeout=180)
    result=out.read().decode();error=err.read().decode()
    if out.channel.recv_exit_status():raise RuntimeError(error[-1600:] or result[-1600:])
    return result


def main():
    web=ROOT/'apps/web/.next/standalone';files={'server.js':web/'server.js'}
    for folder,destination in ((web/'.next','.next'),(ROOT/'apps/web/.next/static','.next/static')):
        for path in folder.rglob('*'):
            if path.is_file():files[destination+'/'+path.relative_to(folder).as_posix()]=path
    package=io.BytesIO()
    with tarfile.open(fileobj=package,mode='w:gz') as tar:
        for name,path in files.items():tar.add(path,arcname=name,recursive=False)
        body=b'FROM keftrade-intelligence-web:paper-learning-webfix-v1\nCOPY --chown=node:node .next /app/.next\nCOPY --chown=node:node server.js /app/server.js\n'
        member=tarfile.TarInfo('Dockerfile');member.size=len(body);tar.addfile(member,io.BytesIO(body))
    out=ROOT/'reports/web-layout-fix';out.mkdir(exist_ok=True)
    manifest={'image':'keftrade-intelligence-web:'+TAG,'package_sha256':hashlib.sha256(package.getvalue()).hexdigest(),
              'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()}}
    with connect() as client:
        before=command(client,"docker inspect keftrade-intelligence-paper-1 --format '{{.Id}} {{.State.StartedAt}}'").strip()
        remote=command(client,'mktemp -d /tmp/mainkraft-layout.XXXXXXXX').strip()
        assert remote.startswith('/tmp/mainkraft-layout.')
        with client.open_sftp() as sftp:
            with sftp.file(remote+'/release.tar.gz','wb') as f:f.write(package.getvalue())
        q=shlex.quote(remote)
        command(client,f'tar -xzf {q}/release.tar.gz -C {q} && docker build --network=none -t keftrade-intelligence-web:{TAG} {q}')
        program=r'''
from pathlib import Path
import re,shutil,subprocess
p=Path('/opt/keftrade-intelligence/releases/phase4');env=p/'.env';override=p/'compose.paper-learning.yml'
original=env.read_text();old=override.read_text()
pattern=r'(?m)^INTELLIGENCE_WEB_IMAGE_TAG=.*$'
assert len(re.findall(pattern,original))==1 and old.count('image: keftrade-intelligence-web:paper-learning-webfix-v1')==1
updated=re.sub(pattern,"INTELLIGENCE_WEB_IMAGE_TAG='paper-learning-webfix-v2'",original)
new=old.replace('image: keftrade-intelligence-web:paper-learning-webfix-v1','image: keftrade-intelligence-web:paper-learning-webfix-v2')
assert not (p/'.env.before-web-layout-v2').exists()
shutil.copy2(env,p/'.env.before-web-layout-v2');shutil.copy2(override,p/'compose.paper-learning.before-web-layout-v2.yml')
try:
    env.write_text(updated);override.write_text(new)
    subprocess.run(['sudo','docker','compose','--project-directory',str(p),'--env-file',str(env),'-f',str(p/'compose.yml'),'-f',str(override),'config','--quiet'],check=True)
except Exception:
    env.write_text(original);override.write_text(old);raise
'''
        command(client,'python3 -c '+shlex.quote(program))
        command(client,'cd /opt/keftrade-intelligence/releases/phase4 && sudo docker compose --env-file .env -f compose.yml -f compose.paper-learning.yml up -d --no-build --no-deps web')
        after=command(client,"docker inspect keftrade-intelligence-paper-1 --format '{{.Id}} {{.State.StartedAt}}'").strip()
        assert before==after
        manifest['paper_container_unchanged']=True
    (out/'release-manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Deployed dashboard layout. Trading worker unchanged.')


if __name__=='__main__':main()
