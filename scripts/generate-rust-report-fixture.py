"""Original read-only report/API projections and legacy SQLite rows, fully offline."""
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from chart_intelligence import store,controller,api
from intelligence import lab_api
from intelligence.util import canonical
from paperpilot import lab_registry

with tempfile.TemporaryDirectory(prefix='mainkraft-report-oracle-') as temporary:
    project=Path(temporary)
    local=project/'.local'
    chart=local/'chart-intelligence'
    phase7=local/'phase7'
    lab=local/'strategy-lab'
    report={'status':'NO_VALIDATED_ADVANTAGE','predictions':[{'features':'PRIVATE'}],'model_id':'frozen-test-only','calibration':{'accuracy':0.49}}
    key=store.put('report',report,chart);store.publish(key,chart)
    phase={'status':'FAILED','winner':None,'audit':{'candidate':{'prediction':{'accuracy':0.51},'scenarios':{'base_30min':{'net_return':-0.01,'trade_count':32,'orders':['PRIVATE'],'trades':['PRIVATE'],'daily_net_pnl':['PRIVATE']}}}},'acceptance':{'accepted':False},'audit_classification':'EXPOSED_DO_NOT_REUSE','prospective':{'status':'WAITING'}}
    key=store.put('report',phase,phase7);store.publish(key,phase7)
    with patch.object(lab_registry,'LAB',lab):
        lab_registry.publish({'status':'failed','coverage':['PRIVATE'],'news':['PRIVATE'],'monthly_walk_forward':{'summary':{'return':-0.02},'rows':['PRIVATE']},'frozen_comparison':{'cash':{'summary':{'return':0},'trades':['PRIVATE']},'note':'No validated advantage'}})
    database=controller.connection(chart)
    for i in range(103):controller.append(database,f'prediction-{i}','prediction',{'index':i,'features':[i,0.1],'return':i/10000})
    for i in range(2):controller.append(database,f'outcome-{i}','outcome',{'index':i,'features':[i],'return':-0.01})
    controller.append(database,'run','controller_run',{'issued':1})
    controller.append(database,'proposal','offline_proposal',{'promotion_authority':False})
    database.commit()
    rows=[{'key':r['key'],'kind':r['kind'],'payload':json.loads(r['payload'])} for r in database.execute('SELECT * FROM evidence ORDER BY rowid')]
    schema=';\n'.join(r[0] for r in database.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))+';'
    database.close()
    with ExitStack() as stack:
        for module,name,value in [(api,'ROOT',project),(store,'STORE',chart),(lab_registry,'LAB',lab)]:stack.enter_context(patch.object(module,name,value))
        expected={'phase7':api.phase7_status(),'chart':api.research_status(),'lab':lab_api.results()}
    files={p.relative_to(local).as_posix():p.read_text(encoding='utf-8') for p in local.rglob('*.json')}
    # Publication timestamps are not used by any read contract; normalize only
    # the synthetic fixture's lab pointer to keep regeneration deterministic.
    pointer=json.loads(files['strategy-lab/latest.json']);pointer.pop('published',None)
    files['strategy-lab/latest.json']=canonical(pointer)
    fixture={'files':files,'sqlite_schema':schema,'evidence':rows,'expected':expected}
    blob=canonical(fixture).encode()
    path=ROOT/'apps/backend-rust/tests/fixtures/report-api-parity.json'
    if path.exists():assert path.read_bytes()==blob,'Frozen report oracle differs'
    else:path.write_bytes(blob)
print('Saved original report projections, immutable IDs, and legacy SQLite evidence/status oracle')
