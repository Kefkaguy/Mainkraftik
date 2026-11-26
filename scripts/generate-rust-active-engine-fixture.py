"""Original active market/inference/residual learner on synthetic candles and matured marks."""
import argparse,hashlib,json,platform,sys
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from paperpilot import experiment
from intelligence.util import canonical,digest
parser=argparse.ArgumentParser();parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows');args=parser.parse_args();assert platform.system().lower()==args.reference_platform
base=json.loads((ROOT/'apps/backend-rust/tests/fixtures/market-parity.json').read_text())
model=ROOT/'apps/backend-rust/tests/fixtures/deployed-paper-bundle.json';bundle=json.loads(model.read_bytes())
instant=datetime.fromisoformat(base['instant']);midpoint=round((base['quotes']['AMD']['bp']+base['quotes']['AMD']['ap'])/2,2)
market=experiment.Market();market.models=bundle;market.daily_inputs=lambda symbol,unused:base['normalized'][symbol]
def quote(symbol):
 if symbol=='AMD':bid,ask=midpoint-.01,midpoint+.01
 else:bid,ask=base['quotes'][symbol]['bp'],base['quotes'][symbol]['ap']
 return {'bid':bid,'ask':ask,'source_time':instant,'acquired_at':instant,'feed':'iex'}
market.quote=quote;sources=[]
def record(account,kind,payload,c=None):sources.append({'account':account,'kind':kind,'payload':payload});return digest([account,kind,payload])
with patch.object(experiment,'record',record):observations=market.observations(['AMD'],SimpleNamespace(request=lambda *a,**k:base['asset']),instant)
assert len(observations)==1;row=observations[0];target_midpoint=(round(midpoint*100)+100)/100
outcome={'prediction_id':'synthetic-prediction','x':row['x'],'prior_return':row['prior_return'],'actual_return':target_midpoint/row['midpoint']-1}
class Cursor:
 def __init__(self,value):self.value=value
 def fetchall(self):return self.value
 def fetchone(self):return self.value
class Connection:
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def execute(self,query,values):
  if "kind='prediction'" in query:return Cursor([])
  if "kind='outcome'" in query:return Cursor([{'payload':outcome}])
  if "kind='model_update'" in query:return Cursor(None)
  raise AssertionError('Unexpected original learner query')
engine=experiment.Engine.__new__(experiment.Engine)
with patch.object(experiment,'connect',Connection),patch.object(experiment,'record',record):beta=engine.resolve_and_learn({'id':'adaptive','policy':'adaptive'},[],instant+timedelta(hours=1))
fixture={'synthetic_only':True,'reference_platform':args.reference_platform,'bundle_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'bundle_id':bundle['bundle_id'],'instant':instant,'initial_midpoint':midpoint,'target_midpoint':target_midpoint,'raw_bars':base['raw_bars'],'quotes':base['quotes'],'asset':base['asset'],'expected_observation':row,'expected_beta':beta,'expected_actual_return':outcome['actual_return'],'original_source_sha256':hashlib.sha256((ROOT/'apps/api/paperpilot/experiment.py').read_bytes()).hexdigest()}
suffix='-linux' if args.reference_platform=='linux' else '';path=ROOT/f'apps/backend-rust/tests/fixtures/active-engine-parity{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Original active-engine oracle differs; never overwrite'
else:
 with path.open('xb') as f:f.write(blob)
print(json.dumps({'original_market_and_learner':True,'matured_outcomes':1,'broker_requests':0,'model_retrained':False,'output':path.as_posix()}))
