"""Original fixed historical protocol on synthetic candles; no market holdout is read."""
import argparse,copy,hashlib,json,platform,sys
from pathlib import Path
from datetime import date
import numpy as np
from threadpoolctl import threadpool_limits
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import schedule
from intelligence.research import build_panel,evaluate
from intelligence.util import canonical
parser=argparse.ArgumentParser();parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows');args=parser.parse_args();assert platform.system().lower()==args.reference_platform
days=list(schedule(date(2017,1,1),date(2024,1,1)))[:900]
def candles(market):
 rows=[]
 for i,day in enumerate(days):
  opening=80+i/64+((i*3)%7)/512
  closing=opening+(((i*(17 if market else 13))%23)-11)/128
  rows.append({'session_date':day,'feed':'sip','adjustment':'split','adjustment_asof':date(2026,11,20),'open':opening,'high':max(opening,closing)+1,'low':min(opening,closing)-1,'close':closing,'volume':1000000+(i%13)*1000})
 return rows
bars=candles(False);spy=candles(True);cases=[]
with threadpool_limits(limits=1):
 for name,n in [('short',400),('minimum',460),('inconclusive',500),('complete',900),('short-spy',900),('zero-volume',900),('gap',500),('mixed-feed',500),('single-class',500)]:
  b=copy.deepcopy(bars[:n]);s=copy.deepcopy(spy[:n])
  if name=='short-spy':s=s[73:]
  if name=='zero-volume':
   for r in b[150:180]:r['volume']=0
  if name=='gap':b.pop(100)
  if name=='mixed-feed':b[100]['feed']='iex'
  if name=='single-class':
   for r in b:r['close']=r['open']
  try:
   panel=build_panel(b,s);expected=evaluate(b,s,'MSI')
   value={'panel':[{'date':r['date'],'index':r['index'],'x':r['x']} for r in panel],'report':expected}
  except ValueError as e:value={'error':str(e)}
  cases.append({'name':name,'bars':b,'spy':s,'expected':value})
fixture={'source_sha256':hashlib.sha256((ROOT/'apps/api/intelligence/research.py').read_bytes()).hexdigest(),'reference_platform':args.reference_platform,'synthetic_only':True,'cases':cases}
suffix='-linux' if args.reference_platform=='linux' else ''
path=ROOT/f'apps/backend-rust/tests/fixtures/research-evaluation{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Original evaluation oracle differs; do not overwrite'
else:
 with path.open('xb') as f:f.write(blob)
print(json.dumps({'cases':len(cases),'market_data_used':False,'protected_evaluation_data_used':False,'model_promoted':False}))
