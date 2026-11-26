"""Original protocol cache identity; native runtime identity is recorded separately."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
names=['research.py','forecasting.py','calendar.py','providers.py']
blobs=[(ROOT/'apps/api/intelligence'/name).read_bytes() for name in names]
value={'legacy_protocol_implementation_hash':hashlib.sha256(b''.join(blobs)).hexdigest(),'original_python_sources':{name:hashlib.sha256(blob).hexdigest() for name,blob in zip(names,blobs,strict=True)},'meaning':'Original technical-v1 cache identity only; new reports record native runtime hashes separately. This never permits reopening sealed holdouts.'}
p=ROOT/'apps/backend-rust/assets/research-evaluation-identity.json';blob=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
if p.exists():assert p.read_bytes()==blob,'Frozen original cache identity changed'
else:
 with p.open('xb') as f:f.write(blob)
print(json.dumps({'original_protocol_identity':value['legacy_protocol_implementation_hash'],'original_sources':len(names)}))
