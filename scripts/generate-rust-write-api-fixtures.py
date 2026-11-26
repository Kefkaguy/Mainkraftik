"""Capture original write-model HTTP validation offline; fake DB and enqueue only."""
import json,sys
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from fastapi.testclient import TestClient
from intelligence import main
from intelligence.util import canonical
class Rows:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=None):
        if 'SELECT symbol FROM securities' in sql:return Rows([{'symbol':'MSI'},{'symbol':'AXON'}])
        if 'INSERT INTO watchlist_versions' in sql:return Rows({'id':42,'symbols':args[0].obj,'created_at':datetime(2026,11,19,tzinfo=UTC)})
        if 'SELECT 1 FROM securities' in sql:return Rows({'exists':1} if args[0] in ['MSI','AXON','SPY'] else None)
        if 'SELECT * FROM vision_models' in sql:return Rows({'id':'blocked','license_cleared':False,'license_review_reference':None,'weight_sha256':None,'runtime_verified':False} if args[0]=='blocked' else None)
        if 'SELECT count(*) AS n FROM jobs' in sql:return Rows({'n':0})
        raise AssertionError('Unexpected SQL: '+sql)
watchlists=[None,[],{}, {'symbols':None},{'symbols':[]},{'symbols':['MSI']},{'symbols':['msi','AXON','MSI']},{'symbols':['MSI'],'extra':True},
            {'symbols':[1]},{'symbols':[None]},{'symbols':'MSI'},{'symbols':['MSI']*26},{'symbols':['bad symbol']},{'symbols':['SPY']},{'symbols':['UNKNOWN']}]
jobs=[None,[],{}, {'kind':None},{'kind':1},{'kind':''},{'kind':'unsupported'},{'kind':'refresh'},{'kind':'refresh','symbol':'MSI'},
      {'kind':'refresh','symbol':''},{'kind':'refresh','symbol':3},{'kind':'refresh','symbol':None},{'kind':'refresh','model_id':3},
      {'kind':'evaluate'},{'kind':'vision'},{'kind':'evaluate','symbol':'MSI'},{'kind':'vision','symbol':'MSI','model_id':'blocked'},
      {'kind':'vision','symbol':'MSI','model_id':'unknown'},{'kind':'refresh','symbol':'UNKNOWN'},{'kind':'refresh','symbol':'bad'},
      {'kind':'refresh','extra':True},{'kind':'resolve','symbol':'SPY'}]
main.app.dependency_overrides[main.require_session]=lambda:{}
cases=[]
try:
    with patch.object(main,'connection',Connection),patch.object(main,'enqueue',return_value='00000000-0000-4000-8000-000000000042') as enqueue:
        client=TestClient(main.app)
        for method,path,bodies in [('PUT','/v1/watchlist',watchlists),('POST','/v1/jobs',jobs)]:
            for body in bodies:
                enqueue.reset_mock()
                response=client.request(method,path,content=json.dumps(body),headers={'content-type':'application/json'})
                cases.append({'method':method,'path':path,'input':body,'status':response.status_code,'response':response.json(),'enqueue':list(enqueue.call_args.args) if enqueue.called else None})
finally:main.app.dependency_overrides.clear()
path=ROOT/'apps/backend-rust/tests/fixtures/write-api-validation.json';blob=canonical({'cases':cases}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen write HTTP fixture differs'
else:path.write_bytes(blob)
print(f'Saved {len(cases)} original watchlist/job write validation and payload contracts')
