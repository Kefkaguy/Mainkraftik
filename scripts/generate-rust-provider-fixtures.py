"""Freeze unchanged research provider requests, refusals, exact decimal and SEC identities."""
import copy,hashlib,json,sys
from datetime import UTC,date,datetime
from pathlib import Path
from unittest.mock import patch
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import providers,financials
from intelligence.util import canonical
at=datetime(2026,11,19,6,tzinfo=UTC);asof=date(2026,11,18)
raw=[{'t':'2026-11-18T05:00:00Z','o':'100.00000000000000000002','h':'102.00000000000000000003','l':'99.99999999999999999999','c':'101.00000000000000000001','v':'1000000.0000000000000001'},
     {'t':'2026-11-17T05:00:00Z','o':99,'h':101.0,'l':98.1,'c':100.25,'v':0}]
normal=[]
for name,rows,feed,acquired in [
 ('exact_decimals',raw,'sip',at),('before_close',raw,'iex',datetime(2026,11,18,21,14,59,tzinfo=UTC)),
 ('at_close',raw,'sip',datetime(2026,11,18,21,15,tzinfo=UTC)),('empty',[],'sip',at),('unsupported_feed',raw,'bad',at),
 ('duplicate',[raw[0],raw[0]],'sip',at),('nonfinite',[dict(raw[0],o='NaN')],'sip',at),
 ('decimal_invalid',[dict(raw[0],o='102.00000000000000000004')],'sip',at),
 ('negative_volume',[dict(raw[0],v='-0.00000000000000000001')],'sip',at),
 ('negative_zero',[dict(raw[0],v='-0')],'sip',at),('weekend',[dict(raw[0],t='2026-11-15T05:00:00Z')],'sip',at),
 ('naive',[dict(raw[0],t='2026-11-18T05:00:00')],'sip',at)]:
    case={'name':name,'raw':rows,'feed':feed,'asof':asof,'acquired':acquired}
    try:case['rows']=providers.normalize_bars('MSI',rows,feed,asof,acquired)
    except ValueError as e:case['error']=str(e)
    normal.append(case)
availability=[]
for text in ['2026-11-18T21:15:00Z','2026-11-18T13:15:00.123400-08:00','2026-11-18T16:15:00',
             '2026-03-08T02:30:00','2026-11-01T01:30:00','',None]:
    submissions={'filings':{'recent':{'accessionNumber':['a'],'acceptanceDateTime':[] if text is None else [text]}}}
    availability.append({'filed':'2026-11-18','accession':'a','submissions':submissions,'result':providers.filing_availability('2026-11-18','a',submissions)})
for filed in ['2026-03-07','2026-03-08','2026-10-31','2026-11-01']:
    availability.append({'filed':filed,'accession':'missing','submissions':{},'result':providers.filing_availability(filed,'missing',{})})
facts={'cik':68505,'facts':{'us-gaap':{}}}
for i,(metric,tags) in enumerate(financials.TAGS.items()):
    for j,tag in enumerate(tags):
        unit='USD/shares' if metric=='eps' else 'shares' if metric=='diluted_shares' else 'USD'
        row={'end':'2026-09-30','filed':'2026-11-10','accn':f'0000068505-26-{i:06d}','val':float(i+j)+0.125,'fy':2026,'fp':'Q3','form':'10-Q'}
        if metric not in ['cash','current_debt','long_term_debt']:row['start']='2026-07-01'
        facts['facts']['us-gaap'][tag]={'units':{unit:[row,dict(row,form='8-K'),dict(row,start='2026-10-01'),{k:v for k,v in row.items() if k!='val'}],'EUR':[row]}}
facts_cases=[]
for payload,submissions in [(facts,{}),({},{}),(facts,{'filings':{'recent':{'accessionNumber':['0000068505-26-000000'],'acceptanceDateTime':['2026-11-10T16:01:00-05:00']}}})]:
    facts_cases.append({'facts':payload,'submissions':submissions,'acquired':at,'rows':financials.normalize_facts('MSI',payload,submissions,at)})

wire=[]
class Client:
    def __init__(self,responses):self.responses=iter(responses);self.requests=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url,**kwargs):
        self.requests.append({'url':url,'query':copy.deepcopy(kwargs.get('params',{})),'headers':copy.deepcopy(kwargs.get('headers',{}))})
        item=next(self.responses)
        if isinstance(item,str):raise httpx.ConnectError(item)
        return httpx.Response(item['status'],json=item.get('payload',{}),request=httpx.Request('GET',url))
def scenario(name,operation,responses,**overrides):
    args={'symbol':'MSI','start':date(2026,1,1),'end':at,'asof':asof,'cik':'0000068505'}
    client=Client(responses);delays=[]
    with patch.object(providers.settings,'alpaca_key','fixture-key'),patch.object(providers.settings,'alpaca_secret','fixture-secret'),patch.object(providers.settings,'feed','sip'),patch.object(providers.settings,'sec_user_agent','Mainkraft fixture fixture@example.invalid'),patch.object(providers.time,'sleep',lambda seconds:delays.append(round(seconds*1000))),patch.object(providers.httpx,'Client',return_value=client):
        with __import__('contextlib').ExitStack() as stack:
            for key,value in overrides.items():stack.enter_context(patch.object(providers.settings,key,value))
            try:
                if operation=='bars':result=providers.fetch_bars(args['symbol'],args['start'],args['end'],args['asof'],client)
                elif operation=='actions':result=providers.fetch_actions(args['symbol'],args['start'],args['asof'],client)
                elif operation=='sec':result=providers.fetch_sec(args['cik'],client)
                else:result=providers.lookup_company(args['symbol'])
                outcome={'result':result}
            except providers.ProviderError as e:outcome={'error':str(e)}
    wire.append({'name':name,'operation':operation,'responses':responses,'overrides':overrides,'args':args,'requests':client.requests,'delays':delays,**outcome})
ok=lambda payload:{'status':200,'payload':payload}
scenario('bars-two-pages','bars',[ok({'bars':[raw[0]],'next_page_token':'two'}),ok({'bars':[raw[1]],'next_page_token':None})])
scenario('bars-repeated','bars',[ok({'next_page_token':'x'})]*2)
scenario('bars-ceiling','bars',[ok({'next_page_token':str(i)}) for i in range(25)])
scenario('bars-retries','bars',[{'status':s} for s in [429,503,502]]+[ok({'bars':raw})])
scenario('bars-exhausted','bars',[{'status':500}]*4)
scenario('bars-permission','bars',[{'status':403}])
scenario('bars-connection','bars',['fixture connection error'])
scenario('bars-no-key','bars',[],alpaca_key='')
scenario('actions-two-pages','actions',[ok({'corporate_actions':{'cash_dividends':[{'ex_date':'2026-11-18','cash':0.5}]},'next_page_token':'second'}),ok({})])
scenario('actions-repeated','actions',[ok({'next_page_token':'x'})]*2)
scenario('actions-ceiling','actions',[ok({'next_page_token':str(i)}) for i in range(20)])
scenario('sec-pair','sec',[ok({'filings':{}}),ok(facts)])
scenario('sec-retry','sec',[{'status':429},ok({}),ok({})])
scenario('sec-no-contact','sec',[],sec_user_agent='missing-contact')
mapping={'0':{'ticker':'msi','title':'Motorola Solutions','cik_str':68505}}
scenario('company','company',[ok(mapping)])
scenario('company-missing','company',[ok({})])
scenario('company-duplicate','company',[ok(dict(mapping,other=mapping['0']))])
scenario('company-no-contact','company',[],sec_user_agent='missing-contact')
fixture={'normalization':normal,'availability':availability,'facts':facts_cases,'wire':wire,'sources':{name:hashlib.sha256((ROOT/'apps/api/intelligence'/name).read_bytes()).hexdigest() for name in ['providers.py','financials.py']}}
path=ROOT/'apps/backend-rust/tests/fixtures/research-providers.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen provider oracle differs'
else:path.write_bytes(blob)
print(f'Saved {len(normal)} decimal/bar, {len(availability)} filing-time, {len(facts_cases)} GAAP normalization, {len(wire)} provider request/failure oracles')
edge_raw=[dict(raw[0],v=-0.0),dict(raw[1],v='-0E-8')]
edge={'raw':edge_raw,'asof':asof,'acquired':at,'rows':providers.normalize_bars('MSI',edge_raw,'sip',asof,at)}
path=ROOT/'apps/backend-rust/tests/fixtures/research-provider-decimal-edges.json';blob=canonical(edge).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen decimal edge oracle differs'
else:path.write_bytes(blob)
print('Saved negative-zero decimal/JSON canonical identity edge cases')
