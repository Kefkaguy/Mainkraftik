"""Immutable GAAP metrics/version-selection oracles from unchanged Python."""
import ast,json,random,sys
from datetime import UTC,date,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'apps/api'),str(ROOT/'apps/api/tests')]
from intelligence.financials import TAGS,VERSION,calculate_metrics,select_versions
from intelligence.util import canonical
from test_financials import fact
def write(path,blob):
    if path.exists():assert path.read_bytes()==blob,'Frozen financial oracle differs'
    else:path.write_bytes(blob)
write(ROOT/'apps/backend-rust/assets/financial-tags.json',canonical({'version':VERSION,'tags':TAGS}).encode())
cases=[]
instant=datetime(2025,6,1,tzinfo=UTC)
base=[fact(metric,100+i*7,tags[0],start=None if metric in ('cash','current_debt','long_term_debt') else date(2025,1,1)) for i,(metric,tags) in enumerate(TAGS.items())]
for row in base:
    row['unit']='USD/shares' if row['metric']=='eps' else 'shares' if row['metric']=='diluted_shares' else 'USD'
old=fact('revenue',80,'Revenues',start=date(2024,1,1),end=date(2024,3,31),available=datetime(2024,5,1,tzinfo=UTC))
future=fact('revenue',999,'Revenues',available=datetime(2026,5,1,tzinfo=UTC))
late=fact('net_income',177,'NetIncomeLoss');late['acquired_at']=datetime(2026,1,1,tzinfo=UTC)
annual=fact('revenue',400,'Revenues',start=date(2024,1,1),end=date(2024,12,31))
sets=[[],base,base+[old,future,late,annual],[fact('long_term_debt',100,'LongTermDebt',start=None)],
      [fact('operating_cash_flow',30,'NetCashProvidedByUsedInOperatingActivities'),fact('capex',10,'PaymentsToAcquirePropertyPlantAndEquipment',start=date(2024,10,1))]]
rng=random.Random(20261119)
for index in range(24):
    rows=[dict(row) for row in base if rng.random()>0.25]+[dict(old),dict(future),dict(late),dict(annual)]
    for row in rows:
        if index%4==0 and row['metric']=='revenue':row['value']=0
        if index%3==0 and row['metric'] in ('capex','operating_cash_flow'):row['period_start']=date(2024,10,1)
        if index%5==0 and row['metric']=='cash':row['available_at']=datetime(2027,1,1,tzinfo=UTC)
    rng.shuffle(rows);sets.append(rows)
for rows in sets:
    for at in (instant,datetime(2026,6,1,tzinfo=UTC)):
        cases.append({'rows':rows,'asof':at,'metrics':calculate_metrics(rows,at),'selected':select_versions(rows,at),'observed':select_versions(rows,at,True)})
write(ROOT/'apps/backend-rust/tests/fixtures/financial-parity.json',canonical({'cases':cases}).encode())
print(f'Saved {len(cases)} original GAAP version/period/revision/metric oracles')
