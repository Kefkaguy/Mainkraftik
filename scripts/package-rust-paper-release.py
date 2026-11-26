"""Create a reviewable native PAPER release and rollback packet; never deploys."""
import argparse
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary',type=Path,required=True)
parser.add_argument('--runtime-observation',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
binary=args.binary.resolve();header=binary.read_bytes()[:20]
assert header[:4]==b'\x7fELF' and header[4:6]==b'\x02\x01' and header[18:20]==b'\x3e\x00','Linux x86_64 ELF required'
runtime=json.loads(args.runtime_observation.read_text(encoding='utf-8'))
assert runtime['container']=='keftrade-intelligence-paper-1' and runtime['image_id'].startswith('sha256:')
assert runtime['status']=='running' and runtime['service_changes']==False
assert len(runtime.get('account_ids',[]))==2 and len(set(runtime['account_ids']))==2 and all(runtime['account_ids']),'Exactly two existing pinned account identities required'
destination=args.output.resolve();destination.relative_to((ROOT/'.local').resolve());destination.mkdir(parents=True,exist_ok=False)
(destination/'release').mkdir();shutil.copy2(binary,destination/'release/mainkraft')
model=ROOT/'apps/backend-rust/tests/fixtures/deployed-paper-bundle.json'
assert sha(model)=='c287fcc54b746686c2d30b3fe3c1d94bcd3bb00c037f762e3ae94fcbcf06429f'
shutil.copy2(model,destination/'release/bundle-baseline.json')
for name in ['rust-paper.Dockerfile','rust-paper.compose.yml']:shutil.copy2(ROOT/'deploy'/name,destination/name)
gates={name:False for name in ['paper_policy_parity','order_recovery','database_backup_restore','artifact_rollback','release_health_resources','python_worker_stopped']}
receipt={'version':1,'policy':'paper-learning-v1','account_ids':runtime.get('account_ids',[]),'database_target':'paper-postgres:5432/intelligence_paper',
         'binary_sha256':sha(destination/'release/mainkraft'),'bundle_sha256':sha(model),'operator_approved':False,'checks':gates}
(destination/'receipt.template.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
rollback={'service':'paper','original_image_id':runtime['image_id'],'original_image_reference':runtime['image_reference'],
          'original_command':runtime['command'],'original_compose_files':runtime['compose_files'],
          'stop_before_replacement':True,'minimum_stop_grace_seconds':130,
          'ledger_policy':'Preserve the current compatible order ledger. Never restore a pre-trade backup over a broker account that has continued trading.',
          'no_other_service_changes':True,'rehearsed_in_container':False}
(destination/'rollback.json').write_text(json.dumps(rollback,indent=2),encoding='utf-8')
docker=subprocess.run(['docker','version','--format','{{json .Server}}'],capture_output=True,text=True,timeout=30)
(destination/'container-prerequisite.txt').write_text(docker.stdout+'\n'+docker.stderr,encoding='utf-8')
report={'at':datetime.now(UTC).isoformat(),'scope':'Reviewable existing-policy PAPER candidate only; new strategy is not activated',
        'binary_sha256':sha(destination/'release/mainkraft'),'runtime_image_id':runtime['image_id'],
        'source_observation_sha256':sha(args.runtime_observation),'container_daemon_available':docker.returncode==0,
        'image_built':False,'complete_container_rollback_verified':False,'deployable':False,'operator_approved':False,
        'required_image_checks':['Verify the local base image ID equals runtime_image_id before building','Build with RUNTIME_IMAGE pointing to that reviewed original image','Check health, memory and shutdown in an isolated validation environment','Rehearse image rollback and fresh isolated DB restore','Stop the original Python worker before any approved replacement'],
        'outputs':{p.relative_to(destination).as_posix():sha(p) for p in destination.rglob('*') if p.is_file()}}
(destination/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'packet':str(destination),'binary_sha256':report['binary_sha256'],'container_daemon_available':report['container_daemon_available'],'deployable':False,'strategy_activated':False}))
