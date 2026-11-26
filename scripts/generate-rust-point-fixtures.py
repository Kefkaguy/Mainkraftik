"""Freeze original causal research features; synthetic prices and facts only."""
import argparse,copy,hashlib,json,platform,random,sys
from datetime import UTC,date,datetime,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows')
args=parser.parse_args()
assert sys.platform==('linux' if args.reference_platform=='linux' else 'win32'),'Reference generation must execute on its declared platform'
suffix='-linux' if args.reference_platform=='linux' else ''
from intelligence import point_features as p
from intelligence.calendar import schedule,cutoff_for
from intelligence.financials import TAGS
from intelligence.util import canonical,digest
days=list(schedule(date(2026,5,1),date(2026,11,18)))[-86:];cutoff=cutoff_for(days[-1]);news_cutoff=cutoff-timedelta(hours=1)
streams={}
for j,symbol in enumerate(['MSI','AXON','SPY','XLK','XLI']):
    streams[symbol]=[]
    for i,day in enumerate(days):
        closing=100+j*32+i/8+(i%5-2)/16;opening=closing+(i%3-1)/32
        streams[symbol].append({'id':j*10000+i+1,'symbol':symbol,'session_date':day,'open':str(opening),'high':str(max(opening,closing)+1.25),'low':str(min(opening,closing)-1.5),'close':str(closing),'volume':str(1000000+i*123+(i%7)*1000),'feed':'sip','adjustment':'split','adjustment_asof':days[-1],'available_at':schedule(day,day)[day]['close']+timedelta(minutes=15),'acquired_at':schedule(day,day)[day]['close']+timedelta(minutes=16)})
info=[]
for i,hours in enumerate([0,1,23,24,25,120,121,600,601,624]):
    created=news_cutoff-timedelta(hours=hours)
    info.append({'id':f'n{i}','provider':'alpaca_news','external_id':f'article-{i}','symbols':['MSI','AXON'],'created_at':created,'updated_at':created+timedelta(minutes=5),'available_at':created+timedelta(minutes=5),'acquired_at':cutoff-timedelta(seconds=1),'payload':{}})
info.append({**info[2],'id':'late-news-revision','updated_at':news_cutoff-timedelta(minutes=1),'available_at':news_cutoff-timedelta(minutes=1),'acquired_at':cutoff+timedelta(seconds=1)})
for i,(age,available,acquired,direction) in enumerate([(20,6,5,1),(30,2,1,-1),(1,1,-1,None),(31,1,1,0)]):
    info.append({'id':f'ir{i}','provider':'official_ir','external_id':f'ir-{i}','symbols':['MSI','AXON'],'created_at':cutoff-timedelta(days=age),'updated_at':cutoff-timedelta(days=available),'available_at':cutoff-timedelta(days=available),'acquired_at':cutoff-timedelta(days=acquired),'payload':{'flags':{'guidance_direction':direction,'contract':i%2==0,'acquisition':i%2==1}}})
coverage=[{'provider':'alpaca_news','symbols':['MSI','AXON'],'status':'complete','started_at':news_cutoff-timedelta(days=40),'ended_at':news_cutoff,'acquired_at':cutoff-timedelta(seconds=1)}]
facts=[]
for year in [2024,2025]:
    for metric,value in [('revenue',100 if year==2024 else 125),('operating_income',20 if year==2024 else 30),('operating_cash_flow',-50 if year==2024 else 60),('cash',1)]:
        facts.append({'id':len(facts)+1,'symbol':'MSI','metric':metric,'tag':TAGS[metric][0],'unit':'USD','value':value,'period_start':None if metric=='cash' else date(year,7,1),'period_end':date(year,9,30),'available_at':cutoff-timedelta(days=20 if year==2024 else 5),'acquired_at':cutoff-timedelta(days=1),'accession':f'fixture-{year}-{metric}'})
facts.append({**facts[4],'id':99,'value':130,'available_at':cutoff-timedelta(days=1),'acquired_at':cutoff+timedelta(hours=1),'accession':'late-amendment'})
events=[{'id':f'e{i}','symbol':'MSI','published_at':cutoff-timedelta(days=age),'acquired_at':cutoff+timedelta(seconds=late),'items':items,'form':form} for i,(age,late,items,form) in enumerate([(7,-1,'2.02','8-K'),(3,-1,'9.01','10-Q'),(1,1,'2.02','8-K/A'),(-1,-1,'2.02','8-K')])]
information_cases=[]
for observed in [False,True]:
    for name,information,cov,fs,es in [('base',info,coverage,facts,events),('no-coverage',info,[],facts,events),('no-news',[],coverage,[],[]),('old-ir-only',[r for r in info if r['id']=='ir3'],[],facts,events),('no-sec',info,coverage,[],[]),('late-coverage',info,[{**coverage[0],'acquired_at':cutoff+timedelta(seconds=1)}],facts,events),('coverage-short',info,[{**coverage[0],'started_at':news_cutoff-timedelta(days=25)+timedelta(microseconds=1)}],facts,events)]:
        value,lineage=p.information_values('MSI',cutoff,information,cov,fs,es,observed)
        information_cases.append({'name':name,'observed':observed,'symbol':'MSI','cutoff':cutoff,'information':information,'coverage':cov,'facts':fs,'events':es,'values':value,'lineage':lineage})
contexts=[]
for name in ['base','different-context-basis','missing-peer','context-shorter','gap','mixed','empty-own','flat','zero-volume']:
    data=copy.deepcopy(streams)
    if name=='different-context-basis':
        for s in ['AXON','XLK','XLI']:
            for row in data[s]:row['adjustment_asof']=days[-2]
    if name=='missing-peer':data.pop('AXON')
    if name=='context-shorter':data['XLK']=data['XLK'][:-3]
    if name=='gap':data['XLI'].pop(20)
    if name=='mixed':data['MSI'][-1]['feed']='iex'
    if name=='empty-own':data['MSI']=[]
    if name=='flat':
        for rows in data.values():
            for row in rows:row.update(open='100',high='101',low='99',close='100')
    if name=='zero-volume':
        for row in data['MSI']:row['volume']='0'
    contexts.append({'name':name,'streams':data,'information':info,'coverage':coverage,'facts':facts,'events':events})
build=[]
for i,context in enumerate(contexts):
    for observed,latest in [(False,False),(True,True)]:
        case={'context':i,'symbol':'MSI','observed':observed,'latest_only':latest}
        try:
            rows=p.build_features(context['streams'],'MSI',info,coverage,facts,events,observed,latest)
            case['rows']=rows;case['ids']=[digest({'symbol':'MSI','date':r['date'],'version':p.VERSION,'mode':'prospective' if observed else 'historical','x':r['x'],'lineage':r['lineage']}) for r in rows]
        except Exception as e:case['error']={'kind':type(e).__name__,'message':str(e)}
        build.append(case)
fixture={'version':p.VERSION,'names':p.NAMES,'groups':p.GROUPS,'information':information_cases,'contexts':contexts,'build':build}
path=ROOT/f'apps/backend-rust/tests/fixtures/point-features{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen causal feature oracle differs'
else:path.write_bytes(blob)
print(json.dumps({'information_cases':len(information_cases),'contexts':len(contexts),'build_cases':len(build),'rows':sum(len(c.get('rows',[])) for c in build)}))
extra=[]
for name in ['covered-no-news','covered-ancient-news','company-scoping','random-prices','future-appended']:
    context=copy.deepcopy(contexts[0]);context['name']=name
    if name.startswith('covered-'):
        context['information']=[] if name=='covered-no-news' else [{**info[1],'id':'ancient','created_at':news_cutoff-timedelta(days=500),'updated_at':news_cutoff-timedelta(days=499),'available_at':news_cutoff-timedelta(days=499),'acquired_at':cutoff_for(days[0])-timedelta(days=30)}]
        context['coverage']=[{**coverage[0],'started_at':cutoff_for(days[0])-timedelta(days=40),'acquired_at':cutoff_for(days[0])-timedelta(days=1)}]
    if name=='company-scoping':
        context['facts'] += [{**r,'id':r['id']+10000,'symbol':'AXON','value':r['value']*2+3} for r in facts]
        context['events'] += [{**r,'id':r['id']+'-AXON','symbol':'AXON'} for r in events]
    if name=='random-prices':
        rng=random.Random(2316658)
        for rows in context['streams'].values():
            for row in rows:
                close=50+rng.random()*500;opening=close*(1+rng.uniform(-.03,.03));row.update(open=str(opening),close=str(close),high=str(max(close,opening)+1.25),low=str(min(close,opening)-1.5),volume=str(rng.randint(1,10000000)))
    if name=='future-appended':
        future=list(schedule(days[-1]+timedelta(days=1),days[-1]+timedelta(days=7)))[0]
        for rows in context['streams'].values():rows.append({**rows[-1],'id':rows[-1]['id']+100000,'session_date':future,'open':'5000','high':'5001','low':'4999','close':'5000','available_at':cutoff_for(future),'acquired_at':cutoff_for(future)})
        context['facts'].append({**facts[-1],'id':999,'value':9999999,'available_at':cutoff+timedelta(seconds=1),'acquired_at':cutoff+timedelta(seconds=1)})
    extra.append(context)
extra_build=[]
for i,context in enumerate(extra):
    symbol='AXON' if context['name']=='company-scoping' else 'MSI'
    for observed,latest in [(False,False),(True,True)]:
        rows=p.build_features(context['streams'],symbol,context['information'],context['coverage'],[r for r in context['facts'] if r['symbol']==symbol],[r for r in context['events'] if r['symbol']==symbol],observed,latest)
        extra_build.append({'context':i,'symbol':symbol,'observed':observed,'latest_only':latest,'rows':rows,'ids':[digest({'symbol':symbol,'date':r['date'],'version':p.VERSION,'mode':'prospective' if observed else 'historical','x':r['x'],'lineage':r['lineage']}) for r in rows]})
path=ROOT/f'apps/backend-rust/tests/fixtures/point-feature-boundaries{suffix}.json';blob=canonical({'contexts':extra,'build':extra_build}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen causal feature boundaries differ'
else:path.write_bytes(blob)
print(json.dumps({'boundary_contexts':len(extra),'build_cases':len(extra_build),'rows':sum(len(c['rows']) for c in extra_build)}))
if suffix:
    import numpy as np
    from importlib.metadata import version
    assert np.__version__=='2.5.4'
    paths=['point_features.py','financials.py','forecasting.py','research.py','experiments.py','calendar.py','util.py']
    metadata={'platform':sys.platform,'machine':platform.machine(),'python':platform.python_version(),'versions':{name:version(name) for name in ['numpy','scikit-learn','pandas','pandas-market-calendars']},'original_sources':{name:hashlib.sha256((ROOT/'apps/api/intelligence'/name).read_bytes()).hexdigest() for name in paths},'numpy_log1p_2':float(np.log1p(2)),'fixtures':{name:hashlib.sha256((ROOT/'apps/backend-rust/tests/fixtures'/name).read_bytes()).hexdigest() for name in ['point-features-linux.json','point-feature-boundaries-linux.json','point-features.json','point-feature-boundaries.json']},'method':'Unchanged original Python functions on Linux; existing Windows corpora are never rewritten; exact comparisons retained.'}
    path=ROOT/'apps/backend-rust/tests/fixtures/point-features-linux-runtime.json';blob=canonical(metadata).encode()
    if path.exists():assert path.read_bytes()==blob,'Linux reference provenance differs'
    else:
        with path.open('xb') as f:f.write(blob)
    print(json.dumps(metadata))
