"""Freeze original NumPy integer streams and unchanged paired-date bootstrap."""
import hashlib,json,sys
from datetime import date,timedelta
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.research import paired_block_interval
from intelligence.util import canonical
streams=[]
for bound in [1,2,3,107,2**16+1,2**31+1,2**32-1,2**32,2**32+1,2**63-1,2**63+1,2**64-1]:
 rng=np.random.default_rng(20261112)
 values=rng.integers(0,bound,size=1003,dtype=np.uint64).tolist()
 streams.append({'bound':bound,'values':values,'next_raw':int(rng.bit_generator.random_raw())})
rng=np.random.default_rng(42);mixed=[]
for kind,n,bound in [('integers',3,107),('normal',5,None),('raw',4,None),('integers',13,2**32+1),('integers',5,3),('normal',2,None),('integers',1,1),('raw',1,None),('integers',1003,2**63+1)]:
 values=rng.integers(0,bound,size=n,dtype=np.uint64).tolist() if kind=='integers' else rng.standard_normal(n).tolist() if kind=='normal' else rng.bit_generator.random_raw(n).tolist()
 mixed.append({'kind':kind,'n':n,'bound':bound,'values':values})
intervals=[]
for n,block,samples,confidence in [(0,20,2000,.9875),(1,20,2000,.9875),(2,20,2000,.9875),(5,2,2000,.9875),(20,20,2000,.9875),(21,20,2000,.9875),(126,20,2000,.9875),(127,20,2000,.9875),(129,7,2000,.9875),(300,20,2000,.9875),(257,31,501,.9),(126,20,2000,.5)]:
 dates=[];deltas=[]
 for i in range(n):
  for j in range(2):dates.append(date(2025,1,1)+timedelta(days=i));deltas.append(((i%17)-8)/1024+(j-0.5)/4096)
 dates.reverse();deltas.reverse()
 intervals.append({'dates':dates,'differences':deltas,'samples':samples,'block':block,'confidence':confidence,'expected':paired_block_interval(dates,deltas,samples=samples,block=block,confidence=confidence)})
fixture={'numpy':np.__version__,'integer_streams':streams,'mixed':mixed,'intervals':intervals,'source_sha256':hashlib.sha256((ROOT/'apps/api/intelligence/research.py').read_bytes()).hexdigest()}
p=ROOT/'apps/backend-rust/tests/fixtures/bootstrap-parity.json';blob=canonical(fixture).encode()
if p.exists():assert p.read_bytes()==blob,'Frozen bootstrap oracle differs'
else:
 with p.open('xb') as f:f.write(blob)
print(json.dumps({'integer_streams':len(streams),'mixed_operations':len(mixed),'bootstrap_cases':len(intervals)}))
