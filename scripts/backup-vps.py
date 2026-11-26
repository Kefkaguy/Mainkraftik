"""Retrieve and restore-test only the two new Intelligence databases off-host."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from opsbackup import verify_local
from vps_access import connect


def command(client,text):
    _,out,err=client.exec_command(text,timeout=180)
    result=out.read().decode()
    if out.channel.recv_exit_status()!=0:
        raise RuntimeError(err.read().decode()[-2000:])
    return result


def main():
    stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    folder=ROOT/'.local/off-host'/stamp
    folder.mkdir(parents=True,exist_ok=False)
    results=[]
    with connect() as client:
        for kind,service in [('research','api'),('paper','paper')]:
            name=f'{kind}-{stamp}.zip'
            tmp='/tmp/'+name
            remote='/opt/keftrade-intelligence/backups/'+name
            command(client,"python3 -c \"import shutil; assert shutil.disk_usage('/opt').free-33554432>=10737418240, 'Disk reserve prevents backup'\"")
            prefix='cd /opt/keftrade-intelligence/releases/phase4 && sudo docker compose --env-file .env -f compose.yml '
            command(client,prefix+f'exec -T {service} python -m opsbackup export {tmp} --kind {kind}')
            # Docker cp does not reliably expose tmpfs mounts. Read the archive
            # through exec instead, with binary stdout and no TTY.
            command(client,'umask 077; '+prefix+f'exec -T {service} cat {tmp} > {remote} && chmod 600 {remote}')
            local=folder/name
            with client.open_sftp() as sftp:
                sftp.get(remote,str(local))
            receipt=verify_local(local,kind)
            with client.open_sftp() as sftp:
                sftp.put(str(local.with_suffix('.receipt.json')),remote.removesuffix('.zip')+'.receipt.json')
            # Remove only this invocation's transport copies after successful
            # off-host restore. The verified receiving archive is retained.
            assert name.startswith(kind+'-') and name.endswith('.zip') and '..' not in name
            command(client,prefix+f'exec -T {service} python -c "from pathlib import Path; p=Path(\'{tmp}\'); assert p.parent==Path(\'/tmp\') and not p.is_symlink(); p.unlink()"')
            command(client,f"python3 -c \"from pathlib import Path; p=Path('{remote}'); assert p.parent==Path('/opt/keftrade-intelligence/backups') and not p.is_symlink(); p.unlink()\"")
            results.append({'kind':kind,'restore_verified':True,'bytes':receipt['bytes'],'sha256':receipt['sha256'],'receipt':str(local.with_suffix('.receipt.json'))})
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
