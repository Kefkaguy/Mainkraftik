"""Reproducible offline migration checkpoint; never accesses deployed services."""
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC,datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
instant=datetime.now(UTC)
destination=ROOT/'reports/rust-migration/checkpoints'/instant.strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True,exist_ok=False)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
checks=[]
commands=[
    ('ranking-oracle',[sys.executable,'scripts/generate-rust-ranking-fixture.py']),
    ('discovery-oracle',[sys.executable,'scripts/generate-rust-discovery-fixture.py']),
    ('research-oracle',[sys.executable,'scripts/generate-rust-research-fixture.py']),
    ('report-oracle',[sys.executable,'scripts/generate-rust-report-fixture.py']),
    ('financial-oracle',[sys.executable,'scripts/generate-rust-financial-fixture.py']),
    ('forecast-statistics-oracle',[sys.executable,'scripts/generate-rust-forecast-fixtures.py']),
    ('information-metadata-oracle',[sys.executable,'scripts/generate-rust-information-fixtures.py']),
    ('information-time-oracle',[sys.executable,'scripts/generate-rust-information-time-fixtures.py']),
    ('evidence-store-oracle',[sys.executable,'scripts/generate-rust-evidence-fixtures.py']),
    ('point-feature-oracle',[sys.executable,'scripts/generate-rust-point-fixtures.py']),
    ('prospective-legacy-identity',[sys.executable,'scripts/generate-rust-prospective-identity.py']),
    ('bootstrap-oracle',[sys.executable,'scripts/generate-rust-bootstrap-fixtures.py']),
    ('training-oracle',[sys.executable,'scripts/generate-rust-training-fixtures.py']),
    ('evaluation-oracle',[sys.executable,'scripts/generate-rust-evaluation-fixtures.py']),
    ('evaluation-legacy-identity',[sys.executable,'scripts/generate-rust-evaluation-identity.py']),
    ('python-set-oracle',[sys.executable,'scripts/generate-rust-python-set-fixtures.py','--output','apps/backend-rust/tests/fixtures/python-set-parity.json']),
    ('python-set-resize-oracle',[sys.executable,'scripts/generate-rust-python-set-fixtures.py','--extended','--output','apps/backend-rust/tests/fixtures/python-set-extended.json']),
    ('numpy-exp-oracle',[str(ROOT/'.local/rust-migration/sentiment-oracle/Scripts/python.exe'),'scripts/generate-rust-numpy-exp-fixtures.py','--output','apps/backend-rust/tests/fixtures/numpy-exp-parity.json']),
    ('sentiment-assembly-oracle',[str(ROOT/'.local/rust-migration/sentiment-oracle/Scripts/python.exe'),'scripts/generate-rust-research-sentiment-fixture.py']),
    ('active-engine-oracle',[sys.executable,'scripts/generate-rust-active-engine-fixture.py']),
    ('ir-html-robots-oracle',[sys.executable,'scripts/generate-rust-ir-fixtures.py']),
    ('provider-oracle',[sys.executable,'scripts/generate-rust-provider-fixtures.py']),
    ('monitoring-oracle',[sys.executable,'scripts/generate-rust-monitoring-fixture.py']),
    ('legacy-ledger-oracle',[sys.executable,'scripts/generate-rust-ledger-fixture.py']),
    ('write-api-oracle',[sys.executable,'scripts/generate-rust-write-api-fixtures.py']),
    ('job-oracle',[sys.executable,'scripts/generate-rust-job-fixtures.py']),
    ('chart-oracle',[sys.executable,'scripts/generate-rust-chart-fixtures.py']),
    ('font-oracle',[sys.executable,'scripts/generate-rust-font-atlas.py']),
    ('annotation-oracle',[sys.executable,'scripts/generate-rust-annotation-fixture.py']),
    ('rust-format',['cargo','fmt','--manifest-path','apps/backend-rust/Cargo.toml','--all','--','--check']),
    ('rust-tests',['cargo','test','--locked','--manifest-path','apps/backend-rust/Cargo.toml','--workspace','--all-features']),
    ('rust-clippy',['cargo','clippy','--locked','--manifest-path','apps/backend-rust/Cargo.toml','--workspace','--all-features','--all-targets','--','-D','warnings']),
    ('isolated-postgres',[sys.executable,'scripts/test-rust-postgres.py']),
    ('native-backup-crossread',[sys.executable,'scripts/test-rust-backup.py']),
    ('native-sentiment',[sys.executable,'scripts/test-rust-sentiment.py']),
    ('native-ir-http',[sys.executable,'scripts/test-rust-ir-http.py']),
    ('native-evidence',[sys.executable,'scripts/test-rust-evidence.py']),
    ('linux-native-core',[sys.executable,'scripts/test-rust-linux.py']),
    ('linux-isolated-postgres',[sys.executable,'scripts/test-rust-linux-postgres.py']),
    ('linux-native-sentiment',[sys.executable,'scripts/test-rust-linux-sentiment.py']),
    ('phase7-records',['cargo','run','--locked','--quiet','--manifest-path','apps/backend-rust/Cargo.toml','--','verify-evidence','--root','.local/phase7']),
    ('chart-records',['cargo','run','--locked','--quiet','--manifest-path','apps/backend-rust/Cargo.toml','--','verify-evidence','--root','.local/chart-intelligence']),
    ('phase8-compressed-records',['cargo','run','--locked','--quiet','--manifest-path','apps/backend-rust/Cargo.toml','--','verify-evidence','--root','.local/phase8','--compressed']),
    ('lab-records',['cargo','run','--locked','--quiet','--manifest-path','apps/backend-rust/Cargo.toml','--','verify-evidence','--root','.local/strategy-lab']),
]
def source_hashes():
    paths=[]
    for directory,children,names in os.walk(ROOT/'apps/backend-rust'):
        children[:]=[name for name in children if name!='target']
        paths.extend(Path(directory)/name for name in names)
    paths.extend(ROOT/'scripts'/name for name in ['checkpoint-rust-migration.py','test-rust-postgres.py','test-rust-sentiment.py','generate-rust-ranking-fixture.py','generate-rust-discovery-fixture.py','generate-rust-research-fixture.py','generate-rust-sentiment-inference-fixture.py','generate-rust-report-fixture.py','generate-rust-financial-fixture.py','generate-rust-monitoring-fixture.py','generate-rust-ledger-fixture.py','prepare-rust-research-api.py','generate-rust-write-api-fixtures.py','generate-rust-job-fixtures.py','generate-rust-chart-fixtures.py','generate-rust-font-atlas.py','generate-rust-annotation-fixture.py','generate-rust-provider-fixtures.py','prepare-rust-ingestion.py','prepare-rust-forecasting.py','generate-rust-forecast-fixtures.py','generate-rust-information-fixtures.py','prepare-rust-information.py','generate-rust-ir-fixtures.py','prepare-rust-official-ir.py'])
    files={path.relative_to(ROOT).as_posix():sha(path) for path in sorted(paths)}
    files['scripts/test-rust-ir-http.py']=sha(ROOT/'scripts/test-rust-ir-http.py')
    files['scripts/generate-rust-information-time-fixtures.py']=sha(ROOT/'scripts/generate-rust-information-time-fixtures.py')
    files['scripts/prepare-rust-dispatch.py']=sha(ROOT/'scripts/prepare-rust-dispatch.py')
    files['scripts/generate-rust-evidence-fixtures.py']=sha(ROOT/'scripts/generate-rust-evidence-fixtures.py')
    files['scripts/test-rust-evidence.py']=sha(ROOT/'scripts/test-rust-evidence.py')
    files['scripts/generate-rust-point-fixtures.py']=sha(ROOT/'scripts/generate-rust-point-fixtures.py')
    files['scripts/prepare-rust-point-features.py']=sha(ROOT/'scripts/prepare-rust-point-features.py')
    files['scripts/generate-rust-prospective-identity.py']=sha(ROOT/'scripts/generate-rust-prospective-identity.py')
    files['scripts/prepare-rust-prospective.py']=sha(ROOT/'scripts/prepare-rust-prospective.py')
    files['scripts/setup-rust-linux-validation.sh']=sha(ROOT/'scripts/setup-rust-linux-validation.sh')
    files['scripts/test-rust-linux.sh']=sha(ROOT/'scripts/test-rust-linux.sh')
    files['scripts/test-rust-linux.py']=sha(ROOT/'scripts/test-rust-linux.py')
    files['scripts/generate-rust-bootstrap-fixtures.py']=sha(ROOT/'scripts/generate-rust-bootstrap-fixtures.py')
    files['scripts/prepare-rust-verdict.py']=sha(ROOT/'scripts/prepare-rust-verdict.py')
    files['scripts/test-rust-backup.py']=sha(ROOT/'scripts/test-rust-backup.py')
    files['scripts/test-rust-linux-postgres.py']=sha(ROOT/'scripts/test-rust-linux-postgres.py')
    files['scripts/test-rust-linux-sentiment.py']=sha(ROOT/'scripts/test-rust-linux-sentiment.py')
    files['scripts/generate-rust-training-fixtures.py']=sha(ROOT/'scripts/generate-rust-training-fixtures.py')
    files['scripts/generate-rust-evaluation-fixtures.py']=sha(ROOT/'scripts/generate-rust-evaluation-fixtures.py')
    files['scripts/generate-rust-evaluation-identity.py']=sha(ROOT/'scripts/generate-rust-evaluation-identity.py')
    files['scripts/prepare-rust-evaluation.py']=sha(ROOT/'scripts/prepare-rust-evaluation.py')
    files['scripts/prepare-rust-audit.py']=sha(ROOT/'scripts/prepare-rust-audit.py')
    files['scripts/verify-rust-checkpoint.py']=sha(ROOT/'scripts/verify-rust-checkpoint.py')
    files['scripts/generate-rust-python-set-fixtures.py']=sha(ROOT/'scripts/generate-rust-python-set-fixtures.py')
    for name in ['generate-rust-numpy-exp-fixtures.py','generate-rust-research-sentiment-fixture.py','generate-rust-active-engine-fixture.py']:
        files['scripts/'+name]=sha(ROOT/'scripts'/name)
    return files

files=source_hashes()
with zipfile.ZipFile(destination/'tested-source.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
    for relative,expected in sorted(files.items()):
        source=(ROOT/relative).resolve()
        source.relative_to(ROOT.resolve())
        blob=source.read_bytes()
        assert hashlib.sha256(blob).hexdigest()==expected,'Source changed while snapshotting: '+relative
        info=zipfile.ZipInfo(relative,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,blob)

for name,command in commands:
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (destination/f'{name}.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    checks.append({'name':name,'command':command,'exit_code':result.returncode})
    print(name,'PASS' if result.returncode==0 else 'FAIL',flush=True)
    if name=='native-backup-crossread' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-backup-report.json').read_text(encoding='utf-8'))
        fixture=(ROOT/reference['file']).parent
        assert sha(fixture/'report.json')==reference['sha256']
        # Synthetic archives and logs only. No database URIs or credentials are saved.
        archive_dir=destination/'backup-crossread'
        archive_dir.mkdir()
        for source in sorted(fixture.iterdir()):
            if source.is_file():
                (archive_dir/source.name).write_bytes(source.read_bytes())
    if name=='isolated-postgres' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-sentiment-assembly.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes();assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'native-sentiment-assembly.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-audit-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-audit-oracle.json').write_bytes(blob)
        for label,extra in reference.get('auxiliary',{}).items():
            blob=(ROOT/extra['file']).read_bytes()
            assert hashlib.sha256(blob).hexdigest()==extra['sha256']
            (destination/('original-audit-'+label+'-oracle.json')).write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-evaluation-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-evaluation-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-verdict-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-verdict-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-prospective-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-prospective-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-point-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-point-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-dispatch-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-dispatch-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-research-api-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-research-api-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-ingestion-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-ingestion-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-forecast-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-forecast-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-information-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-information-oracle.json').write_bytes(blob)
        reference=json.loads((ROOT/'.local/rust-migration/latest-ir-oracle.json').read_text(encoding='utf-8'))
        blob=(ROOT/reference['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest()==reference['sha256']
        (destination/'original-ir-oracle.json').write_bytes(blob)
    if name=='native-ir-http' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-ir-http.json').read_text(encoding='utf-8'))
        fixture=ROOT/reference['directory']
        # Only public CA, oracle, logs and captured requests. Never copy private TLS keys.
        for filename in ['oracle.json','result.json','native.txt','ca.pem']:
            (destination/('ir-http-'+filename)).write_bytes((fixture/filename).read_bytes())
    if name=='native-evidence' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-evidence-crossread.json').read_text(encoding='utf-8'))
        fixture=ROOT/reference['directory']
        for filename in ['native.txt','result.json']:
            (destination/('evidence-crossread-'+filename)).write_bytes((fixture/filename).read_bytes())
    if name=='linux-native-core' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-linux-core.json').read_text(encoding='utf-8'))
        fixture=ROOT/reference['directory']
        assert sha(fixture/'result.json')==reference['sha256']
        for source in sorted(fixture.iterdir()):
            if source.is_file():
                (destination/('linux-core-'+source.name)).write_bytes(source.read_bytes())
    if name=='linux-isolated-postgres' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-linux-postgres.json').read_text(encoding='utf-8'))
        fixture=ROOT/reference['directory']
        assert sha(fixture/'result.json')==reference['sha256']
        assert json.loads((fixture/'result.json').read_text())['passed']
        import shutil
        shutil.copytree(fixture,destination/'linux-postgres')
    if name=='linux-native-sentiment' and result.returncode==0:
        reference=json.loads((ROOT/'.local/rust-migration/latest-linux-sentiment.json').read_text(encoding='utf-8'))
        fixture=ROOT/reference['directory']
        assert sha(fixture/'result.json')==reference['sha256']
        assert json.loads((fixture/'result.json').read_text())['passed']
        import shutil
        shutil.copytree(fixture,destination/'linux-sentiment')
    if result.returncode:break

frozen=json.loads((ROOT/'reports/phase8/phase7-freeze.json').read_text(encoding='utf-8'))['files']
research=json.loads((ROOT/'reports/rust-migration/research-preservation.json').read_text(encoding='utf-8'))
compressed=json.loads((ROOT/'reports/rust-migration/phase8-compressed-preservation.json').read_text(encoding='utf-8'))
audit=json.loads((ROOT/'reports/rust-migration/source-audit.json').read_text(encoding='utf-8'))
original={entry['path']:entry['sha256'] for entry in audit['modules'] if entry['path'].startswith(('apps/api/','workers/'))}
preservation=[]
for name,manifest in [('phase7-frozen',frozen),('phase7-phase8-research',research),('phase8-compressed-frozen',compressed),('original-python',original)]:
    differences=[path for path,expected in manifest.items() if not (ROOT/path).exists() or sha(ROOT/path)!=expected]
    preservation.append({'name':name,'files':len(manifest),'differences':differences})
    print(name,'PASS' if not differences else 'FAIL',flush=True)

current_files=source_hashes()
source_changes=[p for p in sorted(set(files)|set(current_files)) if files.get(p)!=current_files.get(p)]
preservation.append({'name':'tested-source-identity','files':len(files),'differences':source_changes})
print('tested-source-identity','PASS' if not source_changes else 'FAIL',flush=True)
passed=len(checks)==len(commands) and all(c['exit_code']==0 for c in checks) and all(not item['differences'] for item in preservation)
outputs={path.relative_to(destination).as_posix():sha(path) for path in destination.rglob('*') if path.is_file()}
report={'at':instant.isoformat(),'checkpoint_passed':passed,'full_migration_complete':False,'deployment_allowed':False,
        'scope':'Local component parity; no deployed services or broker endpoints accessed. No profitability conclusion.',
        'checks':checks,'preservation':preservation,'files':files,'outputs':outputs,
        'remaining':['remaining service composition and deployed runtime qualification','remaining API wire/error contracts and research/training workflows','complete artifact backup/restore and service rollback rehearsal','full-service benchmarks, deployment environment compatibility and complete runtime validation','separate horizon/turnover investigations after parity','verified complete production cutover']}
(destination/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(destination,flush=True)
sys.exit(0 if passed else 1)
