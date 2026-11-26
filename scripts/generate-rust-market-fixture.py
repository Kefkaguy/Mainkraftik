"""Capture the unmodified Python market pipeline with fake, causal provider data."""
import json
import sys
from datetime import datetime,time,UTC
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.calendar import EASTERN
from intelligence.providers import normalize_bars
from intelligence.util import canonical,digest
from paperpilot import experiment

fixture=json.loads((ROOT/'apps/backend-rust/tests/fixtures/policy-parity.json').read_text())
bundle=json.loads(next((ROOT/'.local/rust-migration').glob('bundle-*.json')).read_text())
instant=datetime(2026,11,19,14,30,5,tzinfo=UTC)
raw={};quotes={}
for symbol,key in [('AMD','own'),('SPY','spy'),('XLK','tech')]:
    raw[symbol]=[]
    for row in fixture['features'][key]:
        close=row['close'];date=datetime.fromisoformat(row['session_date']).date()
        stamp=datetime.combine(date,time(),EASTERN).astimezone(UTC)
        raw[symbol].append({'t':stamp.isoformat(),'o':close-.1,'h':close+.3,'l':close-.3,'c':close,'v':row['volume']})
    price=raw[symbol][-2]['c']+.2
    quotes[symbol]={'t':instant.isoformat(),'bp':price-.01,'ap':price+.01}
normalized={s:normalize_bars(s,rows,'sip',instant.date(),instant) for s,rows in raw.items()}
asset={'tradable':True,'status':'active','class':'us_equity','fractionable':True,'shortable':True,'easy_to_borrow':True}
market=experiment.Market();market.models=bundle
market.daily_inputs=lambda symbol,unused:normalized[symbol]
market.quote=lambda symbol:{'bid':quotes[symbol]['bp'],'ask':quotes[symbol]['ap'],'source_time':instant,'acquired_at':instant,'feed':'iex'}
sources=[]
def record(account,kind,payload,c=None):
    sources.append({'account':account,'kind':kind,'payload':payload})
    return digest([account,kind,payload])
experiment.record=record
observations=market.observations(['AMD'],SimpleNamespace(request=lambda *args:asset),instant)
assert len(observations)==1
result={'instant':instant,'raw_bars':raw,'quotes':quotes,'asset':asset,'expected':observations,'sources':sources,'normalized':normalized}
path=ROOT/'apps/backend-rust/tests/fixtures/market-parity.json';blob=canonical(result).encode()
if path.exists():assert path.read_bytes()==blob
else:path.write_bytes(blob)
print('Captured native market-pipeline oracle without network, brokerage, or DB access')
