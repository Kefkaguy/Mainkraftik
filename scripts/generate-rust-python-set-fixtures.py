"""Capture real CPython hash/set/Counter ordering; no market data, DB or network."""
import argparse,ast,hashlib,json,os,platform,subprocess,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'apps/api/paperpilot/discovery.py'
def symbol(i):
 value=''
 while True:
  value=chr(65+i%26)+value;i=i//26
  if not i:return 'X'+value
def examples():
 names=[symbol(i) for i in range(320)]
 cases=[]
 for n in [0,1,2,4,5,6,18,19,20,76,77,78,300]:
  cases.append(('size-'+str(n),[names[:n]]))
 cases.extend([
  ('duplicates',[['MSI','AXON','MSI','MSI','AXON'],['SPY','AXON','MSI']]),
  ('unicode-and-excluded',[['MSI','信号','AXON','é','😀','', 'A.A','OMEGA'],['AXON','MSI','SPY','Ω']]),
  ('frequency',[names[:80],list(reversed(names[20:100])),names[::3],names[40:120]]),
  ('sixty-cutoff',[names[:90]]),
  ('article-order',[list(reversed(names[:20])),names[20:40],names[::4],names[:5]]),
 ])
 groups=defaultdict(list)
 for i in range(20000):groups[hash(symbol(i))&127].append(symbol(i))
 colliding=max(groups.values(),key=len)[:40]
 cases.append(('hash-collisions',[colliding,colliding[:7],list(reversed(colliding[5:30]))]))
 return cases
def worker(extended=False):
 assert sys.hash_info.algorithm=='siphash13' and sys.hash_info.width==64 and sys.hash_info.cutoff==0
 tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
 research=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='research')
 counter=next(n for n in research.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='count' for t in n.targets))
 code=compile(ast.Module(body=[counter],type_ignores=[]),'original-discovery-counter','exec')
 cases=[]
 for name,lists in examples():
  articles={str(i):{'symbols':values} for i,values in enumerate(lists)}
  valid={s:{'symbol':s} for values in lists for s in values if s.isascii() and s.isalpha() and s.isupper() and 1<=len(s)<=5}
  namespace={'Counter':Counter,'articles':articles,'valid':valid};exec(code,namespace)
  cases.append({'name':name,'articles':list(articles.items()),'valid':valid,'set_order':[list(set(values)) for values in lists],'hashes':{s:hash(s) for values in lists for s in values},'expected':namespace['count'].most_common(60)})
 result={'python':platform.python_version(),'algorithm':sys.hash_info.algorithm,'seed':int(os.environ['PYTHONHASHSEED']),'cases':cases}
 if extended:
  large=[]
  for n in [307,1229,4915,19661,78643]:
   order=list(set(symbol(i) for i in range(n)))
   large.append({'size':n,'first60':order[:60],'order_sha256':hashlib.sha256(json.dumps(order,separators=(',',':')).encode()).hexdigest()})
  result['large']=large
 return result
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',action='store_true');parser.add_argument('--extended',action='store_true');parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows');parser.add_argument('--output',type=Path);args=parser.parse_args()
 if args.worker:print(json.dumps(worker(args.extended),ensure_ascii=True));return
 assert platform.system().lower()==args.reference_platform
 assert args.output,'An explicit new reference path is required'
 results=[]
 for seed in [0,1,17,20261118,4294967295]:
  env=dict(os.environ,PYTHONHASHSEED=str(seed))
  result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',*(['--extended'] if args.extended else [])],env=env,capture_output=True,text=True,encoding='utf-8',check=True)
  results.append(json.loads(result.stdout))
 fixture={'reference_platform':args.reference_platform,'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'synthetic_only':True,'references':results}
 data=json.dumps(fixture,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
 args.output.parent.mkdir(parents=True,exist_ok=True)
 if args.output.exists():assert args.output.read_bytes()==data,'Original hash/set oracle differs; never overwrite'
 else:
  with args.output.open('xb') as f:f.write(data)
 print(json.dumps({'platform':args.reference_platform,'seeds':len(results),'cases':sum(len(r['cases']) for r in results),'sha256':hashlib.sha256(data).hexdigest(),'output':args.output.as_posix(),'market_data_used':False}))
if __name__=='__main__':main()
