"""User-owned PostgreSQL 18 binaries from signed official PGDG metadata; no install/service."""
import hashlib
import json
import gzip
import os
from pathlib import Path
import subprocess
from datetime import UTC, datetime
import urllib.request

assert 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
assert subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip() == 'amd64'
assert 'VERSION_CODENAME=noble' in Path('/etc/os-release').read_text()
root = Path.home() / '.local/share/mainkraft-rust-validation/postgres18'
destination = root / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True, exist_ok=False)
home = destination / 'gnupg'
home.mkdir(mode=0o700)
base = 'https://apt.postgresql.org/pub/repos/apt/'

def download(url, name, sha=None, size=None):
    with urllib.request.urlopen(url, timeout=40) as response:
        data = response.read(256 * 1024 * 1024 + 1)
    assert len(data) <= 256 * 1024 * 1024
    if sha is not None:
        assert hashlib.sha256(data).hexdigest() == sha
    if size is not None:
        assert len(data) == size
    path = destination / name
    with path.open('xb') as f:
        f.write(data)
    return path

key = download('https://www.postgresql.org/media/keys/ACCC4CF8.asc', 'pgdg.asc')
identity = subprocess.check_output(['gpg', '--homedir', str(home), '--batch', '--with-colons', '--show-keys', str(key)], text=True)
assert 'fpr:::::::::B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8:' in identity
ring = destination / 'pgdg.gpg'
subprocess.run(['gpg', '--homedir', str(home), '--batch', '--dearmor', '--output', str(ring), str(key)], check=True)
release = download(base + 'dists/noble-pgdg/InRelease', 'InRelease')
signature = subprocess.run(['gpgv', '--homedir', str(home), '--keyring', str(ring), str(release)], capture_output=True, text=True)
(destination / 'signature.txt').write_text(signature.stdout + signature.stderr)
assert signature.returncode == 0
lines = release.read_text().splitlines()
start = lines.index('SHA256:') + 1
files = {}
for line in lines[start:]:
    if not line.startswith(' '):
        break
    sha, size, name = line.split()
    files[name] = (sha, int(size))
name = 'main/binary-amd64/Packages.gz'
sha, size = files[name]
index = download(base + 'dists/noble-pgdg/' + name, 'Packages.gz', sha, size)
packages = []
for paragraph in gzip.decompress(index.read_bytes()).decode().split('\n\n'):
    fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
    if fields.get('Package') in ('postgresql-18', 'postgresql-client-18', 'libpq5') and fields.get('Architecture') == 'amd64':
        packages.append(fields)
runtime = destination / 'runtime'
runtime.mkdir()
selected = []
for name in ('postgresql-18', 'postgresql-client-18', 'libpq5'):
    candidates = [p for p in packages if p['Package'] == name]
    assert candidates, name
    best = candidates[0]
    for candidate in candidates[1:]:
        if subprocess.run(['dpkg', '--compare-versions', candidate['Version'], 'gt', best['Version']]).returncode == 0:
            best = candidate
    relative = best['Filename']
    assert relative.startswith('pool/') and '..' not in Path(relative).parts
    deb = download(base + relative, Path(relative).name, best['SHA256'], int(best['Size']))
    subprocess.run(['dpkg-deb', '-x', str(deb), str(runtime)], check=True)
    selected.append({k: best[k] for k in ('Package', 'Version', 'Architecture', 'Filename', 'Size', 'SHA256')})
# Extract the one missing base dependency locally; do not install it system-wide.
raw = subprocess.check_output(['apt-cache','show','liburing2'],text=True).split('\n\n')[0]
extra = dict(line.split(': ',1) for line in raw.splitlines() if ': ' in line and not line.startswith(' '))
assert extra['Package']=='liburing2' and extra['Architecture']=='amd64' and extra['Origin']=='Ubuntu'
deb=download('https://archive.ubuntu.com/ubuntu/'+extra['Filename'],Path(extra['Filename']).name,extra['SHA256'],int(extra['Size']))
subprocess.run(['dpkg-deb','-x',str(deb),str(runtime)],check=True)
selected.append({**{k:extra[k] for k in ('Package','Version','Architecture','Filename','Size','SHA256')},'metadata_source':'existing authenticated Ubuntu system APT cache'})
binary = runtime / 'usr/lib/postgresql/18/bin/postgres'
environment = dict(os.environ, LD_LIBRARY_PATH=str(runtime / 'usr/lib/x86_64-linux-gnu'))
linked = subprocess.run(['ldd', str(binary)], env=environment, capture_output=True, text=True)
(destination / 'postgres-ldd.txt').write_text(linked.stdout + linked.stderr)
assert linked.returncode == 0 and 'not found' not in linked.stdout
version = subprocess.check_output([str(binary), '--version'], env=environment, text=True).strip()
assert 'PostgreSQL) 18.' in version
report = {'at': datetime.now(UTC).isoformat(), 'binary': str(binary), 'runtime': str(runtime), 'version': version, 'packages': selected, 'signature_verified': True, 'official_key_fingerprint': 'B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8', 'service_installed': False, 'cluster_started': False, 'scope': 'User-owned WSL test prerequisite only', 'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}}
(destination / 'result.json').write_text(json.dumps(report, indent=2))
(root / 'latest.json').write_text(json.dumps({'directory': str(destination), 'sha256': hashlib.sha256((destination / 'result.json').read_bytes()).hexdigest()}))
print(json.dumps({'version': version, 'directory': str(destination), 'signature_verified': True, 'cluster_started': False}), flush=True)
