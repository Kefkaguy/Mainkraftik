"""Pin the unchanged legacy prospective implementation identity; no runtime Python."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
names=['tuning.py','point_features.py','portable_models.py','prospective.py','information.py']
files=[ROOT/'apps/api/intelligence'/name for name in names]
identity=hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()
audit=json.loads((ROOT/'reports/rust-migration/source-audit.json').read_text())
expected={row['path']:row['sha256'] for row in audit['modules']}
for p in files:assert hashlib.sha256(p.read_bytes()).hexdigest()==expected[p.relative_to(ROOT).as_posix()]
path=ROOT/'apps/backend-rust/assets/legacy-information-implementation.sha256'
blob=(identity+'\n').encode('ascii')
if path.exists():assert path.read_bytes()==blob,'Frozen legacy prospective implementation identity changed'
else:
    with path.open('xb') as f:f.write(blob)
print(json.dumps({'legacy_implementation_hash':identity,'original_source_files':len(files),'runtime_python':False}))
