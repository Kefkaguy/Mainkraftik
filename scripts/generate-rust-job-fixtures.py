"""Original job completion and scheduler oracles without DB or workflow execution."""
import json,sys,logging
from datetime import datetime,UTC
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence import jobs
from intelligence.util import canonical
job_id=UUID('00000000-0000-4000-8000-000000000042')
class Rows:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=None):
        if 'pg_try_advisory_lock' in sql:return Rows({'acquired':True})
        if "UPDATE jobs SET status='queued'" in sql:return Rows([])
        if 'SELECT * FROM jobs WHERE status=' in sql:return Rows({'id':job_id,'kind':kind,'payload':{}})
        if 'INSERT INTO job_events' in sql:events.append({'event':'started' if "'started'" in sql else args[1],'detail':args[-1].obj if hasattr(args[-1],'obj') else {}})
        return Rows(None)
    def commit(self):pass
cases=[]
logging.disable(logging.CRITICAL)
for kind,result,failure in [('refresh',{'status':'partial'},None),('issue',{'status':'skipped'},None),('research',{'status':'failed'},None),('resolve',{'count':3},None),('evaluate',None,'value'),('vision',None,'unexpected')]:
    events=[]
    def dispatch(*args):
        if failure=='value':raise ValueError('Safe validation '+('界'*550))
        if failure=='unexpected':raise RuntimeError('PRIVATE INTERNAL ERROR')
        return result
    with patch.object(jobs,'connection',Connection),patch.object(jobs,'dispatch',dispatch):actual=jobs.run_once()
    cases.append({'kind':kind,'dispatch_result':result,'failure':failure,'expected':actual,'events':events})
logging.disable(logging.NOTSET)
class ScheduleConnection:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=None):
        if 'scheduler_marks WHERE' in sql:return Rows(None)
        if "kind='refresh'" in sql:return Rows({'pending':True} if pending else None)
        if 'INSERT INTO scheduler_marks' in sql:scheduled.append([args[0],calls[-1]])
        return Rows(None)
windows=[]
for text in ['2026-11-18T18:59:59-05:00','2026-11-18T19:00:00-05:00','2026-11-18T19:30:00-05:00','2026-11-18T19:50:00-05:00','2026-11-18T20:00:00-05:00','2026-11-18T20:30:00-05:00','2026-11-18T20:35:00-05:00','2026-11-18T23:59:59-05:00','2026-11-26T20:35:00-05:00','2026-03-09T20:35:00-04:00']:
    for pending in (False,True):
        calls=[];scheduled=[]
        def enqueue(kind,**kwargs):calls.append(kind);return job_id
        with patch.object(jobs,'connection',ScheduleConnection),patch.object(jobs,'enqueue',enqueue),patch.object(jobs,'now',return_value=datetime.fromisoformat(text)):jobs.schedule_jobs()
        windows.append({'instant':text,'pending_refresh':pending,'tasks':scheduled})
path=ROOT/'apps/backend-rust/tests/fixtures/job-parity.json';blob=canonical({'completions':cases,'schedule':windows}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen job fixture differs'
else:path.write_bytes(blob)
print('Saved six original job completion/failure cases and twenty scheduler boundary cases')
