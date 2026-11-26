"""Original Ridge/Newton-logistic training on synthetic data only; no market evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np
from threadpoolctl import threadpool_limits
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.research import fit_heads,predict_heads
from intelligence.util import canonical

parser=argparse.ArgumentParser()
parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows')
args=parser.parse_args();assert platform.system().lower()==args.reference_platform
cases=[]
specs=[('minimum',20,1),('price',64,7),('technical',257,10),('market',1024,14),('information',180,36),('collinear',128,10),('tiny-scale',83,4),('imbalanced',257,7),('constant',57,3),('large-offset',96,5)]
with threadpool_limits(limits=1):
 for name,n,width in specs:
    x=np.asarray([[(((i+1)*(j+3)*17)%113-56)/32 for j in range(width)] for i in range(n)],dtype=float)
    y=np.asarray([((i*7)%19-9)/1024+x[i,0]/2048 for i in range(n)])
    if name=='collinear':x[:,0]=7;x[:,1]=x[:,2];x[:,3]=x[:,2]*2
    if name=='tiny-scale':x[:,0]*=2**-50;x[:,1]*=2**-35;x[:,3]=1
    if name=='imbalanced':y=np.full(n,-1/1024);y[0]=1/512
    if name=='constant':x[:]=7;y=np.asarray([1/512 if i<20 else -1/1024 for i in range(n)])
    if name=='large-offset':x[:,0]+=2**35;x[:,1]*=2**-30;x[:,2]+=2**45;x[:,3]=0
    evaluation=np.asarray([[(((i+101)*(j+3)*17)%127-63)/32 for j in range(width)] for i in range(37)],dtype=float)
    if name=='large-offset':evaluation[:,0]+=2**35;evaluation[:,1]*=2**-30;evaluation[:,2]+=2**45;evaluation[:,3]=0
    if name=='tiny-scale':evaluation[:,0]*=2**-50;evaluation[:,1]*=2**-35;evaluation[:,3]=1
    model=fit_heads(x,y);returns,probability=predict_heads(model,evaluation)
    train_return,train_probability=predict_heads(model,x)
    assert all(np.isfinite(v).all() for v in model.values())
    cases.append({'name':name,'x':x.tolist(),'y':y.tolist(),'evaluation':evaluation.tolist(),'parameters':{k:v.tolist() for k,v in model.items()},'returns':returns.tolist(),'probability':probability.tolist(),'train_returns':train_return.tolist(),'train_probability':train_probability.tolist()})
errors=[]
for labels in [[0.]*20,[1.]*20,[-1.]*20]:
 try:fit_heads(np.ones((20,3)),np.asarray(labels));raise AssertionError('Single-class fitting must fail')
 except ValueError as e:errors.append({'x':[[1.]*3]*20,'y':labels,'error':str(e)})
fixture={'numpy':np.__version__,'reference_platform':args.reference_platform,'source_sha256':hashlib.sha256((ROOT/'apps/api/intelligence/research.py').read_bytes()).hexdigest(),'synthetic_only':True,'cases':cases,'errors':errors}
suffix='-linux' if args.reference_platform=='linux' else ''
path=ROOT/f'apps/backend-rust/tests/fixtures/research-training{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Original training oracle changed; do not overwrite'
else:
 with path.open('xb') as f:f.write(blob)
print(json.dumps({'cases':len(cases),'single_class_refusals':len(errors),'training_rows':sum(len(c['y']) for c in cases),'market_data_used':False,'model_promoted':False}))
