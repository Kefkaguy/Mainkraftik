"""Original causal-feature DB oracle; disposable local schemas, synthetic data only."""
import copy,json,os,sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
from uuid import NAMESPACE_URL,uuid5
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import db,point_features as p
from intelligence.config import settings
from intelligence.storage import jsonb
from intelligence.util import canonical,digest
uri=os.environ['RUST_TEST_DATABASE_URL'];u=urlsplit(uri)
assert u.hostname in ('localhost','127.0.0.1') and u.port==5543 and u.path.startswith('/intelligence_test_rust_')
def scoped(schema):return urlunsplit(u._replace(query=urlencode([*parse_qsl(u.query),('options','-csearch_path='+schema)])))
with psycopg.connect(uri,autocommit=True) as c:c.execute('CREATE SCHEMA python_reference')
fixture=json.loads((ROOT/'apps/backend-rust/tests/fixtures/point-features.json').read_text())
context=fixture['contexts'][0];cutoff=datetime.fromisoformat(fixture['information'][0]['cutoff'])
source=uuid5(NAMESPACE_URL,'rust-point-feature-source')
for schema in ['python_reference','public']:
    settings.database_url=scoped(schema);settings.feed='sip';db.migrate()
    with db.connection() as c:
        c.execute("INSERT INTO source_documents VALUES(%s,'fixture','https://example.invalid/point-features',%s,%s,'{}')",(source,digest('synthetic-point-features'),cutoff))
        def insert(table,record):
            c.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,record)),sql.SQL(',').join(sql.Placeholder() for _ in record)),list(record.values()))
        for rows in context['streams'].values():
            for r in rows:insert('daily_bars',{**r,'source_id':source,'content_hash':digest(r)})
        # A causally available correction acquired later must affect reconstruction,
        # but must never replace the price known at prospective issuance.
        correction={**context['streams']['MSI'][-1],'id':900001,'close':'125','high':'126','low':'100','acquired_at':cutoff+timedelta(days=2),'source_id':source}
        insert('daily_bars',{**correction,'content_hash':digest(correction)})
        # A later adjustment basis is valid only without the earlier cutoff.
        for i,r in enumerate(context['streams']['XLK']):
            later={**r,'id':910000+i,'adjustment_asof':'2026-11-19','available_at':cutoff+timedelta(days=1),'acquired_at':cutoff+timedelta(days=2),'source_id':source}
            insert('daily_bars',{**later,'content_hash':digest(later)})
        for r in context['information']:
            insert('information_versions',{**r,'symbols':jsonb(r['symbols']),'payload':jsonb(r['payload']),'source_id':source})
        for i,r in enumerate(context['coverage']):
            insert('information_fetches',{**r,'id':uuid5(NAMESPACE_URL,f'point-coverage-{i}'),'symbols':jsonb(r['symbols']),'detail':jsonb({})})
        facts=copy.deepcopy(context['facts'])
        facts += [{**r,'id':r['id']+10000,'symbol':'AXON','value':r['value']*2+3} for r in context['facts']]
        for r in facts:
            insert('financial_facts',{**r,'form':'10-Q','filed_date':r['available_at'][:10],'source_id':source,'source_url':'https://example.invalid/facts','content_hash':digest(r)})
        events=copy.deepcopy(context['events'])
        events += [{**r,'id':r['id']+'-AXON','symbol':'AXON'} for r in context['events']]
        for r in events:insert('filing_events',{**r,'accession':r['id'],'source_id':source,'payload':jsonb({})})
settings.database_url=scoped('python_reference');cases=[]
views=[(None,False),(None,True),(cutoff,False),(cutoff,True),(cutoff-timedelta(days=10),False),(cutoff-timedelta(days=10),True)]
for cut,observed in views:
    with db.connection() as c:
        streams,info,coverage,facts,events=p.load_context(c,cut,observed)
        selection={s:{'ids':[r['id'] for r in rows],'last_close':str(rows[-1]['close']) if rows else None,'basis':rows[-1]['adjustment_asof'] if rows else None} for s,rows in streams.items()}
        for symbol in ['MSI','AXON']:
            for latest in [False,True]:
                panel=p.build_features(streams,symbol,info,coverage,[r for r in facts if r['symbol']==symbol],[r for r in events if r['symbol']==symbol],observed,latest)
                mode='prospective' if observed else 'historical'
                ids=p.persist_panel(c,symbol,panel,mode)
                assert p.persist_panel(c,symbol,panel,mode)==ids
                cases.append({'cutoff':cut,'observed':observed,'symbol':symbol,'latest_only':latest,'selection':selection,'rows':panel,'ids':ids})
query="SELECT to_jsonb(r)-'created_at' FROM feature_snapshots r ORDER BY id"
with db.connection() as c:snapshot=[r['?column?'] for r in c.execute(query).fetchall()]
path=Path(os.environ['RUST_POINT_ORACLE']);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x',encoding='utf-8') as f:f.write(canonical({'cases':cases,'snapshot':snapshot,'query':query}))
print(json.dumps({'cases':len(cases),'rows':sum(len(c['rows']) for c in cases),'unique_snapshots':len(snapshot),'exact_snapshot_ids':True}))
