"""Unchanged prospective issuance + real worker oracles; local synthetic schemas only."""
import copy,json,os,sys
from contextlib import ExitStack
from datetime import UTC,date,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from unittest.mock import patch
from uuid import NAMESPACE_URL,uuid5
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,forecasting,jobs,prospective,tuning
from intelligence.calendar import cutoff_for,schedule
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
uri=os.environ['RUST_TEST_DATABASE_URL'];u=urlsplit(uri)
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
original=json.loads((ROOT/'apps/backend-rust/tests/fixtures/point-features.json').read_text())
numeric=json.loads((ROOT/'apps/backend-rust/tests/fixtures/python-parity.json').read_text())['numeric']
artifacts={row['name']:row['artifact'] for row in numeric}
origin=date(2026,11,18);cutoff=cutoff_for(origin);instant=cutoff+timedelta(minutes=30)
specs=[('success','direct'),('repeat','direct'),('before-issuance','direct'),('before-entry','direct'),('entry-deadline','direct'),
 ('not-enrolled','direct'),('not-started','direct'),('code-mismatch','direct'),('artifact-mismatch','direct'),
 ('missing-origin','direct'),('late-origin','direct'),('future-correction','direct'),('no-paired-reference','direct'),
 ('zero-origin-volume','direct'),('news-unavailable','direct'),('market-unavailable','direct'),('sec-unavailable','direct'),
 ('all-available','direct'),('ir-missing','direct'),('context-gap','direct'),('missing-training-metadata','direct'),('database-failure','direct'),
 ('worker-success','worker'),('worker-repeat','worker'),('worker-code-mismatch','worker'),('worker-metadata-failure','worker'),
 ('worker-database-failure','worker'),('worker-resource-denied','worker'),('concurrent','concurrent'),('worker-terminal-recovery','worker'),
 ('short-history','direct'),('zero-prior-volume','direct'),('missing-code-hash','direct'),('missing-feature-group','direct')]
cases=[]
queries={
 'forecasts':"SELECT to_jsonb(f)-'id'-'run_id' AS record FROM forecasts f ORDER BY symbol,horizon,model_id",
 'features':"SELECT to_jsonb(f)-'created_at' AS record FROM feature_snapshots f ORDER BY id",
 'manifests':"SELECT id,kind,feed,adjustment,payload FROM dataset_manifests ORDER BY id",
 'runs':"SELECT origin_session,started_at,status,detail FROM forecast_runs ORDER BY started_at,detail::text,status",
 'protocol':"SELECT * FROM shadow_protocols ORDER BY id",
 'artifacts':"SELECT * FROM shadow_artifacts ORDER BY symbol,horizon",
 'events':"SELECT event,payload FROM shadow_events ORDER BY id",
 'jobs':"SELECT id,kind,payload,status,attempts,result,error FROM jobs ORDER BY id",
 'job_events':"SELECT job_id,event,detail FROM job_events ORDER BY id"}
def normalize(c,value):
    if isinstance(value,list):return [normalize(c,v) for v in value]
    if isinstance(value,dict):return {k:normalize(c,v) for k,v in value.items()}
    if isinstance(value,str):
        try:uid=__import__('uuid').UUID(value)
        except ValueError:return value
        row=c.execute("SELECT symbol||'/'||horizon||'/'||model_id AS key FROM forecasts WHERE id=%s",(uid,)).fetchone()
        if row:return row['key']
    return value
for index,(name,mode) in enumerate(specs):
    context=copy.deepcopy(original['contexts'][0]);stream=context['streams'];at=instant
    if mode=='worker':
        calendar=schedule(date(2025,1,1),origin)
        days=list(calendar)[-300:]
        for j,symbol in enumerate(stream):
            rows=[]
            for i,day in enumerate(days):
                close=100+j*32+i/8+(i%5-2)/16;opening=close+(i%3-1)/32
                rows.append({**stream[symbol][0],'id':j*10000+i+1,'session_date':day,'open':str(opening),'high':str(max(opening,close)+1.25),'low':str(min(opening,close)-1.5),'close':str(close),'volume':str(1000000+i*123+(i%7)*1000),'available_at':calendar[day]['close']+timedelta(minutes=15),'acquired_at':calendar[day]['close']+timedelta(minutes=16)})
            stream[symbol]=rows
    if name=='before-issuance':at=instant-timedelta(seconds=1)
    if name=='before-entry':at=datetime(2026,11,19,14,29,59,tzinfo=UTC)
    if name=='entry-deadline':at=datetime(2026,11,19,14,30,tzinfo=UTC)
    if name=='missing-origin':stream['MSI']=stream['MSI'][:-1]
    if name=='late-origin':stream['MSI'][-1]['acquired_at']=cutoff+timedelta(seconds=1)
    if name=='future-correction':stream['MSI'].append({**stream['MSI'][-1],'id':900001,'close':'125','high':'126','low':'100','acquired_at':cutoff+timedelta(seconds=1)})
    if name=='zero-origin-volume':stream['MSI'][-1]['volume']='0'
    if name=='short-history':stream['MSI']=stream['MSI'][-60:]
    if name=='zero-prior-volume':
        for r in stream['MSI'][-21:-1]:r['volume']='0'
    if name=='market-unavailable':stream['XLK']=stream['XLK'][:-1]
    if name=='context-gap':stream['XLI'].pop(20)
    if name=='news-unavailable':context['coverage']=[]
    if name=='sec-unavailable':context['facts']=[]
    if name=='ir-missing':context['information']=[r for r in context['information'] if r['provider']!='official_ir']
    group={'news-unavailable':'news','market-unavailable':'market','sec-unavailable':'sec','all-available':'all','ir-missing':'ir'}.get(name,'price')
    spec={'code_hash':'0'*64 if name in ['code-mismatch','worker-code-mismatch'] else tuning.implementation_hash(),'feature_group':group,'status':'experimental_inconclusive','promotion':'none'}
    if name=='missing-code-hash':spec.pop('code_hash')
    if name=='missing-feature-group':spec.pop('feature_group')
    frozen=[{'symbol':s,'horizon':h,'artifact':copy.deepcopy(artifacts['synthetic_histboost' if s=='AXON' and h==1 else 'deployed_day' if h==1 else 'deployed_swing'])} for s in ['MSI','AXON'] for h in [1,5]]
    if name in ['missing-training-metadata','worker-metadata-failure']:frozen[-1]['artifact'].pop('training_dates')
    frozen=sorted(frozen,key=lambda a:(a['symbol'],a['horizon']))
    artifact_hash='f'*64 if name=='artifact-mismatch' else digest(frozen)
    schemas={kind:f'{kind}_prospective_{index:02d}' for kind in ['python','native']}
    job_id=uuid5(NAMESPACE_URL,'rust-prospective-job-'+name)
    for schema in schemas.values():
        with psycopg.connect(uri,autocommit=True) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        settings.database_url=scoped(schema);settings.feed='sip';db.migrate()
        with db.connection() as c:
            source=uuid5(NAMESPACE_URL,'prospective-source-'+name);run=uuid5(NAMESPACE_URL,'prospective-baseline-run-'+name)
            c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/prospective',%s,%s,'{}')",(source,digest(name),cutoff))
            def insert(table,records):
                if not records:return
                columns=list(records[0]);query=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                with c.cursor() as cursor:cursor.executemany(query,[[r[k] for k in columns] for r in records])
            insert('daily_bars',[{**r,'source_id':source,'content_hash':digest(r)} for rows in stream.values() for r in rows])
            insert('information_versions',[{**r,'symbols':jsonb(r['symbols']),'payload':jsonb(r['payload']),'source_id':source} for r in context['information']])
            insert('information_fetches',[{**r,'id':uuid5(NAMESPACE_URL,'prospective-coverage-'+name),'symbols':jsonb(r['symbols']),'detail':jsonb({})} for r in context['coverage']])
            facts=copy.deepcopy(context['facts']);facts += [{**r,'id':r['id']+10000,'symbol':'AXON','value':r['value']*2+3} for r in context['facts']]
            insert('financial_facts',[{**r,'form':'10-Q','filed_date':r['available_at'][:10],'source_id':source,'source_url':'https://example.invalid/facts','content_hash':digest(r)} for r in facts])
            events=copy.deepcopy(context['events']);events += [{**r,'id':r['id']+'-AXON','symbol':'AXON'} for r in context['events']]
            insert('filing_events',[{**r,'accession':r['id'],'source_id':source,'payload':jsonb({})} for r in events])
            if mode!='worker':
                c.execute("INSERT INTO forecast_runs VALUES(%s,%s,%s,%s,'completed','{}')",(run,origin,instant,instant))
                c.execute("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,'fixture','sip','split','{}')",('f'*64,))
                for symbol in ['MSI','AXON']:
                    if symbol=='MSI' and name=='no-paired-reference':continue
                    for h in [1,5]:
                        c.execute("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES(%s,%s,%s,%s,%s,%s,%s,'2026-11-19',%s,'sip','split',%s,%s,'historical-baseline-v1','fixture',0.001,0.6,-0.03,0.03,300,'{}')",(uuid5(NAMESPACE_URL,name+symbol+str(h)),run,symbol,origin,h,instant,cutoff,'2026-11-19' if h==1 else '2026-11-25',origin,'f'*64))
            if name!='not-enrolled':
                c.execute("INSERT INTO model_versions(id,kind,specification) VALUES(%s,'experimental_shadow',%s)",(prospective.MODEL_ID,jsonb(spec)))
                c.execute('INSERT INTO shadow_protocols VALUES(%s,%s,%s,%s,%s,%s)',(prospective.PROTOCOL_ID,cutoff-timedelta(days=1),date(2026,11,19) if name=='not-started' else origin,prospective.MODEL_ID,jsonb(spec),artifact_hash))
                for a in frozen:c.execute('INSERT INTO shadow_artifacts VALUES(%s,%s,%s,%s)',(prospective.PROTOCOL_ID,a['symbol'],a['horizon'],jsonb(a['artifact'])))
            if name in ['database-failure','worker-database-failure']:
                c.execute("CREATE FUNCTION fail_prospective_forecast() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.model_id='information-shadow-v3' AND NEW.symbol='AXON' AND NEW.horizon=5 THEN RAISE EXCEPTION 'fixture second-symbol failure'; END IF; RETURN NEW; END $$")
                c.execute('CREATE TRIGGER fixture_failure BEFORE INSERT ON forecasts FOR EACH ROW EXECUTE FUNCTION fail_prospective_forecast()')
            if name=='worker-terminal-recovery':
                c.execute("CREATE FUNCTION fail_issue_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status IN ('completed','skipped') THEN RAISE EXCEPTION 'fixture terminal completion failure'; END IF; RETURN NEW; END $$")
                c.execute('CREATE TRIGGER fixture_job_failure BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION fail_issue_completion()')
    settings.database_url=scoped(schemas['python']);projections=[];results=[]
    def gate(projected_bytes=32*1024**2):
        projections.append(projected_bytes)
        if name=='worker-resource-denied':raise ValueError('Fixture resource exhausted')
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings,'feed','sip'))
        for module in [prospective,forecasting]:stack.enter_context(patch.object(module,'now',return_value=at))
        stack.enter_context(patch.object(jobs,'resource_check',side_effect=gate))
        repeat=8 if mode=='concurrent' else 2 if name in ['repeat','worker-repeat','worker-terminal-recovery'] else 1
        for attempt in range(repeat):
            try:
                if mode!='worker':result=prospective.issue_shadow()
                else:
                    id=uuid5(NAMESPACE_URL,str(job_id)+str(attempt))
                    if name=='worker-terminal-recovery' and attempt==1:
                        with db.connection() as c:c.execute('DROP TRIGGER fixture_job_failure ON jobs')
                    else:
                        with patch.object(jobs,'uuid4',return_value=id):jobs.enqueue('issue',{})
                    result=jobs.run_once()
                with db.connection() as c:result=normalize(c,result)
                results.append({'result':result})
            except Exception as error:results.append({'error':{'kind':type(error).__name__,'message':str(error) if isinstance(error,ValueError) else None}})
    with db.connection() as c:
        snapshot={key:normalize(c,c.execute(query).fetchall()) for key,query in queries.items()}
    cases.append({'name':name,'mode':mode,'schema':schemas['native'],'instant':at,'results':results,'snapshot':snapshot,'projections':projections,'job_ids':[str(uuid5(NAMESPACE_URL,str(job_id)+str(i))) for i in range(repeat)]})
    print(name,'captured',flush=True)
path=Path(os.environ['RUST_PROSPECTIVE_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'cases':cases,'queries':queries,'legacy_implementation_hash':tuning.implementation_hash()}))
print(json.dumps({'scenarios':len(cases),'direct':sum(c['mode']=='direct' for c in cases),'actual_worker':sum(c['mode']=='worker' for c in cases),'runtime_python':False}))
