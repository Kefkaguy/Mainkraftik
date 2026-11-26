"""Read-only, bounded synthetic reference checks in the existing Mainkraft containers.

No service/engine/broker module is imported. No keys, account data, network market
requests, model fitting, files or service settings are changed on the VPS.
"""
import ast
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shlex
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from vps_access import run

generator=ROOT/'scripts/generate-rust-python-set-fixtures.py'
tree=ast.parse(generator.read_text(encoding='utf-8'))
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('symbol','examples','worker')]
assert len(functions)==3
worker="""import ast,hashlib,json,os,platform,sys
from collections import Counter,defaultdict
from pathlib import Path
SOURCE=Path('/app/apps/api/paperpilot/discovery.py')
"""+ast.unparse(ast.Module(body=functions,type_ignores=[]))+"\nprint(json.dumps({'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'reference':worker(True)},ensure_ascii=True))"
numpy_probe="""import hashlib,json,platform,sys
import numpy as np
assert np.__version__=='2.5.4'
rng=np.random.default_rng(20261122)
bits=np.unique(np.concatenate([np.linspace(-110,90,8193,dtype=np.float32).view(np.uint32),np.linspace(-104,0,4097,dtype=np.float32).view(np.uint32),rng.integers(0,2**32,10000,dtype=np.uint32),np.asarray([0,0x80000000,0x7f800000,0xff800000,0x42b17217,0x42b17218,0xc2cff1b4,0xc2cff1b5],dtype=np.uint32)]))
values=bits.view(np.float32);mask=~np.isnan(values);values=values[mask];bits=bits[mask]
with np.errstate(all='ignore'):expected=np.exp(values).view(np.uint32)
print(json.dumps({'python':platform.python_version(),'machine':platform.machine(),'numpy':np.__version__,'inputs':len(bits),'input_sha256':hashlib.sha256(bits.astype('<u4').tobytes()).hexdigest(),'expected_sha256':hashlib.sha256(expected.astype('<u4').tobytes()).hexdigest(),'cpu_features':{k:bool(np._core._multiarray_umath.__cpu_features__[k]) for k in ['AVX2','FMA3','AVX512F','AVX512_SKX']}}))
"""
outer="""import json,subprocess
def qualified(name,service):
 r=subprocess.run(['docker','inspect','--format','{{json .Config.Labels}}',name],capture_output=True,text=True,check=True)
 labels=json.loads(r.stdout)
 assert labels['com.docker.compose.project']=='keftrade-intelligence' and labels['com.docker.compose.service']==service
def probe(name,code,seed=None):
 command=['docker','exec']
 if seed is not None:command+=['-e','PYTHONHASHSEED='+str(seed)]
 command+=[name,'python','-B','-c',code]
 r=subprocess.run(command,capture_output=True,text=True,timeout=20)
 if r.returncode!=0:raise RuntimeError('Synthetic runtime probe failed: '+r.stderr[-1800:])
 return json.loads(r.stdout)
qualified('keftrade-intelligence-paper-1','paper')
qualified('keftrade-intelligence-sentiment-1','sentiment')
"""
outer+='counter=[probe("keftrade-intelligence-paper-1",'+repr(worker)+',seed) for seed in [0,1,17,20261118,4294967295]]\n'
outer+='numeric=probe("keftrade-intelligence-sentiment-1",'+repr(numpy_probe)+')\n'
outer+='print(json.dumps({"counter":counter,"numpy":numeric}))\n'
destination=ROOT/'reports/rust-migration/runtime-observations'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ-deployed-synthetic-reference')
destination.mkdir(parents=True,exist_ok=False)
result=run('python3 -c '+shlex.quote(outer))
(destination/'remote-command.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
if result['exit_code']!=0:
    raise RuntimeError('Read-only runtime check failed; no service changes attempted: '+result['stderr'][-2000:])
observed=json.loads(result['stdout'])
(destination/'observed.json').write_text(json.dumps(observed,indent=2,ensure_ascii=True),encoding='utf-8')
counter=ROOT/'apps/backend-rust/tests/fixtures/python-set-extended-linux.json'
expected=json.loads(counter.read_text(encoding='utf-8'))
assert len(observed['counter'])==len(expected['references'])==5
for actual,reference in zip(observed['counter'],expected['references']):
    assert actual['source_sha256']==expected['source_sha256']
    a=dict(actual['reference']);b=dict(reference);a.pop('python');b.pop('python')
    assert a==b,'Deployed CPython set/Counter profile differs from the frozen reference'
exp=ROOT/'apps/backend-rust/tests/fixtures/numpy-exp-parity-linux.json'
expected_exp=json.loads(exp.read_text(encoding='utf-8'));actual_exp=observed['numpy']
for key,label in [('input_bits','input_sha256'),('expected_bits','expected_sha256')]:
    bits=expected_exp[key];blob=struct.pack('<'+str(len(bits))+'I',*bits)
    assert hashlib.sha256(blob).hexdigest()==actual_exp[label],'Deployed NumPy exp bit patterns differ'
assert actual_exp['inputs']==len(expected_exp['input_bits'])
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
receipt={'at':datetime.now(UTC).isoformat(),'passed':True,'synthetic_only':True,'source_files_modified':False,
         'service_changes':False,'broker_requests':0,'training':False,'market_data_used':False,
         'python':observed['counter'][0]['reference']['python'],'numpy':actual_exp['numpy'],
         'counter_seeds':5,'counter_cases':sum(len(r['reference']['cases']) for r in observed['counter']),
         'large_set_cases':sum(len(r['reference']['large']) for r in observed['counter']),
         'exp_bit_patterns':actual_exp['inputs'],'source_files':{p.relative_to(ROOT).as_posix():sha(p) for p in [Path(__file__),generator,counter,exp]},
         'outputs':{name:sha(destination/name) for name in ['observed.json','remote-command.json']},'deployment_allowed':False}
(destination/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'receipt':(destination/'receipt.json').relative_to(ROOT).as_posix(),'sha256':sha(destination/'receipt.json'),
                  'python':receipt['python'],'numpy':receipt['numpy'],'counter_cases':receipt['counter_cases'],
                  'large_set_cases':receipt['large_set_cases'],'exp_bit_patterns':receipt['exp_bit_patterns'],'broker_requests':0,'service_changes':False}))
