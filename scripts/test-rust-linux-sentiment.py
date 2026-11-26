"""Run the two real native ONNX/HTTP tests in the pinned local WSL environment."""
from datetime import UTC,datetime
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
assert os.name=='nt'
project='/mnt/'+ROOT.drive[0].lower()+'/'+ROOT.relative_to(ROOT.anchor).as_posix()
python='/home/erosimcity/.local/share/mainkraft-rust-validation/sentiment-reference/bin/python'
destination=ROOT/'.local/rust-migration/linux-sentiment'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True,exist_ok=False)
command=['wsl','-d','Ubuntu','--',python,project+'/scripts/test-rust-sentiment.py','--reference-platform','linux']
result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
output=result.stdout+'\n'+result.stderr
(destination/'native.txt').write_text(output,encoding='utf-8')
count=sum(map(int,re.findall(r'test result: ok\. (\d+) passed;',output)))
metadata=subprocess.run(['wsl','-d','Ubuntu','--',python,'-m','pip','freeze'],capture_output=True,text=True)
(destination/'python-packages.txt').write_text(metadata.stdout+metadata.stderr,encoding='utf-8')
passed=result.returncode==0 and count==2 and metadata.returncode==0
references={}
for name in ['sentiment-inference-parity-linux.json','sentiment-http-validation-linux.json']:
 source=ROOT/'apps/backend-rust/tests/fixtures'/name
 references[name]=hashlib.sha256(source.read_bytes()).hexdigest()
 (destination/name).write_bytes(source.read_bytes())
report={'at':datetime.now(UTC).isoformat(),'passed':passed,'tests_passed':count,'exit_code':result.returncode,'command':command,'references':references,'model_retrained':False,'broker_requests':0,'deployment_allowed':False,'full_linux_validation_complete':False,'outputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}}
(destination/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
if passed:
 (ROOT/'.local/rust-migration/latest-linux-sentiment.json').write_text(json.dumps({'directory':destination.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256((destination/'result.json').read_bytes()).hexdigest()}))
else:print(output)
print(json.dumps({'passed':passed,'tests_passed':count,'directory':destination.relative_to(ROOT).as_posix()}),flush=True)
sys.exit(0 if passed else 1)
