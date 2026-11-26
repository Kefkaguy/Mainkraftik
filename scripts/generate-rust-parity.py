"""Create deterministic Python-oracle fixtures. Never trains/changes a frozen model."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.portable_models import fit,predict
from intelligence.util import canonical,digest
from chart_intelligence.core import forward


def main():
    rng=np.random.default_rng(20261119)
    bundle_path=sorted((ROOT/'.local/rust-migration').glob('bundle-*.json'))
    if len(bundle_path)!=1:raise ValueError('Select an unambiguous deployed frozen bundle snapshot')
    bundle=json.loads(bundle_path[0].read_text())
    models={f'deployed_{key}':value['model'] for key,value in bundle['strategies'].items()}
    # These synthetic models test model-family compatibility only. They are
    # separate from strategy selection and cannot authorize a trading policy.
    synthetic=[]
    for _ in range(440):
        x=rng.normal(0,.02,11);x[4]=abs(x[4])+.001
        y=float(x[0]*.3-x[1]*.1+rng.normal(0,.01))
        synthetic.append({'x':x.tolist(),'y':y})
    for family,parameters in [('linear',{'alpha':10,'C':.1}),('histboost',{'iterations':12,'leaves':7,'learning_rate':.05,'l2':5,'minimum_leaf':10})]:
        models['synthetic_'+family]=fit(synthetic[:340],synthetic[340:],[0,1,2,4,7,10],family,parameters,1)
    cases=[]
    for name,artifact in models.items():
        width=max(11,max(artifact['columns'])+1)
        inputs=[]
        for index in range(160):
            row=rng.normal(0,.05,width).tolist();row[4]=float(rng.uniform(1e-7,.1))
            if index%7==0:row[0]=None
            if index%11==0:row[2]=None
            inputs.append(row)
        for magnitude in (0.,1e-9,1e3,-1e3):
            row=[magnitude]*width;row[4]=abs(magnitude);inputs.append(row)
        predictions=predict(artifact,[{'x':row} for row in inputs])
        cases.append({'name':name,'artifact':artifact,'inputs':inputs,'expected':[
            {'expected_net_return':float(m),'probability_positive':float(p),'lower_return':float(lo),'upper_return':float(hi)}
            for m,p,lo,hi in zip(*predictions)]})
    learning=[];scales=np.asarray([1.,*([.05]*11),.05,.05,.005])
    for count in (1,7,200):
        x=rng.normal(0,.04,(count,15));x[:,0]=1.;x[:,7]=abs(x[:,7])+1
        prior=rng.normal(0,.001,count);actual=prior+x[:,1]*.02+rng.normal(0,.003,count)
        z=x/scales;beta=np.linalg.solve(z.T@z+100*np.eye(15),z.T@(actual-prior))/scales
        learning.append({'rows':[{'x':row.tolist(),'prior_return':float(p),'actual_return':float(a)} for row,p,a in zip(x,prior,actual)],'expected':beta.tolist()})
    values=[None,True,False,0,1,-3,1.,-0.,1e-7,1e-6,1e-5,1e-4,1e15,1e16,1e20,{'name':'Mainkraft λ 😀','a':'\n\t\\"'}]
    values+=[float(v) for v in rng.normal(0,1,100)]
    values+=[float(v) for v in np.exp(rng.uniform(-700,700,100))]
    canonical_cases=[{'value':v,'expected':canonical(v),'digest':digest(v)} for v in values]
    canonical_cases.append({'value':{k:v for k,v in bundle.items() if k!='bundle_id'},'expected_digest':bundle['bundle_id']})
    network={'weights':[rng.normal(0,.2,(11,8)).tolist(),rng.normal(0,.2,(8,2)).tolist()],
             'biases':[rng.normal(0,.1,8).tolist(),rng.normal(0,.1,2).tolist()]}
    x=rng.normal(0,1,(20,11)).tolist()
    neural=[{'network':network,'inputs':x,'logistic':logistic,'expected':forward(network,x,logistic).tolist()} for logistic in (False,True)]
    fixture={'oracle':'Unmodified Python numeric predict, NumPy residual solve, Python canonical and network forward',
             'seed':20261119,'deployed_bundle_sha256':hashlib.sha256(bundle_path[0].read_bytes()).hexdigest(),
             'numeric':cases,'learning':learning,'canonical':canonical_cases,'neural_forward':neural}
    path=ROOT/'apps/backend-rust/tests/fixtures/python-parity.json';path.parent.mkdir(parents=True,exist_ok=True)
    blob=json.dumps(fixture,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if path.exists() and path.read_bytes()!=blob:raise ValueError('Existing parity fixture differs; version rather than overwrite')
    if not path.exists():path.write_bytes(blob)
    print(json.dumps({'path':str(path),'sha256':hashlib.sha256(blob).hexdigest(),'numeric_predictions':sum(len(c['inputs']) for c in cases),'learner_fixtures':len(learning),'canonical_fixtures':len(canonical_cases),'network_predictions':40}))


if __name__=='__main__':main()
