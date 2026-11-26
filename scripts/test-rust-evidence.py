"""Original Python readers verify new native files and journal, never frozen roots."""
import hashlib,json,os,subprocess,sys,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from chart_intelligence import store,phase8_evidence,controller
from paperpilot import lab_registry
directory=ROOT/'.local/rust-migration/evidence-crossread'/str(uuid.uuid4());directory.mkdir(parents=True,exist_ok=False)
env=dict(os.environ);env['RUST_NATIVE_EVIDENCE_OUTPUT']=str(directory/'native')
result=subprocess.run(['cargo','test','--manifest-path','apps/backend-rust/Cargo.toml','--test','evidence_storage','--','--nocapture'],cwd=ROOT,env=env,capture_output=True,text=True)
(directory/'native.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8');print(result.stdout);print(result.stderr)
if result.returncode:sys.exit(result.returncode)
fixture=ROOT/'apps/backend-rust/tests/fixtures/evidence-storage.json';f=json.loads(fixture.read_text(encoding='utf-8'));results=[]
for i,c in enumerate(f['cases']):
    root=directory/'native'/str(i);family=c['family']
    with patch.object(lab_registry,'LAB',root):
        read=store.read(c['id'],root) if family=='chart' else lab_registry.read(c['id']) if family=='lab' else phase8_evidence.read_compressed(c['id'],root)
        assert read==c['read'],(i,'native file differs through original reader')
        if family!='compressed':
            try:latest={'latest':store.latest(root) if family=='chart' else lab_registry.latest()}
            except Exception as e:latest={'latest_error':{'kind':type(e).__name__,'message':str(e)}}
            assert latest.get('latest')==c.get('latest') and latest.get('latest_error')==c.get('latest_error'),i
    results.append({'case':i,'family':family,'id':c['id'],'python_read':True})
db=controller.connection(directory/'native/journal')
rows=[list(row) for row in db.execute('SELECT key,kind,payload FROM evidence ORDER BY rowid')]
assert rows[:2]==f['journal_rows'] and rows[-1][0]=='after-lock'
before=rows[0][2];controller.append(db,'forecast/one','prediction',{'do_not_overwrite':True});db.commit();assert db.execute("SELECT payload FROM evidence WHERE key='forecast/one'").fetchone()[0]==before
try:db.execute('DELETE FROM evidence')
except Exception as e:assert str(e)=='Research evidence is append-only'
else:raise AssertionError('Native append-only trigger missing')
db.rollback();db.close()
report={'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'native_exit_code':result.returncode,'python_read_native_artifacts':results,'journal_prefix_and_duplicate_prevention':True,'journal_trigger':True,'directory_sync_supported_on_host':os.name=='posix'}
(directory/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8');(ROOT/'.local/rust-migration/latest-evidence-crossread.json').write_text(json.dumps({'directory':directory.relative_to(ROOT).as_posix()}),encoding='utf-8')
print(json.dumps({'native_files_crossread_by_python':len(results),'journal_compatible':True,'result':str(directory/'result.json')}))
