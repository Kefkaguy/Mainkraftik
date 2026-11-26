"""Capture original evaluation persistence using synthetic candles in disposable schemas."""
import json,os,sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from uuid import NAMESPACE_URL,uuid5
import psycopg
from threadpoolctl import threadpool_limits
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,research
from intelligence.config import settings
from intelligence.calendar import schedule
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
u=urlsplit(os.environ['RUST_TEST_DATABASE_URL'])
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
with psycopg.connect(urlunsplit(u),autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
fixture=json.loads((ROOT/'apps/backend-rust/tests/fixtures/research-evaluation.json').read_text())
case=next(c for c in fixture['cases'] if c['name']=='complete')
calendar=schedule(datetime.fromisoformat(case['bars'][0]['session_date']).date(),datetime.fromisoformat(case['bars'][-1]['session_date']).date())
instant=datetime(2026,11,20,22,tzinfo=UTC);source=uuid5(NAMESPACE_URL,'native-evaluation-source')
for schema in ['python_reference','public']:
 settings.database_url=scoped(schema);settings.feed='sip';db.migrate()
 with db.connection() as c:
  c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/evaluation',%s,%s,'{}')",(source,digest('synthetic-only'),instant))
  rows=[]
  for symbol,key in [('MSI','bars'),('SPY','spy')]:
   for row in case[key]:
    day=datetime.fromisoformat(row['session_date']).date();available=calendar[day]['close']+timedelta(minutes=15)
    rows.append((symbol,day,row['adjustment_asof'],row['open'],row['high'],row['low'],row['close'],row['volume'],available,instant,source,digest([symbol,row])))
  with c.cursor() as cur:cur.executemany("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES(%s,%s,'sip','split',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",rows)
settings.database_url=scoped('python_reference')
with threadpool_limits(limits=1):fresh=research.run_evaluation('MSI')
repeat=research.run_evaluation('MSI')
with db.connection() as c:
 manifest=c.execute('SELECT id,payload FROM dataset_manifests').fetchone()
 c.execute("INSERT INTO research_plans(id,dataset_id,protected_start,specification,code_hash) VALUES('sealed',%s,'2020-01-01','{}',%s)",(manifest['id'],'a'*64))
sealed=research.run_evaluation('MSI')
try:research.run_evaluation('AXON');raise AssertionError('sealed missing report accepted')
except ValueError as e:missing=str(e)
path=Path(os.environ['RUST_EVALUATION_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'synthetic_only':True,'fresh':fresh,'repeat':repeat,'sealed':sealed,'missing':missing,'manifest':manifest}))
print('Captured original fresh, repeat, sealed and missing-report evaluation behavior; synthetic data only')
