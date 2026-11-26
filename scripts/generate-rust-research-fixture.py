"""Execute unmodified Python premarket discovery/price refresh offline as oracle."""
import json
import sys
from contextlib import ExitStack
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import schedule,EASTERN
from intelligence.util import canonical,digest
from paperpilot import discovery,data

instant=datetime(2026,11,19,13,tzinfo=UTC)
session=instant.astimezone(EASTERN).date()
sessions=list(schedule(session-timedelta(days=55),session-timedelta(days=1)))[-30:]
raw={}
for symbol,price in [('AAA',50.0),('BBB',25.0),('LOW',4.0),('SPY',500.0),('XLK',200.0)]:
    raw[symbol]=[{'t':datetime.combine(day,datetime.min.time(),EASTERN).astimezone(UTC).isoformat(),'o':price,'h':price+1,'l':price-1,'c':price,'v':1_000_000.0} for day in sessions]
catalog=[{'symbol':s,'name':s+' company','tradable':True,'fractionable':True,'class':'us_equity','exchange':'NASDAQ'} for s in ['AAA','BBB','LOW']]
articles=[]
for i,symbol in enumerate(['AAA','BBB','AAA','LOW','BBB','AAA']):
    articles.append({'id':str(i),'headline':symbol+' sourced headline','summary':'Visible news','created_at':'2026-11-18T12:00:00Z','updated_at':'2026-11-18T12:00:00Z','symbols':[symbol],'source':'fixture','url':'https://example.test/news','content':'MUST_NOT_ESCAPE'})
page={'news':articles,'next_page_token':None}
completion={'id':'chatcmpl-research_fixture','usage':{'prompt_tokens':100,'completion_tokens':60,'total_tokens':160},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'picks':[{'symbol':'AAA','source_ids':['0'],'stance':'bullish','rationale':'AAA sourced headline'},{'symbol':'BBB','source_ids':['1'],'stance':'mixed','rationale':'BBB sourced headline'}]})}}]}
sources=[];events=[];inserts=[];bar_queries=[];ranking_requests=[]
class Broker:
    def request(self,*args,**kwargs):return catalog
class Response:
    status_code=200
    def json(self):return completion
class Client:
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def post(self,url,headers,json):ranking_requests.append(json);return Response()
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,query,values):
        inserts.append({'table':'pilot_research_runs' if 'pilot_research_runs' in query else 'pilot_selections','values':[v.obj if hasattr(v,'obj') else v for v in values]})
def store(symbol,provider,payload,identity_payload=None):
    identity=digest([symbol,provider,identity_payload if identity_payload is not None else payload])
    sources.append({'id':identity,'symbol':symbol,'provider':provider,'payload':payload});return identity
def bars(symbol,start,end,asof):
    bar_queries.append({'symbol':symbol,'start':start,'end':end,'asof':asof})
    return raw[symbol],{},f'https://data.alpaca.markets/v2/stocks/{symbol}/bars'
def event(kind,payload):events.append([kind,payload])
original_rank=discovery.rank
with ExitStack() as stack:
    for module,name,value in [(discovery,'now',lambda:instant),(data,'now',lambda:instant),(discovery,'PaperBroker',Broker),
         (discovery,'selection',lambda _:None),(discovery,'connect',Connection),(discovery,'store',store),(data,'store',store),
         (discovery,'fetch_bars',bars),(data,'fetch_bars',bars),(discovery,'get_json',lambda *a,**k:page),
         (discovery,'reserve_call',lambda *a:None),(discovery,'event',event),(data,'event',event),
         (discovery.httpx,'Client',Client),
         (discovery,'rank',lambda *a,**k:original_rank(*a,**k,client=Client())),
         (discovery.settings,'paper_sentiment_enabled',False),(discovery.settings,'paper_mainkraft_enabled',True),
         (discovery.settings,'groq_key','fixture-key'),(discovery.settings,'groq_model','fixture-model'),(discovery.settings,'feed','sip')]:
        stack.enter_context(patch.object(module,name,value))
    result=discovery.research(session)
fixture={'instant':instant,'raw':raw,'catalog':catalog,'news':page,'completion':completion,'expected':{'result':result,'sources':sources,'events':events,'inserts':inserts,'bar_queries':bar_queries,'ranking_requests':ranking_requests}}
path=ROOT/'apps/backend-rust/tests/fixtures/research-parity.json'
blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Original reference changed; do not overwrite frozen fixture'
else:path.write_bytes(blob)
print('Saved full unmodified Python discovery, ranking, history and selection-freeze oracle')
