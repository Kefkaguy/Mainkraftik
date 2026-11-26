"""Local WSL Python/Rust worker handoff using only the owned validation cluster."""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = Path.home()/'.local/share/mainkraft-rust-validation'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(destination):
    import fcntl
    import psycopg
    assert 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    destination.resolve().relative_to((ROOT/'.local/rust-migration/linux-paper-handoff').resolve())
    lock=(VALIDATION/'postgres-validation.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    pointer=json.loads((VALIDATION/'postgres18/active-cluster.json').read_text())
    definition=Path(pointer['file']);assert sha(definition)==pointer['sha256']
    cluster=json.loads(definition.read_text());data=Path(cluster['data']).resolve()
    data.relative_to((VALIDATION/'postgres18').resolve())
    assert data.name=='data' and (data/'PG_VERSION').read_text().strip()=='18'
    assert cluster['port']==5543 and cluster['listen_addresses']==['127.0.0.1']
    binary=Path(cluster['bin'])/'pg_ctl'
    assert binary.resolve()==data.parent/'runtime/usr/lib/postgresql/18/bin/pg_ctl'
    environment=dict(os.environ,LD_LIBRARY_PATH=str(Path(cluster['runtime'])/'usr/lib/x86_64-linux-gnu'))
    started=False
    def control(action):
        command=[str(binary),'-D',str(data),'-t','30']
        command+=['-l',str(data.parent/'server.log'),'-w','start'] if action=='start' else ['-m','fast','-w','stop']
        result=subprocess.run(command,env=environment,capture_output=True,text=True)
        (destination/('cluster-'+action+'.txt')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,'Owned validation cluster operation failed'
    if (data/'postmaster.pid').exists():
        pid=int((data/'postmaster.pid').read_text().splitlines()[0])
        command=(Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0',b' ').decode()
        assert str(data) in command and str(Path(cluster['bin'])/'postgres') in command
    else:
        with socket.socket() as probe:
            probe.settimeout(1);assert probe.connect_ex(('127.0.0.1',5543))!=0,'Foreign listener'
        control('start');started=True
    def databases():
        with psycopg.connect('postgresql://rust_fixture@127.0.0.1:5543/postgres',connect_timeout=5) as c:
            return [r[0] for r in c.execute('SELECT datname FROM pg_database ORDER BY datname')]
    sources=[ROOT/'scripts/test-rust-paper-handoff.py',Path(__file__),ROOT/'apps/api/paperpilot/experiment.py',
             ROOT/'apps/backend-rust/src/engine.rs',ROOT/'apps/backend-rust/src/db.rs',ROOT/'apps/backend-rust/tests/process_recovery_lifecycle.rs']
    before={p.relative_to(ROOT).as_posix():sha(p) for p in sources}
    passed=False
    try:
        assert databases()==['postgres','template0','template1']
        environment.update(CARGO_HOME=str(VALIDATION/'cargo'),RUSTUP_HOME=str(VALIDATION/'rustup'),
                           CARGO_TARGET_DIR=str(VALIDATION/'target'),CARGO_BUILD_JOBS='2',RUSTUP_TOOLCHAIN='1.93.0',
                           PATH=str(VALIDATION/'cargo/bin')+':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                           INTELLIGENCE_PAPER_DATABASE_URL='postgresql://rust_fixture@127.0.0.1:5543/intelligence_paper')
        with (destination/'handoff.txt').open('w') as log:
            result=subprocess.run([sys.executable,'scripts/test-rust-paper-handoff.py'],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,text=True)
        assert result.returncode==0,'Linux original Python/Rust handoff failed'
        reference=json.loads((ROOT/'.local/rust-migration/latest-paper-handoff.json').read_text())
        evidence=ROOT/reference['file'];assert sha(evidence)==reference['sha256']
        receipt=json.loads(evidence.read_text());assert receipt['passed'] and receipt['owned_database_removed']
        shutil.copytree(evidence.parent,destination/'evidence')
        assert databases()==['postgres','template0','template1']
        passed=True
    finally:
        if started:control('stop')
        report={'at':datetime.now(UTC).isoformat(),'passed':passed and before=={p.relative_to(ROOT).as_posix():sha(p) for p in sources},
                'source_files':before,'started_owned_cluster':started,'deployment_allowed':False,
                'outputs':{p.relative_to(destination).as_posix():sha(p) for p in destination.rglob('*') if p.is_file()}}
        (destination/'result.json').write_text(json.dumps(report,indent=2))
    return report['passed']


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inside-wsl',action='store_true');parser.add_argument('--destination',type=Path)
args=parser.parse_args()
if args.inside_wsl:
    assert args.destination and args.destination.is_dir()
    sys.exit(0 if inside(args.destination) else 1)
assert os.name=='nt','Use the local WSL launcher'
destination=ROOT/'.local/rust-migration/linux-paper-handoff'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True,exist_ok=False)
project='/mnt/'+ROOT.drive[0].lower()+'/'+ROOT.relative_to(ROOT.anchor).as_posix()
relative=destination.relative_to(ROOT).as_posix()
reference='/home/erosimcity/.local/share/mainkraft-rust-validation/python-reference/bin/python'
result=subprocess.run(['wsl','-d','Ubuntu','--',reference,project+'/scripts/test-rust-linux-paper-handoff.py','--inside-wsl','--destination',project+'/'+relative],cwd=ROOT,capture_output=True,text=True)
(destination/'launcher.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
if result.returncode:
    print(result.stdout+result.stderr);sys.exit(1)
report=json.loads((destination/'result.json').read_text());assert report['passed']
(ROOT/'.local/rust-migration/latest-linux-paper-handoff.json').write_text(json.dumps({'directory':relative,'sha256':sha(destination/'result.json')}))
print(json.dumps({'passed':True,'directory':relative,'deployment_allowed':False}))
