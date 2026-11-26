"""Full unchanged official IR workflow oracle using synthetic HTML and no network."""
import copy,hashlib,json,os,sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
import httpx,psycopg
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,information,ir_metadata,ingestion
from intelligence.config import settings
from intelligence.util import canonical
uri=os.environ['RUST_TEST_DATABASE_URL'];u=urlsplit(uri)
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
with psycopg.connect(uri,autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
for schema in ['python_reference','public']:
    settings.database_url=urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])));db.migrate()
settings.database_url=urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path=python_reference')])))
A='https://investor.axon.com';M='https://www.motorolasolutions.com';N='https://newsroom.motorolasolutions.com'
def routes(phase):
    pages={A+'/robots.txt':{'status':200,'body':'User-agent: *\r\nDisallow: /2026-11-18-story-1\r\nAllow: /\r\n'},M+'/robots.txt':{'status':404,'body':'not found'},N+'/robots.txt':{'status':200,'body':'User-agent: *\nAllow: /'}}
    links=[]
    for i in range(14):
        url=(A+':443' if i==11 else A)+f'/2026-11-18-story-{i}'
        links.append(f'<a href="{url}">Listing {i} <b>label</b></a>')
        body=f'<h1>Reports results {i}</h1>'
        if i==0:body='<p>Raises guidance PARA_STORAGE_MARKER</p>'
        if i==4:body='<meta name="press_release_publish_date" content="2026-11-18T12:00:00Z">'+body
        if i==5:body+='<p>Company <b>lowers</b> outlook.</p>'
        if i==6:body+='<p>'+('x'*701)+' raises outlook</p>'
        if i==7:body='<meta property="wrong" PROPERTY="og:title" content="first" CONTENT="last &amp; title">'+body
        if i==8:body='<script>"<meta name=press_release_publish_date content=invalid>";</script>'+body
        if i==9:body='<meta name="pubdate" content="2026-11-18T14:00:00Z">'+body
        if i==10:body='<h1>Unicode \u00e9 &amp; \U0001f600</h1>'
        if i==11:body='<h1>Nested</h1><p>outer<p>maintains outlook</p>tail</p>'
        if phase=='enrich' and i==0:body='<meta name="wrong" property="article:published_time" content="ignored"><meta property="article:published_time" content="2026-11-18T10:00:00Z">'+body
        if phase=='edited' and i==0:body='<h1>Changed headline</h1><p>Raised guidance</p>'
        if phase=='compact' and i==4:body='<meta name="press_release_publish_date" content="2026W473T120000Z">'+body
        if phase=='overflow' and i==0:body='<meta name="press_release_publish_date" content="0001-01-01T00:00:00+01:00">'+body
        pages[url]={'status':404 if i==2 else 200,'body':body}
    links.extend([f'<a href="{A}/2026-11-18-story-0">Updated duplicate title</a>','<a href="https://evil.invalid/2026-11-18-fake">ignore</a>'])
    pages[A+'/press-releases/']={'status':200,'body':'<html>'+''.join(links)+'</html>'}
    pages[A+'/2026-11-18-story-3']={'status':302,'location':N+'/shared-story'}
    pages[N+'/shared-story']={'status':200,'body':'<h1>Allowed redirect title</h1>'}
    pages[M+'/newsroom.html']={'status':302,'location':N+'/listing'}
    pages[N+'/listing']={'status':200,'body':'<a href="/newsroom/press-releases/one.html">Motorola <b>one</b></a><a href="/newsroom/press-releases/de.html">skip</a><a href="https://newsroom.motorolasolutions.com/newsroom/press-releases/two.html">two</a>'}
    pages[M+'/newsroom/press-releases/one.html']={'status':200,'body':'<h1>Motorola contract</h1><meta property="article:published_time" content="2026-11-17T09:00:00Z">'}
    pages[N+'/newsroom/press-releases/two.html']={'status':200,'body':'<h1>Future</h1><meta name="press_release_publish_date" content="2027-01-01T00:00:00Z">'}
    if phase=='blocked':pages[A+'/robots.txt']={'status':200,'body':'User-agent: *\nDisallow: /'}
    if phase=='unrestricted':pages[A+'/robots.txt']={'status':200,'body':'User-agent: *\nAllow: /'}
    if phase=='external':pages[A+'/2026-11-18-story-0']={'status':302,'location':'https://evil.invalid/collect'}
    if phase=='oversize':pages[A+'/press-releases/']={'status':200,'body_size':3*1024**2+1}
    if phase=='missing':pages[M+'/newsroom.html']={'status':404,'body':'missing'}
    return pages
cases=[]
class Client:
    def __init__(self,*args,**kwargs):self.agent=kwargs.get('headers',{}).get('User-Agent','');self.timeout=kwargs.get('timeout');self.redirects=kwargs.get('follow_redirects')
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def close(self):pass
    def get(self,url,**kwargs):
        if 'data.alpaca.markets' in url:
            data_requests.append({'url':url,'headers':copy.deepcopy(kwargs.get('headers',{})),'query':copy.deepcopy(kwargs.get('params',{})),'timeout':self.timeout})
            if url.endswith('/news'):payload={'news':[{'id':'refresh-fixture','created_at':'2026-11-18T12:00:00Z','updated_at':'2026-11-18T13:00:00Z','symbols':['MSI'],'headline':'Company reports results','content':'NOT_STORED_NEWS_BODY'}]};status=200
            elif url.endswith('/bars'):payload={'bars':[{'t':'2026-11-18T05:00:00Z','o':100,'h':102,'l':99,'c':101,'v':1000000}]};status=200
            else:payload={};status=403
            return httpx.Response(status,json=payload,request=httpx.Request('GET',url))
        requests.append({'url':url,'agent':self.agent});spec=pages[url]
        content=bytes.fromhex(spec['hex']) if 'hex' in spec else ('x'*spec['body_size']).encode() if 'body_size' in spec else spec.get('body','').encode()
        headers={'location':spec['location']} if 'location' in spec else {}
        if 'charset' in spec:headers['content-type']='text/html; charset='+spec['charset']
        return httpx.Response(spec['status'],content=content,headers=headers,request=httpx.Request('GET',url))
instant=datetime(2026,11,20,6,tzinfo=UTC)
for index,(operation,phase) in enumerate([('ingest','normal'),('enrich','enrich'),('enrich','enrich'),('ingest','edited'),('ingest','unrestricted'),('ingest','blocked'),('ingest','external'),('ingest','missing'),('ingest','oversize'),('refresh','normal'),('backfill','normal'),('ingest','compact'),('enrich','compact'),('ingest','overflow'),('enrich','overflow')]):
    at=instant+timedelta(hours=index);pages=routes(phase);requests=[];data_requests=[];delays=[];projections=[]
    def resource(projected_bytes=32*1024**2):projections.append(projected_bytes)
    with patch.object(settings,'sec_user_agent','fixture@example.invalid'),patch.object(settings,'alpaca_key','fixture-key'),patch.object(settings,'alpaca_secret','fixture-secret'),patch.object(settings,'feed','sip'),patch.object(information.httpx,'Client',Client),patch.object(information,'now',return_value=at),patch.object(ir_metadata,'now',return_value=at),patch.object(ingestion,'now',return_value=at),patch.object(information.time,'sleep',lambda seconds:delays.append(round(seconds*1000))),patch.object(information,'resource_check',side_effect=resource),patch.object(ingestion,'resource_check',side_effect=resource):
        try:outcome={'result':information.ingest_ir() if operation=='ingest' else ir_metadata.enrich_ir_publications() if operation=='enrich' else information.refresh_information(backfill=operation=='backfill')}
        except Exception as e:outcome={'reference_error':{'kind':type(e).__name__,'message':str(e)}}
    cases.append({'operation':operation,'phase':phase,'instant':at,'routes':pages,'requests':requests,'data_requests':data_requests,'projections':projections,'delays':delays,**outcome})
for label,start,spec in [
 ('loop',A+'/loop',{'status':302,'location':'/loop'}),('forbidden','https://evil.invalid/a',None),('http','http://investor.axon.com/a',None),
 ('limit',A+'/limit',{'status':200,'body_size':3*1024**2}),('over-limit',A+'/limit',{'status':200,'body_size':3*1024**2+1}),
 ('not-found',A+'/missing',{'status':404,'body':'missing'}),('latin1',A+'/encoding',{'status':200,'hex':'4180e9','charset':'iso-8859-1'}),
 ('utf8',A+'/encoding',{'status':200,'hex':'efbbbf41ffe9','charset':'utf-8'}),('cp1252',A+'/encoding',{'status':200,'hex':'41808191','charset':'windows-1252'})]:
    pages={} if spec is None else {start:spec};requests=[];delays=[]
    client=Client(headers={'User-Agent':'fixture@example.invalid'})
    try:
        response=information.ir_get(client,start);outcome={'result':{'url':str(response.url),'status':response.status_code,'text':response.text if label in ['latin1','utf8','cp1252'] else None,'sha256':hashlib.sha256(response.content).hexdigest()}}
    except ValueError as e:outcome={'error':str(e)}
    cases.append({'operation':'get','label':label,'url':start,'routes':pages,'requests':requests,'data_requests':[],'projections':[],'delays':delays,**outcome})
queries={'documents':"SELECT provider,url,content_hash,acquired_at,payload FROM source_documents ORDER BY provider,url,content_hash",'versions':"SELECT v.id,v.provider,v.external_id,v.symbols,v.created_at,v.updated_at,v.available_at,v.acquired_at,v.payload,s.content_hash source_hash FROM information_versions v JOIN source_documents s ON s.id=v.source_id ORDER BY v.id",'bars':"SELECT symbol,session_date,feed,adjustment,adjustment_asof,open::text,high::text,low::text,close::text,volume::text,available_at,acquired_at,content_hash FROM daily_bars ORDER BY symbol,session_date",'fetches':"SELECT provider,symbols,started_at,ended_at,acquired_at,status,detail FROM information_fetches ORDER BY started_at,ended_at,status"}
with db.connection() as c:snapshot={name:c.execute(sql).fetchall() for name,sql in queries.items()}
path=Path(os.environ['RUST_IR_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'cases':cases,'queries':queries,'snapshot':snapshot}))
print('Captured full original IR ingestion/publication enrichment, redirects, limits, robots and encoding cases')
