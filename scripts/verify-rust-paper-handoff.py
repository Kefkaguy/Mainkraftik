"""Independently verify a retained, two-platform Python/Rust handoff checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path): return json.loads(path.read_text(encoding='utf-8'))
def owned(relative):
    path=(ROOT/relative).resolve();path.relative_to(ROOT.resolve());return path

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path);args=parser.parse_args()
manifest=args.manifest.resolve();manifest.relative_to((ROOT/'reports/rust-migration/paper-handoff-checkpoints').resolve())
folder=manifest.parent;report=load(manifest)
assert report['passed'] and report['partial_fill_shares']==.25 and report['duplicate_orders']==0
assert not report['complete_service_rollback'] and not report['full_migration_complete'] and not report['deployment_allowed']
actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p!=manifest}
assert actual==set(report['outputs'])
for name,expected in report['outputs'].items():
    path=(folder/name).resolve();path.relative_to(folder);assert sha(path)==expected
with zipfile.ZipFile(folder/'harness-source.zip') as archive:
    assert len(archive.namelist())==len(set(archive.namelist()))
    assert set(archive.namelist())==set(report['sources'])
    for name,expected in report['sources'].items():
        assert sha(owned(name))==expected
        assert hashlib.sha256(archive.read(name)).hexdigest()==expected
for platform,relative in [('Windows','windows'),('Linux','linux/evidence')]:
    directory=folder/relative;receipt=load(directory/'result.json')
    assert receipt['passed'] and receipt['owned_database_removed'] and receipt['synthetic_only']
    assert receipt['python_to_rust_to_python'] and receipt['immutable_records_preserved']
    assert receipt['python_excludes_rust'] and receipt['rust_excludes_python']
    assert receipt['physical_submissions_per_fixture_account']==[1,1] and receipt['duplicate_submissions']==0
    assert receipt['partial_shares']==.25 and receipt['shared_durable_cycles']==2 and receipt['forecasts']==2
    assert receipt['alpaca_requests']==0 and receipt['new_accounts']==0 and not receipt['production_changes'] and not receipt['deployment_allowed']
    for name,expected in receipt['source_files'].items():assert sha(owned(name))==expected,platform+' source changed: '+name
    for name,expected in receipt['outputs'].items():assert sha(directory/name)==expected
    for label in ['rust-partial-reconcile','rust-full-reconcile','rust-excluded-by-python-lease','python-excluded-by-rust-lease']:
        text=(directory/(label+'.txt')).read_text(encoding='utf-8')
        assert sum(map(int,re.findall(r'test result: ok\. (\d+) passed;',text)))==1
linux=load(folder/'linux/result.json');assert linux['passed']
for name,expected in linux['source_files'].items():assert sha(owned(name))==expected
for name,expected in linux['outputs'].items():assert sha(folder/'linux'/name)==expected
print(json.dumps({'passed':True,'manifest':manifest.relative_to(ROOT).as_posix(),'manifest_sha256':sha(manifest),
                  'platforms':['Windows','Linux'],'partial_fill_shares':.25,'duplicate_orders':0,
                  'complete_service_rollback':False,'full_migration_complete':False,'deployment_allowed':False},indent=2))
