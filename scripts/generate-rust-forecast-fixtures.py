"""Frozen original baseline statistics and Decimal outcome arithmetic; no evaluation tuning."""
import json,random,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.forecasting import baseline
from intelligence.util import canonical
rng=random.Random(20261120);cases=[]
for n in [0,251,252,253,300,1025,2049]:
    for kind in ['zero','constant','random']:
        samples=[0.0 if kind=='zero' else 0.001 if kind=='constant' else rng.uniform(-0.04,0.03) for _ in range(n)]
        case={'samples':samples}
        try:case['prediction']=baseline(samples)
        except ValueError as e:case['error']=str(e)
        cases.append(case)
pairs=[('1','1'),('2','3'),('100.0000000000000000002','100.0000000000000000001'),('1.0000000000000000000000000001','1'),('10000000000000000000000000001','10000000000000000000000000000'),('1E-30','3E-30'),('123456789.123456789','0.00000123456789')]
for _ in range(256):
    pairs.append((str(Decimal(rng.randrange(1,10**40)).scaleb(-rng.randrange(40))),str(Decimal(rng.randrange(1,10**40)).scaleb(-rng.randrange(40)))))
returns=[{'close':c,'open':o,'return':float(Decimal(c)/Decimal(o)-1)} for c,o in pairs]
path=ROOT/'apps/backend-rust/tests/fixtures/forecast-statistics.json';blob=canonical({'baselines':cases,'returns':returns}).encode()
if path.exists():assert path.read_bytes()==blob,'Frozen forecast arithmetic oracle differs'
else:path.write_bytes(blob)
print(f'Saved {len(cases)} original baseline cases and {len(returns)} exact Decimal outcomes')
