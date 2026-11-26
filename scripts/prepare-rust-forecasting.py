"""Seed independent reference/public schemas; capture unchanged issuance/outcome workflows."""
import json,os,sys
from datetime import UTC,date,datetime,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from uuid import uuid5,NAMESPACE_URL
import psycopg
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,forecasting,vision
from intelligence.config import settings
from intelligence.calendar import schedule
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
uri=os.environ['RUST_TEST_DATABASE_URL'];u=urlsplit(uri)
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
with psycopg.connect(uri,autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
origin=date(2026,11,18);cutoff=datetime(2026,11,19,1,tzinfo=UTC);issued=cutoff+timedelta(minutes=30)
calendar=schedule(origin-timedelta(days=600),origin+timedelta(days=35))
days=[d for d in calendar if d<=origin][-300:]
source=uuid5(NAMESPACE_URL,'rust-forecast-source');run=uuid5(NAMESPACE_URL,'rust-forecast-seed-run')
for schema in ['python_reference','public']:
    settings.database_url=scoped(schema);settings.feed='sip';db.migrate()
    with db.connection() as c:
        c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/forecast',%s,%s,'{}')",(source,digest('fixture'),cutoff))
        c.execute("INSERT INTO model_versions(id,kind,specification) VALUES('outcome-fixture','fixture','{}')")
        for symbol in ['MISS','FEED','DONE']:c.execute('INSERT INTO securities VALUES(%s,%s,NULL,false)',(symbol,symbol))
        def bar(symbol,day,opening,closing,acquired=None,basis=origin):
            available=calendar[day]['close']+timedelta(minutes=15)
            c.execute("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES(%s,%s,'sip','split',%s,%s,%s,%s,%s,1000000,%s,%s,%s,%s)",(symbol,day,basis,Decimal(opening),max(Decimal(opening),Decimal(closing))+1,min(Decimal(opening),Decimal(closing))-1,Decimal(closing),available,acquired or available+timedelta(seconds=1),source,digest([symbol,str(day),opening,closing,str(acquired),str(basis)])))
        for symbol in ['MSI','AXON','SPY']:
            for index,day in enumerate(days[-250:] if symbol=='SPY' else days):
                if symbol=='AXON' and day==days[-3]:continue
                bar(symbol,day,str(100+index/8),str(100+index/8+(index%5-2)/16))
        bar('MSI',origin,'80','200',cutoff+timedelta(seconds=1))
        for symbol in ['MSI','SPY','FEED','DONE']:
            bar(symbol,date(2026,11,19),'100','102')
        bar('MSI',date(2026,11,19),'100.0000000000000000001','100.0000000000000000002',datetime(2026,11,26,tzinfo=UTC))
        for symbol in ['MSI','MISS']:bar(symbol,date(2026,11,25),'101','103')
        bar('AXON',date(2026,11,25),'101','104',basis=date(2026,11,20))
        c.execute("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,'fixture','sip','split','{}')",('f'*64,))
        c.execute("INSERT INTO forecast_runs VALUES(%s,%s,%s,%s,'completed','{}')",(run,origin,issued-timedelta(minutes=10),issued))
        for i,symbol in enumerate(['MISS','FEED','DONE']):
            fid=uuid5(NAMESPACE_URL,'forecast-fixture-'+symbol)
            c.execute("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES(%s,%s,%s,%s,1,%s,%s,'2026-11-19','2026-11-19',%s,'split',%s,%s,'outcome-fixture','fixture',0.001,0.6,-0.03,0.03,300,'{}')",(fid,run,symbol,origin,issued-timedelta(minutes=10),cutoff,'iex' if symbol=='FEED' else 'sip',origin,'f'*64))
            if symbol=='DONE':
                entry=c.execute("SELECT id FROM daily_bars WHERE symbol='DONE'").fetchone()['id'];oid=uuid5(NAMESPACE_URL,'existing-outcome')
                c.execute("INSERT INTO forecast_outcomes VALUES(%s,%s,%s,0.01,NULL,%s,%s,'{}',NULL)",(oid,fid,issued,entry,entry))
                c.execute("INSERT INTO forecast_outcomes VALUES(%s,%s,%s,0.02,NULL,%s,%s,'{}',%s)",(uuid5(NAMESPACE_URL,'existing-correction'),fid,issued,entry,entry,oid))
        model=c.execute('SELECT id FROM vision_models ORDER BY id LIMIT 1').fetchone()['id']
        for label,symbol,created in [('valid','MSI',issued),('late','MSI',datetime(2026,11,19,14,30,tzinfo=UTC)),('basis-gap','AXON',issued)]:
            c.execute('INSERT INTO chart_detections VALUES(%s,%s,%s,%s,%s,\'fixture\',%s,%s,%s,%s)',(uuid5(NAMESPACE_URL,'detection-outcome-'+label),symbol,model,created,issued,'a'*64,'b'*64,'c'*64,jsonb({'manifest':{'last_session':origin}})))
settings.database_url=scoped('python_reference')
operations=[('issue','2026-11-19T01:29:59Z'),('issue','2026-11-19T01:30:00Z'),('issue','2026-11-19T01:30:00Z'),('issue','2026-11-19T14:30:00Z'),
 ('resolve','2026-11-19T21:14:59Z'),('detection','2026-11-19T21:14:59Z'),('resolve','2026-11-19T21:15:00Z'),('detection','2026-11-19T21:15:00Z'),
 ('resolve','2026-11-19T21:15:00Z'),('detection','2026-11-19T21:15:00Z'),('resolve','2026-11-25T21:15:00Z'),('detection','2026-11-25T21:15:00Z')]
cases=[]
for operation,text in operations:
    instant=datetime.fromisoformat(text.replace('Z','+00:00'))
    with patch.object(forecasting,'now',return_value=instant),patch.object(vision,'now',return_value=instant):
        result=forecasting.issue_forecasts() if operation=='issue' else forecasting.resolve_outcomes() if operation=='resolve' else vision.resolve_detection_outcomes()
    if operation=='issue':
        for row in result['issued']:row.pop('id')
    elif operation=='resolve':
        with db.connection() as c:
            result['resolved']=[c.execute("SELECT symbol||'/'||horizon||'/'||model_id AS key FROM forecasts WHERE id=%s",(fid,)).fetchone()['key'] for fid in result['resolved']]
    cases.append({'operation':operation,'instant':instant,'result':result})
queries={
 'manifests':"SELECT id,kind,feed,adjustment,payload FROM dataset_manifests ORDER BY id",
 'forecasts':"SELECT to_jsonb(f)-'id'-'run_id' AS record FROM forecasts f ORDER BY symbol,horizon,model_id",
 'runs':"SELECT origin_session,started_at,status,detail FROM forecast_runs ORDER BY started_at",
 'outcomes':"SELECT f.symbol||'/'||f.horizon||'/'||f.model_id AS key,o.resolved_at,o.realized_return,o.spy_return,o.entry_bar_id,o.exit_bar_id,o.provenance,o.supersedes FROM forecast_outcomes o JOIN forecasts f ON f.id=o.forecast_id ORDER BY key,o.realized_return",
 'detections':"SELECT detection_id,horizon,realized_return,resolved_at,provenance FROM detection_outcomes ORDER BY detection_id,horizon"}
with db.connection() as c:snapshot={name:c.execute(sql).fetchall() for name,sql in queries.items()}
path=Path(os.environ['RUST_FORECAST_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'cases':cases,'snapshot':snapshot,'queries':queries}))
print('Captured twelve original issuance/resolution operations and five persisted evidence families')
