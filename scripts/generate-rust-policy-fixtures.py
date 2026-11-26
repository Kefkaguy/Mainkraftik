"""Parity-only reference export; no broker API or production DB connections."""
import json
import sys
from datetime import date,datetime,UTC,timedelta
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import schedule
from paperpilot.experiment import allocate,quantity_change,target_time
from paperpilot.strategy import features


def immutable(path,blob):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():assert path.read_bytes()==blob
    else:path.write_bytes(blob)


def main():
    calendar=schedule(date(2000,1,1),date(2040,12,31))
    sessions=[{'date':str(d),'open':s['open'].isoformat(),'close':s['close'].isoformat()} for d,s in calendar.items()]
    immutable(ROOT/'apps/backend-rust/assets/exchange-sessions.json',json.dumps(sessions,separators=(',',':')).encode())
    rng=np.random.default_rng(20261120);observations=[]
    for symbol in ('AMD','UBS','MSFT','NVDA','ALM','AS'):
        observations.append({'symbol':symbol,'x':[1.,*rng.normal(0,.03,14).tolist()],'prior_return':float(rng.normal(0,.005)),
            'uncertainty':float(rng.uniform(.0001,.02)),'price':101.01,'midpoint':101.,'shortable':symbol!='ALM',
            'source_id':'parity-source','asset':{'fractionable':True,'shortable':True},'cost_allowance':.0014})
    allocations=[]
    for adaptive in (False,True):
        for correction in (None,rng.normal(0,.0001,15).tolist()):
            for capacity in (0.,29986.44,2000000.):
                slot='account-id5481212';expected=allocate(observations,capacity,adaptive,slot,correction)
                allocations.append({'observations':observations,'capacity':capacity,'adaptive':adaptive,'slot':slot,'correction':correction,'expected':expected})
    quantities=[]
    for _ in range(300):
        current=str(round(float(rng.uniform(-300,300)),9)) if rng.random()>.2 else None
        target=float(rng.uniform(-2e6,2e6));price=float(rng.uniform(.01,2000))
        fractional=bool(rng.integers(0,2));shortable=bool(rng.integers(0,2))
        quantities.append({'current':current,'target':target,'price':price,'fractionable':fractional,'shortable':shortable,
            'expected':quantity_change({'qty':current} if current else None,target,price,fractional,shortable)})
    targets=[]
    for instant in ['2026-11-25T20:30:00+00:00','2026-11-27T17:45:00+00:00','2026-03-06T20:30:00+00:00','2026-07-02T16:45:00+00:00','2026-11-19T14:30:00+00:00']:
        instant=datetime.fromisoformat(instant);targets.append({'instant':instant.isoformat(),'expected':target_time(instant).isoformat()})
    day=date(2026,11,19);days=list(schedule(day-timedelta(days=60),day));series=[]
    for base in (100,400,200):
        series.append([{'session_date':str(d),'close':float(base+i*.4+rng.uniform(-.1,.1)),'volume':float(10000+i*50)} for i,d in enumerate(days)])
    feature={'session':str(day),'own':series[0],'spy':series[1],'tech':series[2],
             'expected':features(day,*series)}
    data={'allocations':allocations,'quantities':quantities,'targets':targets,'features':feature,'calendar_sessions':len(sessions)}
    immutable(ROOT/'apps/backend-rust/tests/fixtures/policy-parity.json',json.dumps(data,separators=(',',':')).encode())
    print(json.dumps({'allocation_cases':len(allocations),'quantity_cases':len(quantities),'calendar_sessions':len(sessions),'target_cases':len(targets)}))


if __name__=='__main__':main()
