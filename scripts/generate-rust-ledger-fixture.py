"""Read-only legacy research-progress semantics; synthetic rows, no enrollment or writes."""
import json,sys
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import prospective
from intelligence.util import canonical
protocol={'start_session':date(2026,1,1),'specification':{'required_dates':126}}
base=[{'origin_session':date(2026,1,1)+timedelta(days=i),'realized_return':0.01,'baseline_truth':0.01} for i in range(127) for _ in range(4)]
missing=[dict(r) for r in base[:504]];missing[-1]['realized_return']=None
cases=[(None,None,[]),(protocol,{'status':'failed','report':{'status':'failed','promotion':'none'}},[]),(protocol,None,base[:3]),(protocol,None,base[:4]),(protocol,None,base),(protocol,None,missing),(protocol,None,base[:504]+[dict(base[0])])]
output=[]
class Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=None):
        if 'shadow_protocols' in sql:return Result(current[0])
        if 'shadow_verdicts' in sql:return Result(current[1])
        if 'SELECT f.origin_session' in sql:return Result(current[2])
        assert sql=='SELECT pg_advisory_xact_lock(753194044)';return Result(None)
for current in cases:
    with patch.object(prospective,'connection',Connection):expected=prospective.progress_and_verdict()
    output.append({'protocol':current[0],'existing':current[1],'rows':current[2],'expected':expected})
path=ROOT/'apps/backend-rust/tests/fixtures/legacy-progress-parity.json';blob=canonical({'cases':output}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen legacy ledger fixture differs'
else:path.write_bytes(blob)
print('Saved seven read-only original legacy-ledger progress cases; no enrollment/model promotion')
