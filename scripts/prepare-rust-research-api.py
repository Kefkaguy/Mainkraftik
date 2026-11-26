"""Seed an isolated DB and capture unchanged Python API contracts against that DB."""
import json,os,sys,base64
from datetime import UTC,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID,uuid5,NAMESPACE_URL
from contextlib import ExitStack
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'apps/api'),str(ROOT/'apps/api/tests')]
from intelligence import main,monitoring,db
from intelligence.config import settings
from intelligence.calendar import schedule
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
from fastapi.testclient import TestClient
from PIL import features as pillow_features

uri=os.environ['RUST_TEST_DATABASE_URL'];target=urlsplit(uri)
assert target.hostname in ('localhost','127.0.0.1') and target.port==5543 and target.path.startswith('/intelligence_test_rust_')
settings.database_url=uri;settings.feed='sip';db.migrate()
instant=datetime(2026,11,19,6,tzinfo=UTC)
days=list(schedule(instant.date()-timedelta(days=150),instant.date()-timedelta(days=1)))[-90:]
ids={symbol:uuid5(NAMESPACE_URL,'mainkraft-rust-api-fixture-'+symbol) for symbol in ['MSI','AXON','SPY']}
with db.connection() as conn:
    for stock,source in ids.items():
        conn.execute('INSERT INTO source_documents VALUES(%s,%s,%s,%s,%s,%s)',(source,'fixture','https://example.test/'+stock,digest(stock),instant-timedelta(days=1),jsonb({'fixture':True})))
        for index,day in enumerate(days):
            price=100+index*0.2+(0.1 if stock=='AXON' else 0)
            available=schedule(day,day)[day]['close']+timedelta(minutes=15)
            conn.execute("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES(%s,%s,'sip','split',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(stock,day,instant.date(),price,price+2,price-2,price+0.1,1000000,available,available+timedelta(seconds=1),source,digest([stock,str(day)])))
    # A newly acquired version must remain visible historically under the
    # original bars_at availability-only view, but not under observed-only cuts.
    day=days[-1];available=schedule(day,day)[day]['close']+timedelta(minutes=15)
    conn.execute("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES('MSI',%s,'sip','split',%s,118,120,116,119,1000000,%s,%s,%s,%s)",(day,instant.date(),available,instant+timedelta(days=1),ids['MSI'],digest('late-vintage')))
    fixture=json.loads((ROOT/'apps/backend-rust/tests/fixtures/financial-parity.json').read_text(encoding='utf-8'))
    for row in fixture['cases'][4]['rows']:
        conn.execute('INSERT INTO financial_facts(symbol,metric,tag,unit,value,period_start,period_end,fiscal_year,fiscal_period,form,accession,filed_date,available_at,acquired_at,source_id,source_url,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,2025,\'Q1\',\'10-Q\',%s,\'2025-05-01\',%s,%s,%s,%s,%s)',('MSI',row['metric'],row['tag'],row['unit'],row['value'],row['period_start'],row['period_end'],row['accession'],row['available_at'],row['acquired_at'],ids['MSI'],row['source_url'],digest(row)))
    conn.execute("INSERT INTO dataset_manifests(id,kind,created_at,feed,adjustment,payload) VALUES(%s,'fixture',%s,'sip','split','{}')",('f'*64,instant))
    conn.execute("INSERT INTO research_plans(id,created_at,dataset_id,protected_start,specification,code_hash) VALUES(%s,%s,%s,'2026-01-01','{}',%s)",('p'*64,instant,'f'*64,'c'*64))
    conn.execute("INSERT INTO research_candidates(id,specification,created_at) VALUES('fixture-candidate','{}',%s)",(instant,))
    conn.execute("INSERT INTO research_experiments(id,plan_id,candidate_id,symbol,horizon,created_at,status,report) VALUES(%s,%s,'fixture-candidate','MSI',1,%s,'failed',%s)",('a'*64,'p'*64,instant,jsonb({'dataset_id':'f'*64,'status':'no_edge'})))
    conn.execute("INSERT INTO research_predictions VALUES(%s,'2025-11-03','2025-11-04',0.001,0.51,-0.03,0.03,-0.02,0,0.5,'fixture')",('a'*64,))
    conn.execute("INSERT INTO research_audits VALUES(%s,%s,%s)",('d'*64,instant,jsonb({'status':'failed'})))
    run=uuid5(NAMESPACE_URL,'mainkraft-rust-api-run')
    conn.execute("INSERT INTO forecast_runs(id,origin_session,started_at,status,detail) VALUES(%s,'2026-11-16',%s,'completed','{}')",(run,instant))
    for i,probability in enumerate([0.0,0.1,0.5,0.7,1.0]):
        fid=uuid5(NAMESPACE_URL,'mainkraft-rust-api-forecast-'+str(i))
        conn.execute("INSERT INTO model_versions(id,kind,specification,created_at) VALUES(%s,'fixture','{}',%s)",('fixture-model-'+str(i),instant))
        conn.execute("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES(%s,%s,'MSI','2026-11-16',1,%s,%s,'2026-11-17','2026-11-17','sip','split','2026-11-16',%s,%s,'fixture',%s,%s,-0.03,0.03,300,'{}')",(fid,run,instant-timedelta(hours=i),instant-timedelta(days=1),'f'*64,'fixture-model-'+str(i),i/1000,probability))
        if i<4:
            conn.execute("INSERT INTO forecast_outcomes(id,forecast_id,resolved_at,realized_return,spy_return,entry_bar_id,exit_bar_id,provenance) VALUES(%s,%s,%s,%s,0,(SELECT min(id) FROM daily_bars WHERE symbol='MSI'),(SELECT max(id) FROM daily_bars WHERE symbol='MSI'),'{}')",(uuid5(NAMESPACE_URL,'mainkraft-rust-api-outcome-'+str(i)),fid,instant-timedelta(hours=1),[0.02,-0.01,0.0,0.05][i]))
    conn.execute("INSERT INTO jobs(id,kind,payload,status,created_at,completed_at,error) VALUES(%s,'refresh','{}','failed',%s,%s,'fixture unavailable')",(uuid5(NAMESPACE_URL,'mainkraft-rust-api-job'),instant,instant))
    conn.execute("INSERT INTO forecast_outcomes(id,forecast_id,resolved_at,realized_return,spy_return,entry_bar_id,exit_bar_id,provenance,supersedes) VALUES(%s,%s,%s,0.025,0.001,(SELECT min(id) FROM daily_bars WHERE symbol='MSI'),(SELECT max(id) FROM daily_bars WHERE symbol='MSI'),%s,%s)",(uuid5(NAMESPACE_URL,'mainkraft-rust-api-outcome-revision'),uuid5(NAMESPACE_URL,'mainkraft-rust-api-forecast-0'),instant,jsonb({'revision':True}),uuid5(NAMESPACE_URL,'mainkraft-rust-api-outcome-0')))
    conn.execute("INSERT INTO worker_status VALUES('research-worker',%s,'{}')",(instant-timedelta(minutes=1),))
    conn.execute("INSERT INTO tuning_plans VALUES(%s,%s,%s,'{}','{}')",('b'*64,instant,'c'*64))
    conn.execute("INSERT INTO candidate_results VALUES(%s,%s,'ridge','market','MSI',1,'failed',%s,%s)",('e'*64,'b'*64,jsonb({'status':'no_edge','artifact':{'private':'OMIT_FROM_PHASE3'}}),jsonb([{'kind':'historical','expected_return':0.0}])))
    from intelligence.storage import bars_at
    archived_cutoff=conn.execute('SELECT %s::timestamptz AS t',(instant,)).fetchone()['t']
    content,manifest,image_hash=main.render_chart(bars_at(conn,'MSI',instant,instant),archived_cutoff)
    conn.execute("INSERT INTO vision_models(id,repository,revision,weight_file) VALUES('render-fixture','fixture','fixture','fixture')")
    for name,bad_image,bad_input in [('valid',False,False),('bad-image',True,False),('bad-input',False,True)]:
        conn.execute('INSERT INTO chart_detections VALUES(%s,\'MSI\',\'render-fixture\',%s,%s,\'candles-90-square-v1\',%s,%s,%s,%s)',
            (uuid5(NAMESPACE_URL,'mainkraft-rust-chart-'+name),instant,archived_cutoff,'0'*64 if bad_input else digest(manifest),'0'*64 if bad_image else image_hash,'0'*64,jsonb({'manifest':manifest,'boxes':[{'label':'fixture flag','confidence':.125,'xyxy':[20.4921874999,40.5078125001,300,250]}]})))

paths=['/v1/workspace','/v1/companies/MSI','/v1/companies/AXON','/v1/companies/VOID','/v1/companies/bad',
       '/v1/companies/MSI?as_of=2025-06-01T00:00:00%2B00:00','/v1/companies/MSI?as_of=2027-06-01T00:00:00%2B00:00',
       '/v1/forecasts','/v1/forecasts?symbol=MSI','/v1/forecasts?symbol=','/v1/forecasts?symbol=bad',
       '/v1/models/performance','/v1/research','/v1/research/experiments/'+'a'*64+'/predictions',
       '/v1/research/experiments/'+'0'*64+'/predictions','/v1/research/experiments/bad/predictions',
       '/v1/phase3/predictions/'+'0'*64,'/v1/phase3/predictions/bad','/v1/phase3/predictions/'+'e'*64,
       '/v1/phase3','/v1/phase3?symbol=MSI','/v1/phase3?symbol=AXON','/v1/phase3?symbol=bad','/v1/monitor',
       '/v1/jobs/'+str(uuid5(NAMESPACE_URL,'mainkraft-rust-api-job')),'/v1/jobs/'+str(UUID(int=0))]
paths.extend(['/v1/companies/MSI/chart','/v1/companies/AXON/chart','/v1/companies/VOID/chart','/v1/companies/bad/chart',
              *['/v1/detections/'+str(uuid5(NAMESPACE_URL,'mainkraft-rust-chart-'+name))+'/chart' for name in ['valid','bad-image','bad-input']],
              '/v1/detections/'+str(UUID(int=0))+'/chart'])
main.app.dependency_overrides[main.require_session]=lambda:{'csrf_token':'fixture-csrf','token_hash':'fixture'}
try:
    with ExitStack() as stack:
        stack.enter_context(patch.object(main,'now',return_value=instant));stack.enter_context(patch.object(monitoring,'now',return_value=instant))
        client=TestClient(main.app)
        cases=[]
        for path in paths:
            response=client.get(path)
            cases.append({'path':path,'status':response.status_code,**({'png':base64.b64encode(response.content).decode()} if response.headers.get('content-type')=='image/png' else {'body':response.json()})})
finally:main.app.dependency_overrides.clear()
zlib_version=pillow_features.version('zlib')
if zlib_version=='1.3':png_profile='pillow-zlib-1.3'
elif zlib_version.endswith('.zlib-ng') and pillow_features.version('zlib_ng')=='2.3.3':png_profile='pillow-zlib-ng-2.3.3'
else:raise RuntimeError('Original Pillow compression profile is not independently validated')
output={'png_compression':png_profile,'instant':instant.isoformat(),'cases':cases,'fixture_only':True,'execution_authority':False}
target=Path(os.environ['RUST_RESEARCH_API_ORACLE'])
with target.open('x',encoding='utf-8') as handle:handle.write(canonical(output))
print(f'Captured {len(cases)} original research API responses against isolated seeded PostgreSQL')
