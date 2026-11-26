"""Call the unmodified archived detection-chart route with offline frozen inputs."""
import argparse,base64,hashlib,io,json,sys,platform
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import main
from intelligence.util import canonical
from intelligence.vision_gateway import validate_boxes
parser=argparse.ArgumentParser()
parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows')
args=parser.parse_args();assert platform.system().lower()==args.reference_platform
suffix='-linux' if args.reference_platform=='linux' else ''
base=json.loads((ROOT/f'apps/backend-rust/tests/fixtures/chart-parity{suffix}.json').read_text(encoding='utf-8'))['cases'][0]
rows=[]
for row in base['bars']:
    if row['id'] not in base['manifest']['bar_ids']:continue
    row=dict(row)
    for key in ['available_at','acquired_at']:row[key]=datetime.fromisoformat(row[key])
    for key in ['session_date','adjustment_asof']:row[key]=date.fromisoformat(row[key])
    rows.append(row)
def box(label,xy,confidence=.75):return {'label':label,'xyxy':xy,'confidence':confidence}
atlas=json.loads((ROOT/'apps/backend-rust/assets/pillow-default-font-10.json').read_text(encoding='utf-8'))
glyphs=[box(chr(int(cp)),[(i%12)*52,(i//12)*55+20,(i%12)*52+8,(i//12)*55+25],.5) for i,cp in enumerate(atlas['glyphs'])]
sets=[[],[box('head_and_shoulders',[40,120,230,290]),box('bull flag',[250.25,350.5,570.75,590],.995)],
      [box('ffi ij AVTo',[10.4921875,35.5078125,300,120],.125),box('fractional',[30.5078125,135.4921875,350,190],.015)],
      [box('分散 📉\nŽáž\r\nffi',[3.5,63.5,100,100]),box('\t\u0000',[230,55,300,90])],
      [box('edge',[640,0,640,640]),box('edge',[0,640,640,640]),box('tiny',[638.5,638.5,639.9,639.9]),box('point',[0,0,0,0])],glyphs,
      [box('confidence',[30,40+i*50,300,80+i*50],p) for i,p in enumerate([0,.005,.015,.125,.995,1])]]
class Rows:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args):
        if 'FROM chart_detections' in sql:return Rows({'cutoff':datetime.fromisoformat(base['cutoff']),'image_hash':base['image_hash'],'input_hash':base['input_hash'],'payload':{'manifest':base['manifest'],'boxes':boxes}})
        assert 'FROM daily_bars' in sql;return Rows(rows)
cases=[]
for boxes in sets:
    with patch.object(main,'connection',Connection):response=main.detection_chart(UUID(int=42))
    png=response.body;pixels=Image.open(io.BytesIO(png)).convert('RGB').tobytes()
    cases.append({'boxes':boxes,'png':base64.b64encode(png).decode(),'image_hash':hashlib.sha256(png).hexdigest(),'pixel_hash':hashlib.sha256(pixels).hexdigest()})
validation=[]
for boxes in [None,{},[{}],[box('x'*101,[0,0,1,1])],[box('x',[0,0,1])],[box('x',[0,0,1,'2'])],[box('x',[-1,0,1,1])],[box('x',[0,0,1,1],1.01)],[box('bool',[False,False,True,True],True)]]:
    try:validate_boxes(boxes);expected={'accepted':True}
    except ValueError as error:expected={'error':str(error)}
    validation.append({'boxes':boxes,'expected':expected})
fixture={'base_case':0,'cases':cases,'validation':validation}
path=ROOT/f'apps/backend-rust/tests/fixtures/annotation-parity{suffix}.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen original annotation PNGs differ'
else:path.write_bytes(blob)
print('Saved seven exact original annotated PNG/pixel oracles including all font glyphs and nine box validation cases')
edge=[]
for x in [.4921875-1e-10,.4921875,.4921875+1e-10,.5078125-1e-10,.5078125,.5078125+1e-10]:
    boxes=[box('ffi phase',[20+x,60+x,200+x,100+x],.125)]
    with patch.object(main,'connection',Connection):response=main.detection_chart(UUID(int=42))
    png=response.body;pixels=Image.open(io.BytesIO(png)).convert('RGB').tobytes()
    edge.append({'boxes':boxes,'png':base64.b64encode(png).decode(),'image_hash':hashlib.sha256(png).hexdigest(),'pixel_hash':hashlib.sha256(pixels).hexdigest()})
path=ROOT/f'apps/backend-rust/tests/fixtures/annotation-edge-parity{suffix}.json';blob=canonical({'cases':edge}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen original subpixel edge fixtures differ'
else:path.write_bytes(blob)
print('Saved six C-float text-position threshold edge cases')
