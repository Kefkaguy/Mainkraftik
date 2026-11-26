"""Private information ingestion oracle in independent schemas of a disposable local DB."""
import copy,json,os,sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from uuid import uuid5,NAMESPACE_URL
import httpx,psycopg
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,information,providers
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
uri=os.environ['RUST_TEST_DATABASE_URL'];u=urlsplit(uri)
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
with psycopg.connect(uri,autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
instant=datetime(2026,11,20,6,tzinfo=UTC);start=datetime(2026,11,1,tzinfo=UTC);end=instant-timedelta(minutes=20)
base=json.loads((ROOT/'apps/backend-rust/tests/fixtures/information-metadata.json').read_text())['news'][0]['row']
other=dict(base,id=102,headline='Awarded contract',symbols=['AXON']);changed=dict(base,updated_at='2026-11-18T14:00:00Z',headline='Reports financial results, lowered outlook')
ok=lambda payload:{'status':200,'payload':payload}
two=[ok({'news':[base,other],'next_page_token':'two'}),ok({'news':[changed,other]})]
inputs=[('two-pages',two,start,end),('replay',two,start,end),('repeated',[ok({'news':[base],'next_page_token':'x'})]*2,start,end),
 ('denied',[{'status':403}],start,end),('missing-key',[ok({'news':[{k:v for k,v in base.items() if k!='id'}]})],start,end),
 ('partial-invalid',[ok({'news':[base],'next_page_token':'bad'}),ok({'news':[dict(base,updated_at='invalid')]})],start,end),
 ('empty',[ok({'news':[]})],start,end),('default',[ok({'news':[base]})],None,None),
 ('connection',['connection failure'],start,end),('naive',[ok({'news':[dict(base,updated_at='2026-11-18T15:00:00')]})],start,end),
 ('invalid-interval',[],end,start),('page-ceiling',[ok({'news':[],'next_page_token':str(i)}) for i in range(600)],start,end)]
compact=dict(base,id=201,created_at='20261118T120000Z',updated_at='2026W473T130000Z')
overflow=dict(base,id=202,created_at='0001-01-01T00:00:00+01:00')
inputs += [('compact-week-timestamps',[ok({'news':[compact]})],start,end),
 ('utc-overflow',[ok({'news':[overflow]})],start,end),
 ('partial-utc-overflow',[ok({'news':[compact],'next_page_token':'overflow'}),ok({'news':[overflow]})],start,end),
 ('leap-second',[ok({'news':[dict(base,updated_at='2026-11-18T23:59:60Z')]})],start,end)]
sources=[]
for i,(tickers,items) in enumerate([(['MSI'],['2.02,9.01','','','8.01','']),(['SPY'],['2.02','','','','']),(['AXON','MSI'],['2.02','','','','']),(['MSI'],['2.02,7.01','','','8.01',''])]):
    payload={'tickers':tickers,'filings':{'recent':{'accessionNumber':['a','b','ignored','c','d'],'form':['8-K','10-Q','S-8','8-K/A','10-K/A'],
     'acceptanceDateTime':['2026-11-18T16:00:00Z','','2026-11-18T16:00:00Z','2026-11-18T08:00:00.123400-08:00',''],
     'filingDate':['2026-11-18','2026-03-08','2026-11-18','2026-11-18','2026-11-01'],'items':items}}}
    sources.append({'id':str(uuid5(NAMESPACE_URL,'information-submission-'+str(i))),'payload':payload,'acquired':instant+timedelta(seconds=i)})
official_source=uuid5(NAMESPACE_URL,'official-information-fixture')
for schema in ['python_reference','public']:
    settings.database_url=scoped(schema);db.migrate()
    with db.connection() as c:
        c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/official',%s,%s,'{}')",(official_source,digest({}),instant))
        for source in sources:c.execute("INSERT INTO source_documents VALUES(%s,'sec_submissions',%s,%s,%s,%s)",(source['id'],'https://data.sec.gov/fixture/'+source['id'],digest(source['payload']),source['acquired'],jsonb(source['payload'])))
settings.database_url=scoped('python_reference')
class Client:
    def __init__(self,responses):self.responses=iter(responses);self.requests=[]
    def get(self,url,**kwargs):
        self.requests.append({'url':url,'query':copy.deepcopy(kwargs.get('params',{})),'headers':copy.deepcopy(kwargs.get('headers',{}))})
        item=next(self.responses)
        if isinstance(item,str):raise httpx.ConnectError(item)
        return httpx.Response(item['status'],json=item.get('payload',{}),request=httpx.Request('GET',url))
cases=[]
with patch.object(settings,'alpaca_key','fixture-key'),patch.object(settings,'alpaca_secret','fixture-secret'),patch.object(information,'now',return_value=instant),patch.object(information,'resource_check') as gate:
    for name,responses,a,b in inputs:
        client=Client(responses);delays=[];gate.reset_mock()
        with patch.object(information.time,'sleep',lambda seconds:delays.append(round(seconds*1000))):
            try:outcome={'result':information.ingest_news(a,b,client)}
            except ValueError as e:outcome={'error':str(e)}
            except Exception as e:outcome={'reference_error':{'kind':type(e).__name__,'message':str(e)}}
        cases.append({'name':name,'responses':responses,'start':a,'end':b,'requests':client.requests,'delays':delays,'gate_calls':len(gate.call_args_list),**outcome})
official=[]
metadata={'external_id':'https://investor.axon.com/2026-11-18-fixture','symbols':['AXON'],'created_at':instant-timedelta(days=2),'updated_at':instant,'available_at':instant,'headline':'Raises guidance','flags':information.tags('Raises guidance'),'publication_original':'2026-11-18'}
with db.connection() as c:
    for row in [metadata,{**metadata,'updated_at':instant+timedelta(seconds=1),'available_at':instant+timedelta(seconds=1)}, {**metadata,'publication_original':'2026-11-18T12:00:00Z'}]:
        identity=information.persist_information(c,'official_ir',row,official_source,instant)
        official.append({'metadata':row,'identity':identity})
filings=[information.ingest_filings(),information.ingest_filings()]
queries={
 'documents':"SELECT provider,url,content_hash,acquired_at,payload FROM source_documents ORDER BY provider,url,content_hash",
 'versions':"SELECT v.id,v.provider,v.external_id,v.symbols,v.created_at,v.updated_at,v.available_at,v.acquired_at,v.payload,s.content_hash source_hash FROM information_versions v JOIN source_documents s ON s.id=v.source_id ORDER BY v.id",
 'fetches':"SELECT provider,symbols,started_at,ended_at,acquired_at,status,detail FROM information_fetches ORDER BY started_at,ended_at,status,detail::text",
 'filings':"SELECT f.id,f.symbol,f.accession,f.form,f.items,f.published_at,f.acquired_at,f.payload,s.content_hash source_hash FROM filing_events f JOIN source_documents s ON s.id=f.source_id ORDER BY f.id"}
with db.connection() as c:snapshot={name:c.execute(query).fetchall() for name,query in queries.items()}
path=Path(os.environ['RUST_INFORMATION_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'instant':instant,'cases':cases,'official_source':official_source,'official':official,'filings':filings,'queries':queries,'snapshot':snapshot}))
print(f'Captured {len(cases)} original news ingestion cases, filing replays, official metadata identities and four evidence families')
