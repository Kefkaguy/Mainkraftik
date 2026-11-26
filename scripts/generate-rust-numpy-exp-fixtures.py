"""Pinned NumPy ndarray float32 exp bit patterns; synthetic math inputs only."""
import argparse,hashlib,json,platform
from pathlib import Path
import numpy as np
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows');parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
assert platform.system().lower()==args.reference_platform and np.__version__=='2.5.4'
features=np._core._multiarray_umath.__cpu_features__
assert features['AVX2'] and features['FMA3'],'This corpus identifies the approved x86 FMA ndarray profile'
rng=np.random.default_rng(20261122)
bits=np.unique(np.concatenate([np.linspace(-110,90,8193,dtype=np.float32).view(np.uint32),np.linspace(-104,0,4097,dtype=np.float32).view(np.uint32),rng.integers(0,2**32,10000,dtype=np.uint32),np.asarray([0,0x80000000,0x7f800000,0xff800000,0x42b17217,0x42b17218,0xc2cff1b4,0xc2cff1b5],dtype=np.uint32)]))
values=bits.view(np.float32);mask=~np.isnan(values);values=values[mask];bits=bits[mask]
with np.errstate(all='ignore'):expected=np.exp(values).view(np.uint32)
fixture={'version':1,'profile':'numpy-2.5.4-ndarray-f32-fma','reference_platform':args.reference_platform,'numpy':np.__version__,'cpu_features':{k:bool(features[k]) for k in ['AVX2','FMA3','AVX512F','AVX512_SKX']},'synthetic_only':True,'input_bits':bits.tolist(),'expected_bits':expected.tolist()}
data=json.dumps(fixture,sort_keys=True,separators=(',',':')).encode();args.output.parent.mkdir(parents=True,exist_ok=True)
if args.output.exists():assert args.output.read_bytes()==data,'Pinned exp oracle differs; do not overwrite'
else:
 with args.output.open('xb') as f:f.write(data)
print(json.dumps({'inputs':len(bits),'sha256':hashlib.sha256(data).hexdigest(),'output':args.output.as_posix(),'market_data_used':False}))
