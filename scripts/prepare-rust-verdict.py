"""Unchanged prospective verdict and monitor worker; disposable synthetic schemas only."""
import json,os,sys
from contextlib import ExitStack
from datetime import UTC,date,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from unittest.mock import patch
from uuid import NAMESPACE_URL,uuid5
import numpy as np,psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,jobs,monitoring,prospective
from intelligence.calendar import schedule,cutoff_for
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
u=urlsplit(os.environ['RUST_TEST_DATABASE_URL']);assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
instant=datetime(2026,11,20,22,tzinfo=UTC);calendar=schedule(date(2025,1,1),date(2026,12,10));days=[d for d in calendar if d<=date(2026,11,18)][-129:]
specs=[('not-enrolled','direct'),('empty','direct'),('short125','direct'),('unresolved','direct'),('missing-baseline-truth','direct'),('unpaired','direct'),('ready-readonly','direct'),('passing','direct'),('failing','direct'),('no-edge','direct'),('integrity-failure','direct'),('integrity-tolerance','direct'),('first126-only','direct'),('latest-correction','direct'),('near-zero','direct'),('stored-verdict','direct'),('verdict-failure','direct'),('concurrent','concurrent'),('worker-fresh','worker'),('worker-stale','worker'),('worker-future','worker'),('worker-resource-denied','worker'),('worker-terminal-recovery','worker')]
queries={'verdicts':"SELECT protocol_id,status,report FROM shadow_verdicts ORDER BY protocol_id",'protocols':"SELECT * FROM shadow_protocols ORDER BY id",'events':"SELECT protocol_id,event,payload FROM shadow_events ORDER BY id",'jobs':"SELECT id,kind,payload,status,attempts,result,error FROM jobs ORDER BY id",'job_events':"SELECT job_id,event,detail FROM job_events ORDER BY id",'forecast_fingerprint':"SELECT count(*) AS n,md5(coalesce(jsonb_agg(to_jsonb(f) ORDER BY id)::text,'')) AS fingerprint FROM forecasts f",'outcome_fingerprint':"SELECT count(*) AS n,md5(coalesce(jsonb_agg(to_jsonb(f) ORDER BY id)::text,'')) AS fingerprint FROM forecast_outcomes f",'bars_fingerprint':"SELECT count(*) AS n,md5(coalesce(jsonb_agg(to_jsonb(f) ORDER BY id)::text,'')) AS fingerprint FROM daily_bars f"}
for key,table,columns in [('forecast_fingerprint','forecasts',['issued_at','data_cutoff']),('outcome_fingerprint','forecast_outcomes',['resolved_at']),('bars_fingerprint','daily_bars',['available_at','acquired_at'])]:
 value='to_jsonb(f)'
 for column in columns:value=f"jsonb_set({value},'{{{column}}}',to_jsonb(extract(epoch from f.{column})))"
 queries[key]=f"SELECT count(*) AS n,md5(coalesce(jsonb_agg({value} ORDER BY id)::text,'')) AS fingerprint FROM {table} f"
cases=[]
for index,(name,mode) in enumerate(specs):
 schemas={kind:f'{kind}_verdict_{index:02d}' for kind in ['python','native']};n=0 if name in ['not-enrolled','empty'] else 125 if name=='short125' else 129 if name=='first126-only' else 126
 for schema in schemas.values():
  with psycopg.connect(urlunsplit(u),autocommit=True) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  settings.database_url=scoped(schema);db.migrate()
  with db.connection() as c:
   source=uuid5(NAMESPACE_URL,'verdict-source-'+name);run=uuid5(NAMESPACE_URL,'verdict-run-'+name)
   c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/verdict',%s,%s,'{}')",(source,digest(name),instant))
   # Coherent observed prices for descriptive monitoring; no executable quote or trade.
   bars=[]
   for j,s in enumerate(['MSI','AXON','SPY']):
    for i,d in enumerate([day for day in calendar if day<=instant.date()][-80:]):
     close=100+j*32+i/8
     bars.append((j*1000+i+1,s,d,close,close+1,close-1,close,calendar[d]['close']+timedelta(minutes=15),source,digest([s,str(d),name])))
   with c.cursor() as cur:cur.executemany("INSERT INTO daily_bars(id,symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES(%s,%s,%s,'sip','split','2026-11-20',%s,%s,%s,%s,1000000,%s,'2026-11-20T21:16:00Z',%s,%s)",bars)
   c.execute("INSERT INTO model_versions(id,kind,specification) VALUES(%s,'experimental_shadow','{}')",(prospective.MODEL_ID,))
   if name!='not-enrolled':c.execute("INSERT INTO shadow_protocols VALUES(%s,%s,%s,%s,%s,%s)",(prospective.PROTOCOL_ID,instant-timedelta(days=400),days[0],prospective.MODEL_ID,jsonb({'minimum_dates':126,'promotion':'none','fixture':name}),'f'*64))
   c.execute("INSERT INTO forecast_runs VALUES(%s,'2026-11-18',%s,%s,'completed','{}')",(run,instant,instant))
   c.execute("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,'fixture','sip','split','{}')",('f'*64,))
   forecasts=[];outcomes=[]
   for i,d in enumerate(days[:n]):
    for j,s in enumerate(['MSI','AXON']):
     for h in [1,5]:
      y=(((i%7)+1)/1024+j/8192)*(1 if i%3 else -1);entry=[day for day in calendar if day>d][0];target=[day for day in calendar if day>d][h-1]
      for model in ['historical-baseline-v1',prospective.MODEL_ID]:
       if name=='unpaired' and i==125 and j==1 and h==5 and model=='historical-baseline-v1':continue
       identity=uuid5(NAMESPACE_URL,name+str(d)+s+str(h)+model);baseline=model=='historical-baseline-v1'
       pred=0 if baseline or name=='no-edge' else -y if name=='failing' else y*.75
       p=.5 if baseline or name=='no-edge' else (.25 if y>0 else .75) if name=='failing' else .75 if y>0 else .25
       if name=='near-zero':
        pred=y*(1-1e-12+(0 if baseline else 1e-15));p0=.75 if y>0 else .25;p=p0 if baseline else float(np.nextafter(p0,1 if y>0 else 0))
       forecasts.append((identity,run,s,d,h,cutoff_for(d)+timedelta(minutes=30),cutoff_for(d),entry,target,'f'*64,model,pred,p))
       if (name=='unresolved' and i==125 and not baseline) or (name=='missing-baseline-truth' and i==125 and baseline) or (name=='first126-only' and i>=126):continue
       truth=y+(0.001 if name=='integrity-failure' and i==0 and baseline else 5e-11 if name=='integrity-tolerance' and i==0 and baseline else 0)
       if name=='latest-correction':truth=-y
       oid=uuid5(NAMESPACE_URL,str(identity)+'outcome');outcomes.append((oid,identity,instant-timedelta(minutes=30),truth,1,1,None))
       if name=='latest-correction':outcomes.append((uuid5(NAMESPACE_URL,str(identity)+'corrected'),identity,instant-timedelta(minutes=15),y,1,1,oid))
   with c.cursor() as cur:
    cur.executemany("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'sip','split','2026-11-20',%s,%s,'fixture',%s,%s,-0.03,0.03,300,'{}')",forecasts)
    cur.executemany("INSERT INTO forecast_outcomes(id,forecast_id,resolved_at,realized_return,entry_bar_id,exit_bar_id,supersedes,provenance) VALUES(%s,%s,%s,%s,%s,%s,%s,'{}')",outcomes)
   if name=='stored-verdict':c.execute("INSERT INTO shadow_verdicts(protocol_id,status,report) VALUES(%s,'failed','{\"status\":\"failed\",\"fixture\":\"original stored verdict\"}')",(prospective.PROTOCOL_ID,))
   heartbeat=instant+timedelta(days=1) if name=='worker-future' else instant-timedelta(minutes=5) if name=='worker-stale' else instant-timedelta(minutes=4,seconds=59)
   c.execute("INSERT INTO worker_status VALUES('research-worker',%s,'{\"status\":\"fixture\"}')",(heartbeat,))
   for j in range(3):c.execute("INSERT INTO jobs(id,kind,payload,status,error,created_at,completed_at) VALUES(%s,'monitor','{}','failed','fixture failure',%s,%s)",(uuid5(NAMESPACE_URL,name+'priorjob'+str(j)),instant-timedelta(hours=j+1),instant-timedelta(hours=j+1)))
   if name=='verdict-failure':
    c.execute("CREATE FUNCTION fail_verdict() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture verdict commit failure'; END $$")
    c.execute('CREATE TRIGGER fixture_verdict_failure BEFORE INSERT ON shadow_verdicts FOR EACH ROW EXECUTE FUNCTION fail_verdict()')
   if name=='worker-terminal-recovery':
    c.execute("CREATE FUNCTION fail_monitor_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='completed' THEN RAISE EXCEPTION 'fixture terminal failure'; END IF; RETURN NEW; END $$")
    c.execute('CREATE TRIGGER fixture_job_failure BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION fail_monitor_completion()')
 settings.database_url=scoped(schemas['python']);results=[];projections=[]
 def gate(projected_bytes=32*1024**2):
  projections.append(projected_bytes)
  if name=='worker-resource-denied':raise ValueError('Fixture resource exhausted')
 repeats=8 if mode=='concurrent' else 2 if name in ['passing','verdict-failure','worker-terminal-recovery'] else 1
 job_ids=[uuid5(NAMESPACE_URL,name+'job'+str(i)) for i in range(repeats)]
 with ExitStack() as stack:
  stack.enter_context(patch.object(monitoring,'now',return_value=instant));stack.enter_context(patch.object(jobs,'resource_check',side_effect=gate));stack.enter_context(patch.object(settings,'feed','sip'))
  for attempt in range(repeats):
   try:
    if name=='verdict-failure' and attempt==1:
     with db.connection() as c:c.execute('DROP TRIGGER fixture_verdict_failure ON shadow_verdicts')
    if mode=='worker':
     if name=='worker-terminal-recovery' and attempt==1:
      with db.connection() as c:c.execute('DROP TRIGGER fixture_job_failure ON jobs')
     else:
      with patch.object(jobs,'uuid4',return_value=job_ids[attempt]):jobs.enqueue('monitor',{})
     value=jobs.run_once()
    else:value=prospective.progress_and_verdict(finalize=name!='ready-readonly')
    result={'result':value}
   except Exception as e:result={'error':{'kind':type(e).__name__,'message':str(e) if isinstance(e,ValueError) else None}}
   with db.connection() as c:result['verdict_count']=c.execute('SELECT count(*) n FROM shadow_verdicts').fetchone()['n']
   results.append(result)
 with db.connection() as c:
  snapshot={key:c.execute(q).fetchall() for key,q in queries.items()};timezone=c.execute('SHOW TimeZone').fetchone()['TimeZone']
 cases.append({'name':name,'mode':mode,'schema':schemas['native'],'instant':instant,'finalize':name!='ready-readonly','results':results,'snapshot':snapshot,'projections':projections,'job_ids':job_ids,'python_database_timezone':timezone})
 print(name,'captured',flush=True)
p=Path(os.environ['RUST_VERDICT_ORACLE']);p.parent.mkdir(parents=True,exist_ok=True)
with p.open('x',encoding='utf-8') as f:f.write(canonical({'cases':cases,'queries':queries}))
print(json.dumps({'scenarios':len(cases),'actual_workers':sum(c['mode']=='worker' for c in cases)}))
