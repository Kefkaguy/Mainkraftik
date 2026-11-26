"""Launch native Rust declaration/market-data acquisition with existing configuration.

Python only passes process environment and records CLI output. Training, inference,
feature calculation and portfolio evaluation remain native Rust. Never logs keys.
"""
import argparse
from datetime import UTC,datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--vps-data-config',action='store_true')
parser.add_argument('--bulk-candles',action='store_true')
parser.add_argument('--feed-probe',action='store_true')
parser.add_argument('--protocol',type=Path)
parser.add_argument('--start',default='2025-03-31T14:30:00Z')
parser.add_argument('--end',default='2025-03-31T15:30:00Z')
parser.add_argument('--feed',choices=['sip','iex'],default='sip')
args=parser.parse_args()
sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.config import settings
key,secret=settings.alpaca_key,settings.alpaca_secret
source='existing_local_configuration'
if args.vps_data_config:
    sys.path.insert(0,str(ROOT/'scripts'))
    from vps_access import run
    program="""import json,subprocess
name='keftrade-intelligence-paper-1'
labels=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Labels}}',name],text=True))
assert labels['com.docker.compose.project']=='keftrade-intelligence' and labels['com.docker.compose.service']=='paper'
code='import json;from paperpilot.config import settings;print(json.dumps(dict(key=settings.alpaca_key,secret=settings.alpaca_secret)))'
result=subprocess.run(['docker','exec',name,'python','-B','-c',code],capture_output=True,text=True)
assert result.returncode==0
print(result.stdout)
"""
    received=run('python3 -c '+shlex.quote(program))
    if received['exit_code']!=0:raise RuntimeError('Existing market-data configuration unavailable; no service changed')
    configured=json.loads(received['stdout']);key,secret=configured['key'],configured['secret']
    del received,configured
    source='existing_vps_market_data_configuration_read_only'
if not key or not secret:raise RuntimeError('Market-data credentials unavailable; no broker account is substituted')
binary=ROOT/'apps/backend-rust/target/debug'/('mainkraft.exe' if os.name=='nt' else 'mainkraft')
assert binary.is_file(),'Build the native binary first'
binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest()
output=ROOT/'.local/intraday-research'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
output.mkdir(parents=True,exist_ok=False)
env=dict(os.environ,INTELLIGENCE_ALPACA_KEY=key,INTELLIGENCE_ALPACA_SECRET=secret)
def call(label,args):
    completed=subprocess.run([str(binary),*args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=1200)
    # Native data errors deliberately contain no request headers or credentials.
    for value in [key,secret]:assert value not in completed.stdout and value not in completed.stderr
    (output/(label+'.txt')).write_text(completed.stdout+'\n'+completed.stderr,encoding='utf-8')
    if completed.returncode:raise RuntimeError('Native '+label+' failed; inspect sanitized local log')
    return json.loads(completed.stdout)
if args.feed_probe:
    print(json.dumps(call('feed-permissions',['hourly-feed-probe','--output',str(output)]),indent=2));sys.exit(0)
declaration={'protocol':str(args.protocol)} if args.protocol else call('declaration',['hourly-declare','--feed',args.feed,'--output',str(output)])
acquired=[]
if args.bulk_candles:
    from datetime import timedelta
    protocol=json.loads(Path(declaration['protocol']).read_text(encoding='utf-8'))
    cursor=datetime.fromisoformat(protocol['development_start']).replace(tzinfo=UTC)
    end=datetime.fromisoformat(protocol['development_end']).replace(tzinfo=UTC)+timedelta(days=1)
    while cursor<end:
        stop=min(cursor+timedelta(days=7),end)
        item=call('acquisition-'+str(len(acquired)),['hourly-collect','--candles-only','--protocol',declaration['protocol'],'--start',cursor.isoformat(),'--end',stop.isoformat(),'--feed',args.feed,'--quote-feed','iex','--output',str(output)])
        acquired.append(item);print(json.dumps({'chunk':len(acquired),'candles':item['counts']['candles'],'quotes':item['counts']['quotes'],'errors':len(item['counts']['failures'])}),flush=True)
        cursor=stop
else:
    acquired.append(call('acquisition',['hourly-collect','--protocol',declaration['protocol'],'--start',args.start,'--end',args.end,'--feed',args.feed,'--quote-feed','iex','--output',str(output)]))
assert hashlib.sha256(binary.read_bytes()).hexdigest()==binary_sha256,'Native binary changed during collection'
report={'at':datetime.now(UTC).isoformat(),'configuration_source':source,'key_material_logged':False,'binary_sha256':binary_sha256,
        'protocol':declaration['protocol'],'datasets':[a['dataset'] for a in acquired],'counts':[a['counts'] for a in acquired],'broker_requests':0,'service_changes':False,'training_language':'Rust','strategy_activated':False}
(output/'probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
