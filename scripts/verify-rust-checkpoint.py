"""Independently recheck a completed local checkpoint, its archive and preservation receipts."""
import argparse,hashlib,json,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def owned(relative):
 path=(ROOT/relative).resolve();path.relative_to(ROOT.resolve());return path
def load(path):return json.loads(path.read_text(encoding='utf-8'))
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path);args=parser.parse_args()
path=args.manifest.resolve();path.relative_to((ROOT/'reports/rust-migration/checkpoints').resolve())
manifest=load(path);folder=path.parent
assert manifest['checkpoint_passed'] and not manifest['full_migration_complete'] and not manifest['deployment_allowed']
assert all(c['exit_code']==0 for c in manifest['checks'])
assert all(not p['differences'] for p in manifest['preservation'])
with zipfile.ZipFile(folder/'tested-source.zip') as archive:
 assert len(archive.namelist())==len(set(archive.namelist()))
 assert set(archive.namelist())==set(manifest['files'])
 for name,expected in manifest['files'].items():
  assert sha(owned(name))==expected,'Current source changed: '+name
  assert hashlib.sha256(archive.read(name)).hexdigest()==expected,'Archived source changed: '+name
actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p!=path}
assert actual==set(manifest['outputs']),'Checkpoint outputs added or missing'
for name,expected in manifest['outputs'].items():
 output=(folder/name).resolve();output.relative_to(folder.resolve())
 assert sha(output)==expected,'Checkpoint output changed: '+name
audit=load(ROOT/'reports/rust-migration/source-audit.json')
groups={
 'phase7':load(ROOT/'reports/phase8/phase7-freeze.json')['files'],
 'research':load(ROOT/'reports/rust-migration/research-preservation.json'),
 'phase8_compressed':load(ROOT/'reports/rust-migration/phase8-compressed-preservation.json'),
 'original_python':{m['path']:m['sha256'] for m in audit['modules'] if m['path'].startswith(('apps/api/','workers/'))},
}
for group,files in groups.items():
 for name,expected in files.items():assert sha(owned(name))==expected,group+' changed: '+name
counts={}
for name in ['rust-tests','isolated-postgres','native-backup-crossread','native-sentiment','native-ir-http','native-evidence','linux-core-native']:
 log=folder/(name+'.txt')
 assert log.is_file(),name+' log missing'
 count=sum(map(int,re.findall(r'test result: ok\. (\d+) passed;',log.read_text(encoding='utf-8'))))
 assert count>0,name+' contains no completed tests'
 counts[name]=count
linux=load(folder/'linux-postgres/result.json')
assert linux['passed'] and linux['source_unchanged']
assert all(c['exit_code']==0 and c['passed_tests']==c['required_tests'] for c in linux['checks'])
sentiment=load(folder/'linux-sentiment/result.json');assert sentiment['passed'] and sentiment['tests_passed']==2
print(json.dumps({'passed':True,'checkpoint':path.relative_to(ROOT).as_posix(),'checkpoint_sha256':sha(path),'verifier_sha256':sha(Path(__file__)),'source_files_and_zip_members':len(manifest['files']),'output_files':len(manifest['outputs']),'counts':counts,'linux_database_checks':linux['checks'],'linux_native_sentiment_tests':sentiment['tests_passed'],'preservation':{k:len(v) for k,v in groups.items()},'full_migration_complete':False,'deployment_allowed':False},indent=2))
