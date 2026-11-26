"""Freeze original private news metadata and text flags, including Unicode boundaries."""
import json,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.information import news_metadata,tags
from intelligence.util import canonical
texts=['earnings report raises guidance','no changes, raises outlook','raised guidance','increased outlook','reaffirmed guidance','maintain outlook',
 'report blah earnings revenuex','_earnings_','earnings\u0301','\u03b1earnings','earnings\u00b2','contract awarded acquisition','merger unchanged from prior, lowers outlook',
 'raises raises guidance','raises outlook, lowers guidance','lowers outlook, raises guidance','reports revenue','reports'+('x'*35)+'revenue',
 'raise'+(' '*55)+'guidance','raise'+('x'*55)+'guidance','raise '+('x'*54)+' guidance']
rng=random.Random(20261120)
words=['raises','raised','raise','increase','increased','lowers','reduced','reaffirm','maintains','guidance','outlook','no','not','unchanged from prior','earnings','reports','revenue','contract','acquires','merger','award','selected']
for _ in range(220):texts.append(''.join(rng.choice([' ','\t','\n','\u001c','\u00a0','\u0301','_','\u03b1'])+rng.choice(words) for _ in range(rng.randrange(1,9))))
for count in [0,1,54,55,56]:texts.extend(['raises '+('x'*count)+' guidance','raises '+('x'*count)+' outlook'])
rows=[]
base={'id':101,'created_at':'2026-11-18T12:00:00Z','updated_at':'2026-11-18T13:00:00Z','headline':'Reports earnings, raises outlook','symbols':['MSI','AXON','MSI'],'source':'fixture','author':'fixture author','url':'https://example.invalid/news','content':'private article body must not persist','summary':'private summary must not persist'}
for row in [base,dict(base,id=True,headline=None),dict(base,headline='\U0001f600'*1001),dict(base,created_at='2026-11-18T04:00:00.123456789-08:00'),dict(base,updated_at='2026-11-18T10:00:00Z'),dict(base,updated_at='2026-11-18T12:00:00'),dict(base,updated_at='invalid'),{k:v for k,v in base.items() if k!='id'}, {k:v for k,v in base.items() if k!='headline'}]:
    case={'row':row}
    try:case['metadata']=news_metadata(row)
    except (ValueError,KeyError) as e:case['kind']=type(e).__name__;case['message']=str(e)
    rows.append(case)
path=ROOT/'apps/backend-rust/tests/fixtures/information-metadata.json';blob=canonical({'tags':[{'text':text,'flags':tags(text)} for text in texts],'news':rows}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen information metadata oracle differs'
else:path.write_bytes(blob)
print(f'Saved {len(texts)} original flag cases and {len(rows)} private news metadata/refusal cases')
