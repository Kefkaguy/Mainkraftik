"""Strict-news contract oracles; no LLM request, account or database access."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'apps/api'),str(ROOT/'apps/api/tests')]
from test_research_context import inputs
from paperpilot.discovery import research_context,filter_research_output,ResearchOutputRejected,_model_provenance,Ranking

contexts=[]
for size,unicode in [(2,False),(40,False),(40,True)]:
    candidates,articles,sentiment=inputs(size)
    if unicode:
        for article in articles.values():article['headline']='收购"\\\n'*200;article['summary']='利益'*500
    for tone in (None,sentiment):
        content,audit=research_context(candidates,articles,tone)
        contexts.append({'candidates':candidates,'articles':articles,'sentiment':tone,'content':content,'metadata':audit})
candidates,articles,_=inputs(2);context,audit=research_context(candidates,articles)
symbols=list(candidates)
pick=lambda s,ref:{'symbol':s,'source_ids':[ref],'stance':'bullish','rationale':'Sourced fixture'}
outputs=[{'picks':[]},{'picks':[pick(symbols[0],'00-0')]},{'picks':[pick(symbols[0],'00-0'),pick(symbols[0],'00-1'),pick(symbols[1],'01-0')]},
         {'picks':[pick('UNKNOWN','00-0')]},{'picks':[pick(symbols[0],'01-0')]},{'picks':[{**pick(symbols[0],'00-0'),'tools':'untrusted'}]},
         {'picks':[pick(symbols[0],'00-0')],'extra':True}]
filters=[]
for output in outputs:
    try:
        accepted,rejections=filter_research_output(output,candidates,articles,audit['article_ids_by_symbol']);result={'accepted':accepted,'rejections':rejections}
    except ResearchOutputRejected as exc:result={'error':str(exc),'rejections':exc.rejections}
    filters.append({'output':output,'candidates':candidates,'articles':articles,'presented':audit['article_ids_by_symbol'],'expected':result})
provenance=[]
for value in [{'id':'chatcmpl-test_1','usage':{'total_tokens':123,'queue_time':.1,'secret':'never','prompt_tokens':True}}, {'id':'hostile-id','usage':{'total_time':-1,'total_tokens':1e20}},None]:
    provenance.append({'input':value,'expected':_model_provenance(value)})
data={'contexts':contexts,'filters':filters,'provenance':provenance}
path=ROOT/'apps/backend-rust/tests/fixtures/news-parity.json';blob=json.dumps(data,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()
if path.exists():assert path.read_bytes()==blob
else:path.write_bytes(blob)
schema=ROOT/'apps/backend-rust/assets/news-ranking-schema.json';raw=json.dumps(Ranking.model_json_schema(),separators=(',',':')).encode()
if schema.exists():assert schema.read_bytes()==raw
else:schema.write_bytes(raw)
print('Saved six exact prompt contexts, seven strict-output cases and three provenance oracles')
