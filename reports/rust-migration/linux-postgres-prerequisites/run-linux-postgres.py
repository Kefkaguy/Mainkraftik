"""Run existing isolated harness against the owned WSL PostgreSQL cluster."""
from pathlib import Path
import hashlib,json,os,subprocess,sys
from datetime import datetime,UTC
root=Path(__file__).resolve().parents[2]
assert (root/'scripts/test-rust-postgres.py').is_file()
validation=Path.home()/'.local/share/mainkraft-rust-validation'
ptr=json.loads((validation/'postgres18/active-cluster.json').read_text());cluster_path=Path(ptr['file'])
assert hashlib.sha256(cluster_path.read_bytes()).hexdigest()==ptr['sha256']
cluster=json.loads(cluster_path.read_text());assert cluster['port']==5543 and cluster['listen_addresses']==['127.0.0.1']
destination=root/'.local/rust-migration/linux-postgres-runs'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ');destination.mkdir(parents=True,exist_ok=False)
env=dict(os.environ,CARGO_HOME=str(validation/'cargo'),RUSTUP_HOME=str(validation/'rustup'),CARGO_TARGET_DIR=str(validation/'target'),CARGO_BUILD_JOBS='2',RUSTUP_TOOLCHAIN='1.93.0',PATH=str(validation/'cargo/bin')+':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',INTELLIGENCE_DATABASE_URL='postgresql://rust_fixture@127.0.0.1:5543/intelligence',INTELLIGENCE_PAPER_DATABASE_URL='postgresql://rust_fixture@127.0.0.1:5543/intelligence_paper',INTELLIGENCE_ALPACA_KEY='fixture',INTELLIGENCE_ALPACA_SECRET='fixture')
command=[sys.executable,'scripts/test-rust-postgres.py']
with (destination/'native.txt').open('w') as log:
 result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,text=True)
versions=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True);(destination/'python-packages.txt').write_text(versions)
report={'at':datetime.now(UTC).isoformat(),'passed':result.returncode==0,'exit_code':result.returncode,'cluster':cluster,'python':sys.version,'command':command,'full_linux_validation_complete':False,'deployment_allowed':False,'outputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}}
(destination/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps({'passed':report['passed'],'exit_code':result.returncode,'directory':str(destination)}),flush=True);sys.exit(result.returncode)
