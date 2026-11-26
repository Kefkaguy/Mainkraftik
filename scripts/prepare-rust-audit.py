"""Original saved-head audit on synthetic sealed histories in disposable local schemas."""
import json,os,runpy,sys
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
oracle=Path(os.environ['RUST_AUDIT_ORACLE'])
os.environ['RUST_EVALUATION_ORACLE']=str(oracle.with_suffix('.evaluation.json'))
runpy.run_path(str(ROOT/'scripts/prepare-rust-evaluation.py'),run_name='__main__')
from intelligence import db,experiments
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
u=urlsplit(os.environ['RUST_TEST_DATABASE_URL'])
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
sentinel={'immutable':'original audit sentinel'};sentinel_id=digest(sentinel)
for schema in ['python_reference','public']:
 settings.database_url=scoped(schema)
 with db.connection() as c:
  c.execute("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,'fixture','sip','split','{}')",('f'*64,))
  c.execute("INSERT INTO evaluation_reports(id,model_id,symbol,created_at,dataset_id,status,protocol,report) VALUES(%s,'technical-v1','MSI','2000-01-01',%s,'inconclusive','{}',%s)",(uuid5(NAMESPACE_URL,'old-original-audit-fixture'),'f'*64,jsonb({'horizons':[]})))
  c.execute('INSERT INTO research_audits(id,report) VALUES(%s,%s)',(sentinel_id,jsonb(sentinel)))
settings.database_url=scoped('public')
with db.connection() as c:
 c.execute("INSERT INTO dataset_manifests SELECT * FROM python_reference.dataset_manifests WHERE id<>%s",('f'*64,))
 c.execute("INSERT INTO evaluation_reports SELECT * FROM python_reference.evaluation_reports WHERE created_at>'2000-01-02'")
 c.execute('INSERT INTO research_plans SELECT * FROM python_reference.research_plans')
settings.database_url=scoped('python_reference')
expected=experiments.audit_original();again=experiments.audit_original();assert again==expected
settings.database_url=scoped('public')
queries={
 'reports':"SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY id)::text) FROM evaluation_reports r",
 'manifests':"SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY id)::text) FROM dataset_manifests r",
 'bars':"SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY id)::text) FROM daily_bars r",
 'plans':"SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY id)::text) FROM research_plans r",
}
with db.connection() as c:
 c.execute("SET LOCAL TIME ZONE 'UTC'")
 c.execute('INSERT INTO research_audits SELECT * FROM python_reference.research_audits WHERE id=%s',(expected['id'],))
 before={key:c.execute(query).fetchone()['md5'] for key,query in queries.items()}
 original_audits=c.execute('SELECT id,report FROM research_audits ORDER BY id').fetchall()
 snapshots=[]
 for row in c.execute("SELECT DISTINCT ON(symbol) * FROM evaluation_reports WHERE model_id='technical-v1' ORDER BY symbol,created_at DESC"):
  manifest=c.execute('SELECT payload FROM dataset_manifests WHERE id=%s',(row['dataset_id'],)).fetchone()['payload']
  bars=c.execute('SELECT * FROM daily_bars WHERE id=ANY(%s) ORDER BY symbol,session_date',(manifest['bar_ids'],)).fetchall()
  snapshots.append({'id':str(row['id']),'symbol':row['symbol'],'report':row['report'],'bars':[b for b in bars if b['symbol']==row['symbol']],'spy':[b for b in bars if b['symbol']=='SPY']})
with oracle.open('x',encoding='utf-8') as f:f.write(canonical({'synthetic_only':True,'expected':expected,'original_audits':original_audits,'before':before,'queries':queries,'snapshots':snapshots}))
print('Captured original saved-head reconstruction twice; sealed synthetic history, no new candidate fitting')
