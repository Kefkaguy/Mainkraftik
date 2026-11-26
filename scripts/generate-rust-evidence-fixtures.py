"""Freeze Python store/journal formats using only new synthetic temporary stores."""
import gzip,hashlib,json,random,struct,sys,tempfile
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from chart_intelligence import store,phase8_evidence,controller
from paperpilot import lab_registry
from intelligence.util import canonical
instant=datetime(2026,11,20,6,1,2,123000,tzinfo=UTC)
values=[None,True,False,0,1,-1,2**64-1,-0.0,1e-7,1e-5,1e-4,1e16,5e-324,1.2345678901234567,'\x00\t\n\r\b\f λ😀"\\',[],{},[None,True,2],{'λ':'value','a':'😀'}]
rng=random.Random(2316657)
for _ in range(120):
    value=struct.unpack('>d',rng.getrandbits(64).to_bytes(8,'big'))[0]
    if __import__('math').isfinite(value):values.append(value)
encoding=[{'value':v,'compact':json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False),'spaced':json.dumps(v,sort_keys=True,allow_nan=False)} for v in values]
cases=[];journal=[];baseline=None
with tempfile.TemporaryDirectory(prefix='mainkraft-evidence-reference-') as directory:
    base=Path(directory)
    for i,payload in enumerate([{'status':'fixture','text':'λ😀','value':-0.0},{'record_id':'payload-wins','report_id':'payload-value'},None,[],17,'text',{'small':1e-7,'nested':[1,False,None]},{}]):
        kind=['fixture',{'custom':True},None][i%3]
        for family in ['chart','lab','compressed']:
            root=base/f'{family}-{i}';record={'kind':kind,'payload':payload};encoded=json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False)
            with patch.object(lab_registry,'LAB',root),patch.object(lab_registry,'now',return_value=instant):
                key=store.put(kind,payload,root) if family=='chart' else lab_registry.put(kind,payload) if family=='lab' else phase8_evidence.put_compressed(kind,payload,root)
                case={'family':family,'kind':kind,'payload':payload,'id':key,'encoded':encoded,'read':store.read(key,root) if family=='chart' else lab_registry.read(key) if family=='lab' else phase8_evidence.read_compressed(key,root)}
                if family!='compressed':
                    if family=='chart':store.publish(key,root);case['published_id']=key
                    else:case['published_id']=lab_registry.publish(payload)
                    case['pointer']=(root/'latest.json').read_text(encoding='utf-8')
                    try:case['latest']=store.latest(root) if family=='chart' else lab_registry.latest()
                    except Exception as e:case['latest_error']={'kind':type(e).__name__,'message':str(e)}
                else:case['gzip_hex']=(root/'compressed'/(key+'.json.gz')).read_bytes().hex()
                cases.append(case)
    db=controller.connection(base/'journal')
    for key,kind,payload in [('forecast/one','prediction',{'score':1e-7,'text':'λ😀'}),('forecast/one','prediction',{'changed':True}),('run/one','controller_run',{'done':True,'values':values[:20]})]:
        controller.append(db,key,kind,payload);db.commit();journal.append({'key':key,'kind':kind,'payload':payload})
    controller.append(db,'rollback','outcome',{'future':True});db.rollback()
    rows=[list(row) for row in db.execute('SELECT key,kind,payload FROM evidence ORDER BY rowid')]
    errors=[]
    for statement in ["UPDATE evidence SET payload='{}'","DELETE FROM evidence"]:
        try:db.execute(statement)
        except Exception as e:errors.append({'kind':type(e).__name__,'message':str(e)})
        db.rollback()
    db.close()
    original_open=Path.open
    class Interrupted:
        def __init__(self,path,*a,**kw):self.file=original_open(path,*a,**kw)
        def __enter__(self):return self
        def __exit__(self,*a):self.file.close()
        def write(self,text):self.file.write(text[:len(text)//2]);self.file.flush();raise OSError('Injected process interruption')
    root=base/'baseline';payload={'message':'interrupted synthetic record'}
    with patch.object(Path,'open',lambda path,*a,**kw:Interrupted(path,*a,**kw) if a and a[0]=='x' else original_open(path,*a,**kw)):
        try:store.put('fixture',payload,root)
        except OSError:pass
    key=hashlib.sha256(json.dumps({'kind':'fixture','payload':payload},sort_keys=True,separators=(',',':')).encode()).hexdigest();path=root/'records'/(key+'.json')
    try:store.put('fixture',payload,root)
    except Exception as e:baseline={'partial_final_record':True,'bytes':path.read_text(encoding='utf-8'),'retry_kind':type(e).__name__,'retry_message':str(e)}
fixture={'instant':instant,'encoding':encoding,'cases':cases,'journal':journal,'journal_rows':rows,'journal_errors':errors,'original_interruption':baseline}
path=ROOT/'apps/backend-rust/tests/fixtures/evidence-storage.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen evidence-store oracle differs'
else:path.write_bytes(blob)
print(json.dumps({'encoding_cases':len(encoding),'store_cases':len(cases),'journal_rows':len(rows),'original_interruption':baseline}))
