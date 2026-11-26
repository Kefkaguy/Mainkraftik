"""Original ingestion oracle in a separate schema of a disposable localhost test DB."""
import copy,hashlib,json,os,sys
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import httpx,psycopg
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,ingestion,providers
from intelligence.config import settings
from intelligence.util import canonical
uri=os.environ['RUST_TEST_DATABASE_URL'];target=urlsplit(uri)
assert target.hostname in ('localhost','127.0.0.1') and target.port==5543 and target.path.startswith('/intelligence_test_rust_')
with psycopg.connect(uri,autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
settings.database_url=urlunsplit(target._replace(query=urlencode([*parse_qsl(target.query),('options','-csearch_path=python_reference')])))
db.migrate();instant=datetime(2026,11,19,6,tzinfo=UTC)
provider=json.loads((ROOT/'apps/backend-rust/tests/fixtures/research-providers.json').read_text())
facts=provider['facts'][0]['facts']
raw=[{'t':'2026-11-16T05:00:00Z','o':'100.00000000000000000002','h':'102.00000000000000000003','l':'99.99999999999999999999','c':'101.00000000000000000001','v':'1000000.0000000000000001'},
     {'t':'2026-11-18T05:00:00Z','o':101,'h':103,'l':100,'c':102,'v':1000000},
     {'t':'2026-11-19T05:00:00Z','o':102,'h':104,'l':101,'c':103,'v':1000000}]
actions={'corporate_actions':{'cash_dividends':[{'ex_date':'2026-11-18','cash':0.5}],'forward_splits':[{'process_date':'2026-11-17','ratio':2}], 'name_changes':[{'effective_date':'2026-11-16','name':'fixture'}]}}
class Client:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url,**kwargs):
        if '/bars' in url:return httpx.Response(200,json={'bars':raw},request=httpx.Request('GET',url))
        if 'corporate-actions' in url:return httpx.Response(403 if kwargs['params']['symbols']=='AXON' else 200,json=actions,request=httpx.Request('GET',url))
        if '/submissions/' in url:payload={}
        elif 'CIK0001069183' in url:payload={}
        else:payload=facts
        return httpx.Response(200,json=payload,request=httpx.Request('GET',url))
client=Client();gates=[];cases=[]
def resource(projected_bytes=32*1024**2):gates.append(projected_bytes)
operations=[('prices','MSI'),('prices','MSI'),('financials','MSI'),('financials','MSI'),('prices','AXON'),('financials','SPY'),('financials','AXON'),('all',['MSI','SPY','AXON']),('all',[])]
with patch.object(settings,'alpaca_key','fixture-key'),patch.object(settings,'alpaca_secret','fixture-secret'),patch.object(settings,'feed','sip'),patch.object(settings,'sec_user_agent','fixture@example.invalid'),patch.object(ingestion,'now',return_value=instant),patch.object(ingestion,'resource_check',side_effect=resource),patch.object(providers.time,'sleep'),patch.object(providers.httpx,'Client',return_value=client):
    for operation,arg in operations:
        before=len(gates)
        try:
            result=getattr(ingestion,'ingest_'+operation)(arg) if operation=='all' else getattr(ingestion,'ingest_'+operation)(arg,client)
            if operation=='all':result.pop('run_id')
            outcome={'result':result}
        except providers.ProviderError as e:outcome={'error':str(e)}
        cases.append({'operation':operation,'arg':arg,'gates':gates[before:],**outcome})
def snapshot():
    with db.connection() as c:
        return {
          'documents':c.execute('SELECT provider,url,content_hash,acquired_at,payload FROM source_documents ORDER BY provider,url,content_hash').fetchall(),
          'bars':c.execute('SELECT b.symbol,b.session_date,b.feed,b.adjustment,b.adjustment_asof,b.open::text,b.high::text,b.low::text,b.close::text,b.volume::text,b.available_at,b.acquired_at,b.content_hash,s.content_hash source_hash FROM daily_bars b JOIN source_documents s ON s.id=b.source_id ORDER BY b.symbol,b.session_date').fetchall(),
          'actions':c.execute('SELECT a.symbol,a.action_type,a.effective_date,a.acquired_at,a.content_hash,a.payload,s.content_hash source_hash FROM corporate_actions a JOIN source_documents s ON s.id=a.source_id ORDER BY a.symbol,a.action_type,a.content_hash').fetchall(),
          'facts':c.execute('SELECT (to_jsonb(f)-\'id\'-\'source_id\')||jsonb_build_object(\'source_hash\',s.content_hash,\'value\',f.value::text) AS record FROM financial_facts f JOIN source_documents s ON s.id=f.source_id ORDER BY f.symbol,f.metric,f.tag,f.content_hash').fetchall(),
          'metrics':c.execute('SELECT symbol,as_of,calculation_version,inputs_hash,payload FROM calculated_metrics ORDER BY symbol,inputs_hash').fetchall(),
          'runs':c.execute('SELECT kind,symbols,status,result,error FROM ingest_runs ORDER BY started_at').fetchall()}
resource_cases=[]
for free,projected,reserve,size in [(11*1024**3,32*1024**2,10,8*1024**2),(10*1024**3,32*1024**2,10,8*1024**2),(32*1024**2,32*1024**2,0,224*1024**2),(32*1024**2-1,32*1024**2,0,8*1024**2),(11*1024**3,32*1024**2,10,224*1024**2+1)]:
    error=None
    with patch.object(settings,'data_dir','.local/rust-migration/resource-fixture'),patch.object(settings,'min_free_gb',reserve),patch.object(ingestion.shutil,'disk_usage',return_value=__import__('collections').namedtuple('Usage','total used free')(free,0,free)):
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,*args):return self
            def fetchone(self):return {'n':size}
        with patch.object(ingestion,'connection',return_value=Connection()):
            try:ingestion.resource_check(projected)
            except providers.ProviderError as e:error=str(e)
    resource_cases.append({'free':free,'projected':projected,'reserve':reserve,'size':size,'error':error})
fixture={'instant':instant,'raw':raw,'facts':facts,'actions':actions,'cases':cases,'snapshot':snapshot(),'resource_cases':resource_cases,'source_sha256':hashlib.sha256((ROOT/'apps/api/intelligence/ingestion.py').read_bytes()).hexdigest()}
path=Path(os.environ['RUST_INGESTION_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(canonical(fixture),encoding='utf-8')
print('Captured nine original ingestion operations, all persisted research tables, and five resource boundaries')
