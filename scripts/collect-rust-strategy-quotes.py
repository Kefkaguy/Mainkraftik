"""Run only the native, finite, resumable historical quote collector.

Reads existing data configuration without printing or changing credentials.
No trading modules, broker endpoints, service installation or deployment.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    sys.path.insert(0, str(ROOT / 'scripts'))
    from vps_access import run
    code = """import subprocess,json
name='keftrade-intelligence-paper-1'
labels=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Labels}}',name]))
assert labels['com.docker.compose.project']=='keftrade-intelligence' and labels['com.docker.compose.service']=='paper'
code='import json;from paperpilot.config import settings;print(json.dumps(dict(key=settings.alpaca_key,secret=settings.alpaca_secret)))'
result=subprocess.run(['docker','exec',name,'python','-B','-c',code],capture_output=True,text=True)
assert result.returncode==0
print(result.stdout)
"""
    result = run('python3 -c ' + shlex.quote(code))
    if result['exit_code']:
        raise RuntimeError('Existing market-data configuration unavailable')
    keys = json.loads(result['stdout'])
    if not keys['key'] or not keys['secret']:
        raise RuntimeError('Existing market-data credentials unavailable')
    binary = args.binary.resolve()
    output = args.output.resolve()
    output.relative_to((ROOT / '.local').resolve())
    output.mkdir(parents=True, exist_ok=True)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    binary_sha = digest(binary)
    receipt = {'at': datetime.now(UTC).isoformat(), 'binary_sha256': binary_sha,
               'plan_sha256': digest(args.plan), 'broker_requests': 0, 'service_changes': False}
    run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    (output / (run_id + '-started.json')).write_text(json.dumps(receipt, indent=2))
    environment = dict(os.environ, INTELLIGENCE_ALPACA_KEY=keys['key'], INTELLIGENCE_ALPACA_SECRET=keys['secret'])
    with (output / (run_id + '-stdout.txt')).open('x') as stdout, (output / (run_id + '-progress.txt')).open('x') as stderr:
        result = subprocess.run([str(binary), 'multi-quote-collect', '--plan', str(args.plan.resolve()), '--output', str(output)],
                                cwd=ROOT, env=environment, stdout=stdout, stderr=stderr)
    if digest(binary) != binary_sha:
        raise RuntimeError('Collector binary changed during acquisition')
    receipt.update(exit_code=result.returncode, finished_at=datetime.now(UTC).isoformat())
    (output / (run_id + '-finished.json')).write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
