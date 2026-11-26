"""Make routine compose restarts retain the already-verified paper learning mode."""
import json
import shlex
from pathlib import Path

from vps_access import run

PROGRAM=r'''
import hashlib,json,os,re,shutil,subprocess
from pathlib import Path
stage=Path('/opt/keftrade-intelligence/releases/phase4')
compose=stage/'compose.yml';env=stage/'.env'
before=compose.read_text();oldenv=env.read_text()
start=before.index('\n  paper:\n');end=before.index('\n  web:\n',start)
section=before[start:end]
anchor='    environment:\n'
assert section.count(anchor)==1
newsection=section.replace(anchor,anchor+"      INTELLIGENCE_PAPER_EXPERIMENT_ENABLED: 'true'\n      INTELLIGENCE_PAPER_EXPERIMENT_ACCOUNTS_FILE: /run/mainkraft-experiment/account2.json\n",1)
volume='      - /etc/keftrade-intelligence/paper-learning/account2.json:/run/mainkraft-experiment/account2.json:ro\n'
assert newsection.count('    volumes:\n')==1
newsection=newsection.replace('    volumes:\n','    volumes:\n'+volume,1)
updated=before[:start]+newsection+before[end:]
newenv=oldenv
for name in ('PAPER','WEB'):
    pattern=rf"(?m)^INTELLIGENCE_{name}_IMAGE_TAG=.*$"
    assert len(re.findall(pattern,newenv))==1
    newenv=re.sub(pattern,f"INTELLIGENCE_{name}_IMAGE_TAG='paper-learning-v1'",newenv)
nextcompose=stage/'.compose.paper-learning.default.next.yml';nextenv=stage/'.env.paper-learning.default.next'
assert not nextcompose.exists() and not nextenv.exists() and not (stage/'.env.before-paper-learning').exists()
nextcompose.write_text(updated);nextenv.write_text(newenv);nextenv.chmod(0o600)
try:
    cmd=['sudo','docker','compose','--project-directory',str(stage),'--env-file',str(nextenv),'-f',str(nextcompose)]
    subprocess.run(cmd+['config','--quiet'],check=True)
    # All unrelated service blocks remain byte-for-byte identical.
    assert updated[:start]==before[:start] and updated[updated.index('\n  web:\n'):]==before[end:]
    shutil.copy2(env,stage/'.env.before-paper-learning')
    assert (stage/'compose.before-paper-learning.yml').read_text()==before
    os.replace(nextenv,env);os.replace(nextcompose,compose)
    base=['sudo','docker','compose','--env-file',str(env),'-f',str(compose)]
    default=json.loads(subprocess.check_output(base+['config','--format','json']))
    override=json.loads(subprocess.check_output(base+['-f',str(stage/'compose.paper-learning.yml'),'config','--format','json']))
    assert all(default['services'][s]==override['services'][s] for s in ('paper','web'))
    print(json.dumps({'default_compose_retains_experiment':True,'default_and_override_match':True,
        'unrelated_service_blocks_unchanged':True,'original_compose_sha256':hashlib.sha256(before.encode()).hexdigest(),
        'updated_compose_sha256':hashlib.sha256(updated.encode()).hexdigest(),'credential_environment_files_unchanged':True}))
except Exception:
    if (stage/'.env.before-paper-learning').exists():shutil.copy2(stage/'.env.before-paper-learning',env)
    compose.write_text(before)
    raise
finally:
    nextcompose.unlink(missing_ok=True);nextenv.unlink(missing_ok=True)
'''


if __name__=='__main__':
    result=run('python3 -c '+shlex.quote(PROGRAM))
    if result['exit_code']:raise RuntimeError(result['stderr'])
    Path('reports/paper-learning/default-config-verification.json').write_text(result['stdout'])
    print(result['stdout'])
