"""Freeze the complete fixed Pillow default font rasterization, not a replacement font."""
import base64,hashlib,json,struct
from pathlib import Path
from PIL import ImageFont,features,__version__
ROOT=Path(__file__).resolve().parents[1]
font=ImageFont.load_default()
assert font.size==10 and int(font.layout_engine)==0
data=font.font_bytes
tables={}
for i in range(struct.unpack_from('>H',data,4)[0]):
    tag,checksum,offset,length=struct.unpack_from('>4sIII',data,12+16*i);tables[tag]=(offset,length)
cmap=tables[b'cmap'][0];mapping={}
for i in range(struct.unpack_from('>H',data,cmap+2)[0]):
    platform,encoding,offset=struct.unpack_from('>HHI',data,cmap+4+8*i)
    if platform not in (0,3):continue
    start=cmap+offset;format=struct.unpack_from('>H',data,start)[0]
    if format==4:
        count=struct.unpack_from('>H',data,start+6)[0]//2
        ends=start+14;starts=ends+2*count+2;deltas=starts+2*count;ranges=deltas+2*count
        for segment in range(count):
            lo=struct.unpack_from('>H',data,starts+2*segment)[0];hi=struct.unpack_from('>H',data,ends+2*segment)[0]
            delta=struct.unpack_from('>h',data,deltas+2*segment)[0];ro=struct.unpack_from('>H',data,ranges+2*segment)[0]
            for cp in range(lo,hi+1):
                glyph=(cp+delta)&65535 if ro==0 else struct.unpack_from('>H',data,ranges+2*segment+ro+2*(cp-lo))[0]
                if ro and glyph:glyph=(glyph+delta)&65535
                if glyph:mapping[cp]=glyph
    elif format==12:
        for group in range(struct.unpack_from('>I',data,start+12)[0]):
            lo,hi,glyph=struct.unpack_from('>III',data,start+16+12*group)
            for cp in range(lo,hi+1):
                if glyph+cp-lo:mapping[cp]=glyph+cp-lo
    else:raise AssertionError('Unexpected Unicode cmap format')
glyphs={}
for cp in [0x10ffff,*sorted(mapping)]:
    text=chr(cp);mask,offset=font.getmask2(text,mode='L')
    glyphs[str(cp)]={'size':mask.size,'offset':offset,'advance64':round(font.getlength(text)*64),'mask':base64.b64encode(bytes(mask)).decode()}
kerning={}
for a in mapping:
    for b in mapping:
        delta=round(font.getlength(chr(a)+chr(b))*64)-glyphs[str(a)]['advance64']-glyphs[str(b)]['advance64']
        if delta:kerning[f'{a},{b}']=delta
atlas={'font':'Pillow embedded Aileron Regular subset','font_sha256':hashlib.sha256(data).hexdigest(),'copyright_name_field':'No Rights Reserved.',
       'pillow':__version__,'freetype':features.version('freetype2'),'size':10,'layout':'BASIC','line_height':font.getbbox('A')[3]+4,'fallback':0x10ffff,'glyphs':glyphs,'kerning64':kerning}
blob=json.dumps(atlas,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
path=ROOT/'apps/backend-rust/assets/pillow-default-font-10.json'
if path.exists():assert path.read_bytes()==blob,'Frozen font rasterization differs'
else:path.write_bytes(blob)
print(json.dumps({'glyphs':len(glyphs),'kerning_pairs':len(kerning),'font_sha256':atlas['font_sha256'],'freetype':atlas['freetype'],'atlas_sha256':hashlib.sha256(blob).hexdigest()}))
