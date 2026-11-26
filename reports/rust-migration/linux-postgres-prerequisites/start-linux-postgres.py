"""Start only the owned WSL PostgreSQL test cluster; no system service."""
import hashlib,json,os,socket,subprocess
from pathlib import Path
from datetime import datetime,UTC
root=Path.home()/'.local/share/mainkraft-rust-validation/postgres18'
pointer=json.loads((root/'latest.json').read_text())
folder=Path(pointer['directory']); report_path=folder/'result.json'
assert hashlib.sha256(report_path.read_bytes()).hexdigest()==pointer['sha256']
report=json.loads(report_path.read_text()); runtime=Path(report['runtime'])
bin=runtime/'usr/lib/postgresql/18/bin'
assert report['signature_verified'] and report['version'].startswith('postgres (PostgreSQL) 18.')
probe=socket.socket();probe.settimeout(1);occupied=probe.connect_ex(('127.0.0.1',5543))==0;probe.close()
assert not occupied,'Refusing to replace any existing local port listener'
data=folder/'data'
env=dict(os.environ,LD_LIBRARY_PATH=str(runtime/'usr/lib/x86_64-linux-gnu'))
if not data.exists():
 result=subprocess.run([str(bin/'initdb'),'-D',str(data),'--username=rust_fixture','--auth-local=trust','--auth-host=trust','--locale=C.UTF-8','--encoding=UTF8'],env=env,capture_output=True,text=True)
 (folder/'initdb.txt').write_text(result.stdout+result.stderr);assert result.returncode==0
else:
 assert (data/'PG_VERSION').read_text().strip()=='18' and (folder/'initdb.txt').is_file() and not (data/'postmaster.pid').exists()
sockets=folder/'sockets'
with (data/'postgresql.conf').open('a') as f:
 f.write("\nlisten_addresses='127.0.0.1'\nport=5543\nmax_connections=40\nshared_buffers='32MB'\nwork_mem='4MB'\nunix_socket_directories=''\n")
result=subprocess.run([str(bin/'pg_ctl'),'-D',str(data),'-l',str(folder/'server.log'),'-w','start'],env=env,capture_output=True,text=True)
(folder/('startup-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+'.txt')).write_text(result.stdout+result.stderr);assert result.returncode==0
version=subprocess.check_output([str(bin/'psql'),'-h','127.0.0.1','-p','5543','-U','rust_fixture','-d','postgres','-Atc','SHOW server_version'],env=env,text=True).strip()
state={'at':datetime.now(UTC).isoformat(),'version':version,'data':str(data),'bin':str(bin),'runtime':str(runtime),'port':5543,'listen_addresses':['127.0.0.1'],'system_service_installed':False,'scope':'User-owned WSL validation cluster only; no application worker or broker','pg_ctl_process_owned':True}
(folder/'cluster.json').write_text(json.dumps(state,indent=2));(root/'active-cluster.json').write_text(json.dumps({'file':str(folder/'cluster.json'),'sha256':hashlib.sha256((folder/'cluster.json').read_bytes()).hexdigest()}))
print(json.dumps(state),flush=True)
