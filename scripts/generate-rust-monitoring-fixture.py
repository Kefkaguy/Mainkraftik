"""Pure original risk/reliability/statistical/issuance fixtures. No market access."""
import json,sys
from datetime import UTC,datetime,date
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import schedule,issuance_window
from intelligence.forecasting import continuity
from intelligence.monitoring import risk_summary,reliability
from intelligence.research_models import metrics
from intelligence.util import canonical
calendar=list(schedule(date(2026,5,1),date(2026,11,18)))
bars=[{'session_date':day,'feed':'sip','adjustment':'split','adjustment_asof':date(2026,11,19),'open':100+i/5,'close':100+i/5+(-1)**i/10} for i,day in enumerate(calendar)]
sets=[[],bars[:60],bars, bars[:3]+bars[4:],bars+bars[-1:],list(reversed(bars)),[dict(b,feed='iex' if i==1 else 'sip') for i,b in enumerate(bars)]]
risks=[{'bars':rows,'continuity':continuity(rows),'risk':risk_summary(rows)} for rows in sets]
rng=np.random.default_rng(20261119);cases=[]
for size in [1,5,256]:
    y=rng.normal(0,0.02,size);expected=rng.normal(0,0.01,size);p=rng.uniform(0,1,size);p[0]=0.0
    if size>1:p[-1]=1.0
    lower=expected-0.03;upper=expected+0.03
    cases.append({'y':y.tolist(),'expected':expected.tolist(),'p':p.tolist(),'lower':lower.tolist(),'upper':upper.tolist(),'metrics':metrics(y,expected,p,lower,upper)})
history=[{'model_id':'b' if i%2 else 'a','horizon':1 if i%3 else 5,'origin_session':calendar[-1-i],'realized_return':float(i/1000) if i<11 else None,'expected_return':0.001,'probability_positive':i/12,'lower_return':-0.03,'upper_return':0.03} for i in range(12)]
times=['2026-03-06T20:00:00-05:00','2026-03-06T20:29:59-05:00','2026-03-06T20:30:00-05:00','2026-03-09T09:29:59-04:00','2026-03-09T09:30:00-04:00','2026-11-02T09:29:59-05:00','2026-11-19T20:30:00-05:00','2026-11-26T20:30:00-05:00','2026-11-27T20:30:00-05:00']
windows=[{'instant':text,'expected':issuance_window(datetime.fromisoformat(text))} for text in times]
fixture={'risk':risks,'metrics':cases,'history':history,'reliability':reliability(history),'issuance':windows}
path=ROOT/'apps/backend-rust/tests/fixtures/monitoring-parity.json';blob=canonical(fixture).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen monitoring oracle differs'
else:path.write_bytes(blob)
print('Saved risk/continuity, reliability/calibration, and DST/holiday issuance oracles')
