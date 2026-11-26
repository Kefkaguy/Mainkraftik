"""Offline unmodified discovery acquisition oracle; no HTTP, account, or DB access."""
import ast
import json
import re
import sys
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from paperpilot import discovery
from intelligence.util import canonical,digest

started=datetime(2026,11,19,13,tzinfo=UTC)
base={'symbol':'AAA','name':'Example operating company','tradable':True,'fractionable':True,'class':'us_equity','exchange':'NASDAQ'}
catalog=[base,{**base,'symbol':'ZZZ'},{**base,'symbol':'BAD.A'},{**base,'symbol':'ETF','name':'Example ETF'},
         {**base,'symbol':'ODD','name':'TRUST_company'},{**base,'symbol':'NO','fractionable':False},
         {**base,'symbol':'LOW','exchange':'OTC'},{**base,'symbol':'UNIC','name':'信ETF号'}]
tree=ast.parse((ROOT/'apps/api/paperpilot/discovery.py').read_text(encoding='utf-8'))
research=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='research')
valid=next(n for n in research.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='valid' for t in n.targets))
namespace={'catalog':catalog,'re':re}
exec(compile(ast.Module(body=[valid],type_ignores=[]),'original-discovery-valid','exec'),namespace)
article={'id':9,'headline':'Visible news','summary':'Summary','created_at':'2026-11-18T12:00:00Z','updated_at':'2026-11-18T12:00:00Z','symbols':['ZZZ','AAA'],'source':'fixture','url':'https://example.test/news','content':'PRIVATE omitted','author':'PRIVATE omitted'}
pages=[{'news':[article,{**article,'id':10,'created_at':'invalid'},{**article,'id':11,'updated_at':'2026-11-19T13:00:01Z'}],'next_page_token':'next'},
       {'news':[{**article,'headline':'Updated','updated_at':'2026-11-19T12:00:00Z'}, {**article,'id':'a','symbols':['AAA']},{**article,'headline':'Old'}],'next_page_token':None}]
captured={}; queries=[]
class Broker:
    def request(self,*args,**kwargs):return catalog
class Client:
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
class Captured(Exception):pass
def get(*args,**kwargs):
    queries.append(kwargs['params']);return pages[len(queries)-1]
def store(symbol,provider,payload):
    captured.update(payload=payload,id=digest([symbol,provider,payload]));raise Captured()
with patch.object(discovery,'now',return_value=started),patch.object(discovery,'PaperBroker',Broker),patch.object(discovery.httpx,'Client',Client),patch.object(discovery,'get_json',side_effect=get),patch.object(discovery,'store',side_effect=store):
    try:discovery.research(preview=True)
    except Captured:pass
data={'started':started.isoformat(),'catalog':catalog,'eligible':namespace['valid'],'pages':pages,'queries':queries,'expected':captured}
path=ROOT/'apps/backend-rust/tests/fixtures/discovery-parity.json';raw=canonical(data).encode()
# canonical sorts keys, so explicitly preserve acquired article order separately.
data['expected_article_order']=list(captured['payload']['articles'])
raw=json.dumps(json.loads(canonical(data)),separators=(',',':')).encode()
if path.exists():assert path.read_bytes()==raw
else:path.write_bytes(raw)
print('Saved original catalog and two-page acquisition/source-identity oracle')
