"""Freeze original Python 3.14 information timestamps; no network, DB or history writes."""
import _pydatetime,hashlib,json,random,sys,unicodedata
from datetime import UTC,datetime,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.information import stamp
from intelligence.util import canonical
inputs=[]
dates=['2026-11-20','20261120','2026-W47-5','2026W475','2026-W47','2026W47','2020-W53-7','0001-01-01','9999-12-31']
times=['12','1234','12:34','123456','12:34:56','12:34:56.123456789','12:34:56,1','24:00','24:00:00.0000000']
zones=['Z','+00','-00:00','+0530','-08:00','+01:02:03.1234567','-00:00:00.000001','+00:00:00.000001','+01:60','+23:59:59.999999','-23:59:59.999999','']
for date in dates:
    inputs.append(date)
    for time in times:
        for zone in zones:inputs.append(date+'T'+time+zone)
for sep in [' ','t','\t','\n','\x00','\U0001f600','1','-','Z']:
    for date in dates:
        inputs.extend([date+sep+'12:34:56+00:00',date+sep,date+sep+'12:34:56z'])
inputs += ['', 'invalid',"not'a'time",'not"a"time','both\'and"quotes','\x00\x01\x1f\x7f\x85\u200b\u00a0',
 '2026-11-20T23:59:60Z','2026-11-20T24:00:01Z','2026-11-20T25:00Z','2026-11-20T12:60Z','2026-11-20T12:00:60Z',
 '2026-11-20T12:00:00+24:00','2026-11-20T12:00:00-24:00','2026-11-20T12:00:00+00:99',
 '2026-11-20T12.5Z','2026-11-20T12:34.5Z','2026-11-20T12:34:56.Z','2026-11-20T12:34:56.123xZ',
 '2026-11-20Z12:34:56Z','2026-11-20T12:00:00ZZ','2026-11-20T12:00:00+00:00Z',
 '2026-02-30T12Z','2026-13-01T12Z','2026-00-01T12Z','2026-01-00T12Z','0000-01-01T12Z',
 '0001-01-01T00+01','9999-12-31T23-01','9999-12-31T24Z','9999-W52-7T00Z',
 '2026-W00-1T12Z','2026-W54-1T12Z','2026-W47-0T12Z','2026-W47-8T12Z',
 '2026-111-20T12Z','202611-20T12Z','2026-1120T12Z','２０２６-１１-２０T１２Z',
 '2026-11-20T12:34:56+00:00:00.000001','2026-11-20T12:34:56-00:00:00.000001']
rng=random.Random(20261121)
seeds=['2026-11-20T12:34:56.123456Z','2026W475T123456+0530','2026-W47-5T12:34:56-08:00']
for _ in range(800):
    text=rng.choice(seeds);pos=rng.randrange(len(text)+1);token=rng.choice(['','0','9','-',':','.','Z','z','W','x','\n','\U0001f600'])
    inputs.append(text[:pos]+token+text[pos+1:])
epoch=datetime(1970,1,1,tzinfo=UTC)
def capture(text):
    case={'input':text}
    try:
        result=stamp(text);case['result']=result.isoformat(timespec='microseconds').replace('+00:00','Z');case['micros']=(result-epoch)//timedelta(microseconds=1)
    except Exception as e:case.update(kind=type(e).__name__,message=str(e))
    return case
rows=[capture(text) for text in dict.fromkeys(inputs)]
source=Path(_pydatetime.__file__)
fixture={'reference':sys.version,'helper_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'cases':rows}
path=ROOT/'apps/backend-rust/tests/fixtures/information-time.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen timestamp oracle differs'
else:path.write_bytes(blob)
print(json.dumps({'cases':len(rows),'accepted':sum('result' in r for r in rows),'fixture':path.relative_to(ROOT).as_posix()}))
ranges=[];start=None
for code in range(0x110000):
    hidden=not chr(code).isprintable()
    if hidden and start is None:start=code
    if not hidden and start is not None:ranges.append([start,code-1]);start=None
if start is not None:ranges.append([start,0x10ffff])
table=ROOT/'apps/backend-rust/assets/python-nonprinting.json';table_blob=json.dumps(ranges,separators=(',',':')).encode()
assert table.read_bytes()==table_blob,'Frozen Python Unicode repr table differs'
boundaries=[]
for year in ['0000','0001','1900','2000','9999']:
    for month in ['00','01','02','12','13']:
        for day in ['00','01','28','29','30','31','32']:
            for hour in ['00','24','25']:
                for zone in ['','Z','+24:00']:boundaries.append(f'{year}-{month}-{day}T{hour}:00:00{zone}')
for hour in ['00','01','23','24','99']:
    for minute in ['00','59','60','99']:
        for second in ['00','59','60','99']:
            for fraction in ['','.000001','.9999999']:
                for sign in ['+','-']:boundaries.append(f'2026-11-20T12:34:56{sign}{hour}:{minute}:{second}{fraction}')
for start,end in ranges:
    for code in [start,end]:
        if not 0xd800<=code<=0xdfff:boundaries.append('bad'+chr(code))
for clock in ['12','12:34','12:34:56','123456','12:34:56.123456789']:
    for suffix in ['.','x','\x00','\x00junk','\x00+00:00','x+00:00','.+00:00',',+00:00','..+00:00']:
        boundaries.append('2026-11-20T'+clock+suffix)
rng=random.Random(519410)
for _ in range(3000):
    text=rng.choice(seeds);pos=rng.randrange(len(text)+1);width=rng.randrange(4);token=rng.choice(['','00','99','-','::','..','Z','z','W','x','\x00','\t','\U0001f600'])
    boundaries.append(text[:pos]+token+text[pos+width:])
extra={'cases':[capture(text) for text in dict.fromkeys(boundaries)]}
boundary_file=ROOT/'apps/backend-rust/tests/fixtures/information-time-boundaries.json';blob=canonical(extra).encode()
if boundary_file.exists():assert boundary_file.read_bytes()==blob,'Frozen boundary timestamp oracle differs'
else:boundary_file.write_bytes(blob)
provenance={'cpython_version':'3.14.2','datetime_source_url':'https://raw.githubusercontent.com/python/cpython/v3.14.2/Modules/_datetimemodule.c','datetime_source_sha256':'b220d2766e04cb95b05e00231759622e3f8df3b77391f6542cedebbeb6ccb897','python_helper_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'unicode_version':unicodedata.unidata_version,'nonprinting_ranges':len(ranges),'nonprinting_table_sha256':hashlib.sha256(table_blob).hexdigest(),'reference_fixture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'boundary_fixture_sha256':hashlib.sha256(blob).hexdigest()}
provenance_file=ROOT/'apps/backend-rust/third-party/python-time-provenance.json';blob=json.dumps(provenance,indent=2).encode()
if provenance_file.exists():assert provenance_file.read_bytes()==blob,'Frozen datetime provenance differs'
else:provenance_file.write_bytes(blob)
print(json.dumps({'boundary_cases':len(extra['cases']),'accepted':sum('result' in r for r in extra['cases']),'nonprinting_ranges':len(ranges)}))
