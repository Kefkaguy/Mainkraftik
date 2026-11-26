"""Original fixed chart pixels/PNG/manifest identities, without DB/model execution."""
import argparse,base64,hashlib,io,json,math,sys,zlib,platform
from datetime import UTC,datetime,date,timedelta
from pathlib import Path
from PIL import Image,features,__version__ as pillow_version
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import schedule
from intelligence.vision import render_chart
from intelligence.util import canonical,digest
parser=argparse.ArgumentParser()
parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows')
args=parser.parse_args()
assert platform.system().lower()==args.reference_platform
assert (features.version('zlib')=='1.3') if args.reference_platform=='linux' else (features.version('zlib').endswith('.zlib-ng') and features.version('zlib_ng')=='2.3.3')
suffix='-linux' if args.reference_platform=='linux' else ''
def immutable(path,blob):
    if path.exists():assert path.read_bytes()==blob,'Frozen chart fixture differs'
    else:path.write_bytes(blob)
days=list(schedule(date(2026,5,1),date(2026,11,18)))[-95:]
cutoff=datetime(2026,11,19,6,tzinfo=UTC)
cases=[]
for scenario in ['rising','falling','flat_bodies','volatile','fractional_cutoff']:
    rows=[]
    for index,day in enumerate(days):
        opening=50.0+index*0.2
        closing=opening+0.3
        if scenario=='falling':opening=80-index*0.15;closing=opening-0.4
        if scenario=='flat_bodies':closing=opening
        if scenario=='volatile':opening=60+math.sin(index*0.7)*7;closing=opening+math.cos(index)*2
        available=schedule(day,day)[day]['close']+timedelta(minutes=15)
        rows.append({'id':index+1,'symbol':'MSI','session_date':day,'feed':'sip','adjustment':'split','adjustment_asof':cutoff.date(),'open':opening,'high':max(opening,closing)+1,'low':min(opening,closing)-1,'close':closing,'volume':1000000,'available_at':available,'acquired_at':available+timedelta(seconds=1),'content_hash':digest([scenario,index])})
    # Both future availability and future actual acquisition must be excluded.
    rows.extend([{**rows[-1],'id':1001,'available_at':cutoff+timedelta(days=1)}, {**rows[-1],'id':1002,'acquired_at':cutoff+timedelta(days=1)}])
    at=cutoff if scenario!='fractional_cutoff' else datetime.fromisoformat('2026-11-18T22:00:00.123400-08:00')
    content,manifest,image_hash=render_chart(rows,at)
    pixels=Image.open(io.BytesIO(content)).convert('RGB').tobytes()
    pos=8;compressed=b'';chunks=[]
    while pos<len(content):
        n=int.from_bytes(content[pos:pos+4],'big');kind=content[pos+4:pos+8];data=content[pos+8:pos+8+n];pos+=12+n;chunks.append(kind.decode())
        if kind==b'IDAT':compressed+=data
    cases.append({'scenario':scenario,'bars':rows,'cutoff':at,'png':base64.b64encode(content).decode(),'image_hash':image_hash,'pixel_hash':hashlib.sha256(pixels).hexdigest(),'filtered_hash':hashlib.sha256(zlib.decompress(compressed)).hexdigest(),'manifest':manifest,'input_hash':digest(manifest),'chunks':chunks})
invalid=[]
base=cases[0]['bars'][:-2]
for name,rows in [('short',base[:89]),('missing_session',base[:-20]+base[-19:]),('mixed_feed',[{**r,'feed':'iex' if i==20 else 'sip'} for i,r in enumerate(base)]),('unordered',list(reversed(base))),('degenerate',[{**r,'high':50,'low':50,'open':50,'close':50} for r in base])]:
    try:render_chart(rows,cutoff);raise AssertionError('fixture must be refused')
    except ValueError as error:invalid.append({'name':name,'bars':rows,'cutoff':cutoff,'error':str(error)})
fixture={'pillow':pillow_version,'zlib':features.version('zlib'),'zlib_ng':features.version('zlib_ng'),'source_sha256':hashlib.sha256((ROOT/'apps/api/intelligence/vision.py').read_bytes()).hexdigest(),'cases':cases,'invalid':invalid}
immutable(ROOT/f'apps/backend-rust/tests/fixtures/chart-parity{suffix}.json',canonical(fixture).encode())
immutable(ROOT/'apps/backend-rust/third-party/PILLOW-LICENSE.txt',(ROOT/'.local/rust-migration/pillow-source-12.3.0/LICENSE').read_bytes())
print('Saved five exact chart PNG/pixel/filter/manifest oracles and five refusal cases')
