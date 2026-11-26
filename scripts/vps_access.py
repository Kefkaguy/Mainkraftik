"""Explicit-command SSH helper. Secrets stay in ignored local configuration."""

import json
import getpass
import os
import sys
from pathlib import Path

import paramiko

ROOT=Path(__file__).resolve().parents[1]


def connect():
    path=ROOT/'.local/vps-access.json'
    config=json.loads(path.read_text()) if path.exists() else {'host':'15.204.114.198','username':'ubuntu'}
    password=config.get('password') or os.environ.get('INTELLIGENCE_VPS_PASSWORD') or getpass.getpass('VPS SSH password (not stored): ')
    client=paramiko.SSHClient()
    client.load_system_host_keys()
    client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(config['host'],username=config['username'],password=password,
                   look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=20)
    return client


def run(command):
    if '/home/ubuntu/minecraft' in command.lower() or any(x in command for x in ['docker system prune','docker volume prune','docker image prune']):
        raise ValueError('Protected resource/global cleanup command rejected')
    with connect() as client:
        _,stdout,stderr=client.exec_command(command,timeout=180)
        out,err=stdout.read().decode(),stderr.read().decode()
        return {'exit_code':stdout.channel.recv_exit_status(),'stdout':out,'stderr':err}


if __name__=='__main__':
    print(json.dumps(run(sys.stdin.read()),indent=2))
