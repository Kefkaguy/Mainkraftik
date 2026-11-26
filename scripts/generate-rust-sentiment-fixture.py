"""Capture the existing sentiment protocol/aggregation with an offline fake model."""
import hashlib
import json
import sys
from datetime import datetime,UTC
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'apps/api'),str(ROOT/'apps/api/tests')]
import httpx
from test_research_context import inputs
from paperpilot import sentiment
from intelligence.util import canonical,digest

candidates,articles,_=inputs(2);at=datetime(2026,11,19,12,30,tzinfo=UTC);captured={}
sentiment.now=lambda:at
sentiment.store=lambda symbol,kind,payload:digest([symbol,kind,payload])
def handler(request):
    items=json.loads(request.content)['items'];captured['items']=items
    scores=[]
    for i,item in enumerate(items):scores.append({'id':item['id'],'text_hash':hashlib.sha256(item['text'].encode()).hexdigest(),
        'scores':{'negative':.8 if i<3 else .1,'neutral':.1 if i<3 else .2,'positive':.1 if i<3 else .7}})
    model={'revision':sentiment.REVISION,'sha256':{'onnx/model_quantized.onnx':sentiment.WEIGHT_HASH}}
    captured['response']={'model':model,'scores':scores}
    return httpx.Response(200,json=captured['response'])
with httpx.Client(transport=httpx.MockTransport(handler)) as client:
    evidence=sentiment.assess(candidates,articles,'news-source-fixture',client)
data={'at':at,'candidates':candidates,'articles':articles,'source_id':'news-source-fixture','items':captured['items'],'response':captured['response'],'expected':evidence}
p=ROOT/'apps/backend-rust/tests/fixtures/sentiment-parity.json';blob=json.dumps(data,default=lambda v:v.isoformat(),ensure_ascii=True,separators=(',',':')).encode()
if p.exists():assert p.read_bytes()==blob
else:p.write_bytes(blob)
print('Saved offline sentiment request/probability/provenance/aggregation oracle')
