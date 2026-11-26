"""Unchanged discovery/sentiment/worker functions with synthetic inputs and real pinned ONNX."""
import argparse,ast,hashlib,json,platform,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows');args=parser.parse_args()
assert platform.system().lower()==args.reference_platform
linux=args.reference_platform=='linux'
if linux:sys.path.append(str(Path.home()/'.local/share/mainkraft-rust-validation/python-reference/lib/python3.12/site-packages'))
else:sys.path.append(str(ROOT/'.venv/Lib/site-packages'))
sys.path.insert(0,str(ROOT/'apps/api'))
import onnxruntime,tokenizers,numpy
assert onnxruntime.__version__=='1.32.0' and tokenizers.__version__=='0.23.3' and numpy.__version__=='2.5.4'
from paperpilot import sentiment
from intelligence.util import canonical
worker_source=ROOT/'workers/sentiment/app.py';worker_tree=ast.parse(worker_source.read_text(encoding='utf-8'))
assignment=next(n for n in worker_tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ROOT' for t in n.targets))
assignment.value=ast.Call(func=ast.Name(id='Path',ctx=ast.Load()),args=[ast.Constant(str(ROOT/'.local/models/distilroberta-financial-q8'))],keywords=[])
ast.fix_missing_locations(worker_tree);worker={'__name__':'original_sentiment_assembly_reference'}
exec(compile(worker_tree,str(worker_source),'exec'),worker)
calls=[]
def actual_score(body):
 result=worker['score'](worker['Batch'](**body));calls.append({'items':body['items'],'expected':result});return result
source=ROOT/'scripts/generate-rust-research-fixture.py';tree=ast.parse(source.read_text(encoding='utf-8'))
cut=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='path' for t in n.targets))
tree.body=tree.body[:cut]
# Change test adapters only; application research/ranking/sentiment functions remain unchanged.
adapters=ast.parse('''
class Response:
 status_code=200
 def __init__(self,payload=None):self.payload=completion if payload is None else payload
 def json(self):return self.payload
class Client:
 def __init__(self,*args,**kwargs):pass
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def close(self):pass
 def post(self,url,headers=None,json=None):
  if url.endswith('/score'):return Response(actual_score(json))
  ranking_requests.append(json);return Response()
''').body
replacement={n.name:n for n in adapters}
tree.body=[replacement.get(n.name,n) if isinstance(n,ast.ClassDef) else n for n in tree.body]
patches=next(n for n in ast.walk(tree) if isinstance(n,ast.List) and any(isinstance(t,ast.Tuple) and len(t.elts)==3 and isinstance(t.elts[1],ast.Constant) and t.elts[1].value=='paper_sentiment_enabled' for t in n.elts))
for item in patches.elts:
 if isinstance(item,ast.Tuple) and len(item.elts)==3 and isinstance(item.elts[1],ast.Constant) and item.elts[1].value=='paper_sentiment_enabled':item.elts[2]=ast.Constant(True)
patches.elts.extend(ast.parse("[(sentiment,'now',lambda:instant),(sentiment,'store',store)]").body[0].value.elts)
ast.fix_missing_locations(tree)
namespace={'__file__':str(source),'__name__':'original_research_assembly_reference','sentiment':sentiment,'actual_score':actual_score}
exec(compile(tree,str(source),'exec'),namespace)
fixture=namespace['fixture'];assert len(calls)==1
assert 'MUST_NOT_ESCAPE' not in canonical(fixture['expected'])
capi=Path(onnxruntime.__file__).parent/'capi'
if linux:
 libraries=list(capi.glob('libonnxruntime.so.*'));assert len(libraries)==1;library=libraries[0]
else:library=capi/'onnxruntime.dll'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
fixture.update(synthetic_only=True,sentiment_calls=calls,reference_platform=args.reference_platform,runtime_library_sha256=sha(library),reference_sources={p.relative_to(ROOT).as_posix():sha(p) for p in [source,worker_source,ROOT/'apps/api/paperpilot/discovery.py',ROOT/'apps/api/paperpilot/sentiment.py']})
suffix='-linux' if linux else ''
path=ROOT/f'apps/backend-rust/tests/fixtures/research-sentiment-parity{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Original sentiment-enabled assembly oracle differs; never overwrite'
else:
 with path.open('xb') as f:f.write(blob)
print(json.dumps({'sentiment_batches':len(calls),'model_retrained':False,'source_functions_modified':False,'broker_requests':0,'private_content_not_exposed':True,'output':path.as_posix(),'sha256':hashlib.sha256(blob).hexdigest()}))
