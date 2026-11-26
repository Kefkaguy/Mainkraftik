"""Local WSL native component gate; no installers, services, broker calls or deployment."""
import hashlib,json,os,re,subprocess,sys
from datetime import UTC,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
assert os.name=='nt','This orchestration helper targets the current Windows + local Ubuntu WSL environment'
project='/mnt/'+ROOT.drive[0].lower()+'/'+ROOT.relative_to(ROOT.anchor).as_posix()
reference='/home/erosimcity/.local/share/mainkraft-rust-validation/python-reference/bin/python'
destination=ROOT/'.local/rust-migration/linux-core'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True,exist_ok=False)
sentiment_reference='/home/erosimcity/.local/share/mainkraft-rust-validation/sentiment-reference/bin/python'
steps=[('oracle',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-point-fixtures.py','--reference-platform','linux']),
       ('charts',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-chart-fixtures.py','--reference-platform','linux']),
       ('annotations',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-annotation-fixture.py','--reference-platform','linux']),
       ('training',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-training-fixtures.py','--reference-platform','linux']),
       ('evaluation',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-evaluation-fixtures.py','--reference-platform','linux']),
       ('python-set',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-python-set-fixtures.py','--reference-platform','linux','--output',project+'/apps/backend-rust/tests/fixtures/python-set-parity-linux.json']),
       ('python-set-resize',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-python-set-fixtures.py','--reference-platform','linux','--extended','--output',project+'/apps/backend-rust/tests/fixtures/python-set-extended-linux.json']),
       ('numpy-exp',['wsl','-d','Ubuntu','--',sentiment_reference,project+'/scripts/generate-rust-numpy-exp-fixtures.py','--reference-platform','linux','--output',project+'/apps/backend-rust/tests/fixtures/numpy-exp-parity-linux.json']),
       ('sentiment-assembly',['wsl','-d','Ubuntu','--',sentiment_reference,project+'/scripts/generate-rust-research-sentiment-fixture.py','--reference-platform','linux']),
       ('active-engine',['wsl','-d','Ubuntu','--',reference,project+'/scripts/generate-rust-active-engine-fixture.py','--reference-platform','linux']),
       ('native',['wsl','-d','Ubuntu','--','bash',project+'/scripts/test-rust-linux.sh'])]
checks=[]
for name,command in steps:
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (destination/(name+'.txt')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    checks.append({'name':name,'command':command,'exit_code':result.returncode})
    print(name,'PASS' if result.returncode==0 else 'FAIL',flush=True)
    if result.returncode:break
passed=len(checks)==len(steps) and all(c['exit_code']==0 for c in checks)
count=sum(map(int,re.findall(r'test result: ok\. (\d+) passed;', (destination/'native.txt').read_text()))) if (destination/'native.txt').exists() else 0
report={'at':datetime.now(UTC).isoformat(),'passed':passed,'ordinary_tests':count,'checks':checks,'fixture_runtime':json.loads((ROOT/'apps/backend-rust/tests/fixtures/point-features-linux-runtime.json').read_text()),'full_linux_validation_complete':False,'remaining':['isolated PostgreSQL suites on Linux','native ONNX runtime and original Python worker cross-validation on Linux','IR HTTP and evidence cross-reader orchestration on Linux','deployment environment compatibility and complete service validation'],'deployment_allowed':False,'outputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}}
(destination/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
if passed:
    pointer={'directory':destination.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256((destination/'result.json').read_bytes()).hexdigest()}
    (ROOT/'.local/rust-migration/latest-linux-core.json').write_text(json.dumps(pointer),encoding='utf-8')
print(json.dumps({'passed':passed,'ordinary_tests':count,'directory':destination.relative_to(ROOT).as_posix()}))
sys.exit(0 if passed else 1)
