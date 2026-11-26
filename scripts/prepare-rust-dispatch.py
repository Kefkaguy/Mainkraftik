"""Unchanged Python durable worker + real workflows, isolated schemas and fake HTTPS."""
import copy,json,os,sys
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from uuid import uuid5,NAMESPACE_URL
import httpx,psycopg
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,jobs,ingestion,information,ir_metadata,providers,forecasting,vision
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical
u=urlsplit(os.environ['RUST_TEST_DATABASE_URL'])
assert u.hostname in ['localhost','127.0.0.1'] and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
with psycopg.connect(os.environ['RUST_TEST_DATABASE_URL'],autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
instant=datetime(2026,11,20,6,tzinfo=UTC);facts=json.loads((ROOT/'apps/backend-rust/tests/fixtures/research-providers.json').read_text())['facts'][0]['facts']
raw=[{'t':'2026-11-18T05:00:00Z','o':'100.000000000000000001','h':103,'l':99,'c':102,'v':1000000}]
forecast_id=uuid5(NAMESPACE_URL,'dispatch-forecast');run_id=uuid5(NAMESPACE_URL,'dispatch-forecast-run');detection_id=uuid5(NAMESPACE_URL,'dispatch-detection')
for schema in ['python_reference','public']:
    settings.database_url=scoped(schema);db.migrate()
    with db.connection() as c:
        c.execute("INSERT INTO watchlist_versions(symbols) VALUES('[\"MSI\"]')")
        c.execute("INSERT INTO model_versions(id,kind,specification) VALUES('dispatch-fixture','fixture','{}')")
        c.execute("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,'fixture','sip','split','{}')",('f'*64,))
        c.execute("INSERT INTO forecast_runs VALUES(%s,'2026-11-17','2026-11-18T01:00Z','2026-11-18T01:00Z','completed','{}')",(run_id,))
        c.execute("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES(%s,%s,'MSI','2026-11-17',1,'2026-11-18T01:00Z','2026-11-18T00:00Z','2026-11-18','2026-11-18','sip','split','2026-11-20',%s,'dispatch-fixture','fixture',0.001,0.6,-0.03,0.03,300,'{}')",(forecast_id,run_id,'f'*64))
        model=c.execute('SELECT id FROM vision_models ORDER BY id LIMIT 1').fetchone()['id']
        c.execute("INSERT INTO chart_detections VALUES(%s,'MSI',%s,'2026-11-18T01:00Z','2026-11-18T01:00Z','fixture',%s,%s,%s,%s)",(detection_id,model,'a'*64,'b'*64,'c'*64,jsonb({'manifest':{'last_session':'2026-11-17'}})))
settings.database_url=scoped('python_reference')
state={};requests=[];projections=[];delays=[]
A='https://investor.axon.com';M='https://www.motorolasolutions.com'
class Client:
    def __init__(self,*args,**kw):self.agent=kw.get('headers',{}).get('User-Agent')
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def close(self):pass
    def get(self,url,**kw):
        ir=url.startswith((A,M));requests.append({'url':url,'query':copy.deepcopy(kw.get('params',{})),'headers':copy.deepcopy(kw.get('headers',{})),'agent':self.agent if ir else None})
        status=200;payload={};body=None
        if ir:
            if url.endswith('/robots.txt'):body='User-agent: *\nAllow: /'
            elif url.endswith('/press-releases/'):body='<a href="/2026-11-18-story">fixture</a>'
            elif url.endswith('/newsroom.html'):body='<a href="/newsroom/press-releases/fixture.html">fixture</a>'
            else:body='<h1>Raises guidance</h1><meta name="press_release_publish_date" content="2026W473T120000Z">'
        elif url.endswith('/news'):
            if state.get('phase')=='denied':status=403
            else:payload={'news':[{'id':'dispatch-news','created_at':'0001-01-01T00+01' if state.get('phase')=='overflow' else '2026-11-18T12Z','updated_at':'2026-11-18T13Z','symbols':['MSI'],'headline':'Contract awarded','content':'PRIVATE_BODY_NOT_PERSISTED'}]}
        elif url.endswith('/bars'):payload={'bars':raw}
        elif url.endswith('corporate-actions'):payload={'corporate_actions':{}}
        elif '/submissions/' in url:payload={}
        elif 'CIK0001069183' in url:payload={}
        else:payload=facts
        return httpx.Response(status,content=body.encode() if body is not None else None,json=payload if body is None else None,request=httpx.Request('GET',url))
def gate(projected_bytes=32*1024**2):
    projections.append(projected_bytes)
    if state.get('phase')=='resource-denied':raise providers.ProviderError('Fixture resource exhausted')
cases=[]
specs=[('refresh',['MSI','SPY'],'normal'),('resolve',None,'normal'),('information',None,'normal'),
 ('refresh',None,'normal'),('refresh',[],'normal'),('refresh',{'MSI':1,'SPY':2},'normal'),('refresh',['MSI','MSI','SPY'],'normal'),
 ('refresh',['UNKNOWN'],'normal'),('refresh',['MSI']*27,'normal'),('refresh','MSI','normal'),('refresh',[['MSI']],'normal'),('refresh',['MSI',None],'normal'),
 ('refresh',17,'normal'),('refresh',False,'normal'),('refresh',None,'resource-denied'),
 ('refresh',['MSI','SPY'],'denied'),('information',None,'denied'),('refresh',['MSI','SPY'],'overflow'),('information',None,'overflow')]
with patch.object(settings,'feed','sip'),patch.object(settings,'alpaca_key','fixture-key'),patch.object(settings,'alpaca_secret','fixture-secret'),patch.object(settings,'sec_user_agent','fixture@example.invalid'),patch.object(information.httpx,'Client',Client),patch.object(jobs,'resource_check',side_effect=gate),patch.object(ingestion,'resource_check',side_effect=gate),patch.object(information,'resource_check',side_effect=gate),patch.object(providers.time,'sleep',lambda n:delays.append(round(n*1000))),patch.object(information.time,'sleep',lambda n:delays.append(round(n*1000))):
    from contextlib import ExitStack
    with ExitStack() as stack:
        for module in [ingestion,information,ir_metadata,forecasting,vision]:stack.enter_context(patch.object(module,'now',return_value=instant))
        for index,(kind,symbols,phase) in enumerate(specs):
            id=uuid5(NAMESPACE_URL,'dispatch-case-'+str(index));state={'phase':phase};requests.clear();projections.clear();delays.clear()
            payload={} if symbols is None else {'symbols':symbols}
            with patch.object(jobs,'uuid4',return_value=id),patch.object(ingestion,'uuid4',return_value=id):
                jobs.enqueue(kind,payload);result=jobs.run_once()
            cases.append({'id':str(id),'kind':kind,'payload':payload,'phase':phase,'result':result,'requests':copy.deepcopy(requests),'projections':projections.copy(),'delays':delays.copy()})
queries={
 'jobs':"SELECT id,kind,payload,status,attempts,result,error FROM jobs ORDER BY id",
 'events':"SELECT job_id,event,detail FROM job_events ORDER BY id",
 'runs':"SELECT id,kind,symbols,status,result,error FROM ingest_runs ORDER BY id",
 'documents':"SELECT provider,url,content_hash,acquired_at,payload FROM source_documents ORDER BY provider,url,content_hash",
 'bars':"SELECT symbol,session_date,feed,adjustment_asof,open::text,high::text,low::text,close::text,volume::text,available_at,acquired_at,content_hash FROM daily_bars ORDER BY symbol,session_date",
 'facts':"SELECT symbol,metric,tag,period_start,period_end,unit,value::text,filed_date,available_at,acquired_at,content_hash FROM financial_facts ORDER BY symbol,metric,tag,content_hash",
 'metrics':"SELECT symbol,as_of,calculation_version,inputs_hash,payload FROM calculated_metrics ORDER BY symbol,inputs_hash",
 'information':"SELECT id,provider,external_id,symbols,created_at,updated_at,available_at,acquired_at,payload FROM information_versions ORDER BY id",
 'coverage':"SELECT provider,symbols,started_at,ended_at,acquired_at,status,detail FROM information_fetches ORDER BY status,detail::text",
 'outcomes':"SELECT forecast_id,resolved_at,realized_return,spy_return,entry_bar_id,exit_bar_id,provenance FROM forecast_outcomes ORDER BY forecast_id",
 'detection_outcomes':"SELECT detection_id,horizon,realized_return,resolved_at,provenance FROM detection_outcomes ORDER BY detection_id,horizon"}
with db.connection() as c:snapshot={name:c.execute(sql).fetchall() for name,sql in queries.items()}
path=Path(os.environ['RUST_DISPATCH_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'instant':instant,'raw':raw,'facts':facts,'cases':cases,'queries':queries,'snapshot':snapshot}))
print(f'Captured {len(cases)} original durable jobs and {len(queries)} persisted evidence families; actual workflows, fake HTTP only')
