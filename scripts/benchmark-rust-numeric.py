"""Measured local component comparison; no broker, DB, or strategy evaluation."""
import argparse
import ctypes
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'apps/backend-rust/tests/fixtures/python-parity.json'


def python_child(iterations):
    sys.path.insert(0,str(ROOT/'apps/api'))
    from intelligence.portable_models import predict
    data=json.loads(FIXTURE.read_text())['numeric']
    for case in data:
        for x in case['inputs']:predict(case['artifact'],[{'x':x}])
    start=time.perf_counter();cpu_start=time.process_time();checksum=0.;count=0
    for _ in range(iterations):
        for case in data:
            for x in case['inputs']:
                m,p,_,_=predict(case['artifact'],[{'x':x}]);checksum+=float(m[0]+p[0]);count+=1
    elapsed=time.perf_counter()-start
    print(json.dumps({'implementation':'python','scope':'single-row numeric inference only; not full trading service','iterations':iterations,
                     'predictions':count,'compute_wall_seconds':elapsed,'predictions_per_second':count/elapsed,'checksum':checksum,
                     'compute_cpu_seconds':time.process_time()-cpu_start,'actual_executable':sys.executable}))


class Memory(ctypes.Structure):
    _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong),('PeakWorkingSetSize',ctypes.c_size_t),
              ('WorkingSetSize',ctypes.c_size_t),('QuotaPeakPagedPoolUsage',ctypes.c_size_t),('QuotaPagedPoolUsage',ctypes.c_size_t),
              ('QuotaPeakNonPagedPoolUsage',ctypes.c_size_t),('QuotaNonPagedPoolUsage',ctypes.c_size_t),
              ('PagefileUsage',ctypes.c_size_t),('PeakPagefileUsage',ctypes.c_size_t)]


def measured(command,output):
    if os.name!='nt':raise ValueError('This harness measures Windows process CPU/RAM; add a separate Linux harness before comparing VPS services')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Memory),ctypes.c_ulong]
    kernel.GetProcessTimes.argtypes=[ctypes.c_void_p,*([ctypes.POINTER(ctypes.c_ulonglong)]*4)]
    environment=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    # Windows venv python.exe is a redirector which spawns the real interpreter.
    # Measure the actual interpreter directly, using the exact same venv wheels.
    environment['PYTHONPATH']=os.pathsep.join([str(ROOT/'.venv/Lib/site-packages'),environment.get('PYTHONPATH','')]).rstrip(os.pathsep)
    with output.open('w') as stdout,output.with_suffix('.stderr.txt').open('w') as stderr:
        start=time.perf_counter()
        child=subprocess.Popen(command,cwd=ROOT,env=environment,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
        handle=kernel.OpenProcess(0x0400|0x0010,False,child.pid)
        if not handle:child.wait();raise ctypes.WinError(ctypes.get_last_error())
        peak=0
        try:
            while child.poll() is None:
                memory=Memory();memory.cb=ctypes.sizeof(memory)
                if psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.cb):peak=max(peak,memory.PeakWorkingSetSize)
                time.sleep(.005)
            elapsed=time.perf_counter()-start
            created,exited,system,user=[ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            cpu=(system.value+user.value)/1e7
        finally:kernel.CloseHandle(handle)
    if child.returncode:raise RuntimeError(output.with_suffix('.stderr.txt').read_text()[-1200:])
    result=json.loads(output.read_text());result.update(process_wall_seconds=elapsed,process_cpu_seconds=cpu,
        sampled_peak_working_set_bytes=peak,sampling_interval_ms=5,pid=child.pid)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',action='store_true');parser.add_argument('--iterations',type=int,default=30);parser.add_argument('--repetitions',type=int,default=3)
    args=parser.parse_args()
    if args.child:return python_child(args.iterations)
    executable=ROOT/'apps/backend-rust/target/release/mainkraft.exe'
    if not executable.exists():raise ValueError('Build the release binary before benchmarking')
    work=ROOT/'.local/rust-migration/benchmark';work.mkdir(exist_ok=True)
    runs=[]
    for repetition in range(args.repetitions):
        for name,command in [('python',[sys._base_executable,__file__,'--child','--iterations',str(args.iterations)]),
                             ('rust',[str(executable),'benchmark-numeric','--fixture',str(FIXTURE),'--iterations',str(args.iterations)])]:
            result=measured(command,work/f'{name}-{repetition}.json');runs.append(result)
            print(name,repetition,'compute seconds',round(result['compute_wall_seconds'],5),'peak MiB',round(result['sampled_peak_working_set_bytes']/1024**2,2),flush=True)
    expected=runs[0]['checksum'];assert all(abs(r['checksum']-expected)<1e-8+abs(expected)*1e-10 for r in runs)
    summary={name:{field:statistics.median(r[field] for r in runs if r['implementation']==name) for field in
                  ('compute_wall_seconds','predictions_per_second','process_wall_seconds','process_cpu_seconds','sampled_peak_working_set_bytes')}
             for name in ('python','rust')}
    report={'scope':'LOCAL WINDOWS NUMERIC COMPONENT ONLY; not paper-engine, API, ingestion, research or VPS performance',
            'platform':platform.platform(),'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            'rust_binary_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'iterations':args.iterations,
            'repetitions':args.repetitions,'threads':1,'runs':runs,'median':summary,
            'limitations':['Startup-inclusive process CPU/RAM, separately timed warmed inference loop','RAM is sampled Windows peak working set; not Linux RSS','No broker network, DB, sentiment or full-service workloads included','No system-wide speed claim supported by this component benchmark']}
    path=ROOT/'reports/rust-migration/numeric-benchmark-windows.json';path.write_text(json.dumps(report,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
