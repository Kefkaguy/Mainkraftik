"""Isolated WSL PostgreSQL/backup parity; no installer, app service or broker access."""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = Path.home() / '.local/share/mainkraft-rust-validation'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def native_sources():
    result = {}
    for directory, children, files in os.walk(ROOT / 'apps/backend-rust'):
        children[:] = [c for c in children if c != 'target']
        for name in files:
            path = Path(directory) / name
            result[path.relative_to(ROOT).as_posix()] = sha(path)
    for name in ['test-rust-linux-postgres.py', 'test-rust-postgres.py', 'test-rust-backup.py', 'prepare-rust-research-api.py', 'prepare-rust-evaluation.py', 'prepare-rust-audit.py', 'generate-rust-evaluation-fixtures.py', 'generate-rust-evaluation-identity.py', 'generate-rust-training-fixtures.py', 'generate-rust-python-set-fixtures.py', 'generate-rust-numpy-exp-fixtures.py', 'generate-rust-research-sentiment-fixture.py', 'generate-rust-active-engine-fixture.py']:
        result['scripts/' + name] = sha(ROOT / 'scripts' / name)
    return result

def inside(destination):
    assert 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    destination.resolve().relative_to((ROOT / '.local/rust-migration/linux-postgres').resolve())
    import fcntl
    import psycopg
    lock = (VALIDATION / 'postgres-validation.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    pointer = json.loads((VALIDATION / 'postgres18/active-cluster.json').read_text())
    source = Path(pointer['file'])
    assert sha(source) == pointer['sha256']
    cluster = json.loads(source.read_text())
    data = Path(cluster['data']).resolve()
    data.relative_to((VALIDATION / 'postgres18').resolve())
    assert data.name == 'data' and (data / 'PG_VERSION').read_text().strip() == '18'
    assert cluster['port'] == 5543 and cluster['listen_addresses'] == ['127.0.0.1']
    binary = Path(cluster['bin']) / 'pg_ctl'
    assert binary.resolve() == data.parent / 'runtime/usr/lib/postgresql/18/bin/pg_ctl'
    environment = dict(os.environ, LD_LIBRARY_PATH=str(Path(cluster['runtime']) / 'usr/lib/x86_64-linux-gnu'))

    def control(action):
        command = [str(binary), '-D', str(data), '-t', '30']
        if action == 'start':
            command += ['-l', str(data.parent / 'server.log'), '-w', 'start']
        else:
            command += ['-m', 'fast', '-w', 'stop']
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        (destination / ('cluster-' + action + '.txt')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Owned Linux validation cluster ' + action + ' failed')

    started = False
    if (data / 'postmaster.pid').exists():
        pid = int((data / 'postmaster.pid').read_text().splitlines()[0])
        command = (Path('/proc') / str(pid) / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
        assert str(data) in command and str(Path(cluster['bin']) / 'postgres') in command
    else:
        import socket
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(('127.0.0.1', 5543)) != 0, 'Refusing a foreign listener'
        control('start')
        started = True
    uri = 'postgresql://rust_fixture@127.0.0.1:5543/postgres'

    def databases():
        with psycopg.connect(uri, connect_timeout=5) as connection:
            return [r[0] for r in connection.execute('SELECT datname FROM pg_database ORDER BY datname')]

    source_hashes = native_sources()
    checks = []
    complete = False
    empty_names = ['postgres', 'template0', 'template1']
    try:
        assert databases() == empty_names, 'Validation requires its own empty test cluster'
        environment.update(
            CARGO_HOME=str(VALIDATION / 'cargo'), RUSTUP_HOME=str(VALIDATION / 'rustup'),
            CARGO_TARGET_DIR=str(VALIDATION / 'target'), CARGO_BUILD_JOBS='2', RUSTUP_TOOLCHAIN='1.93.0',
            PATH=str(VALIDATION / 'cargo/bin') + ':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            INTELLIGENCE_DATABASE_URL='postgresql://rust_fixture@127.0.0.1:5543/intelligence',
            INTELLIGENCE_PAPER_DATABASE_URL='postgresql://rust_fixture@127.0.0.1:5543/intelligence_paper',
            INTELLIGENCE_ALPACA_KEY='fixture', INTELLIGENCE_ALPACA_SECRET='fixture', PYTHONUNBUFFERED='1',
        )
        for name, script, count in [('postgres', 'test-rust-postgres.py', 24), ('backup', 'test-rust-backup.py', 1)]:
            with (destination / (name + '.txt')).open('w') as log:
                result = subprocess.run([sys.executable, 'scripts/' + script], cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, text=True)
            text = (destination / (name + '.txt')).read_text()
            passed_tests = sum(map(int, re.findall(r'test result: ok\. (\d+) passed;', text)))
            checks.append({'name': name, 'exit_code': result.returncode, 'passed_tests': passed_tests, 'required_tests': count})
            if result.returncode or passed_tests != count:
                raise RuntimeError('Linux ' + name + ' parity gate failed')
            if name == 'postgres':
                for path in sorted((ROOT / '.local/rust-migration').glob('latest-*-oracle.json')):
                    ref = json.loads(path.read_text())
                    blob = ROOT / ref['file']
                    assert sha(blob) == ref['sha256']
                    (destination / path.name.replace('latest-', 'original-')).write_bytes(blob.read_bytes())
                    for label,extra in ref.get('auxiliary',{}).items():
                        extra_file=ROOT/extra['file']
                        assert sha(extra_file)==extra['sha256']
                        (destination/path.name.replace('latest-','original-').replace('.json','-'+label+'.json')).write_bytes(extra_file.read_bytes())
                ref=json.loads((ROOT/'.local/rust-migration/latest-sentiment-assembly.json').read_text())
                blob=ROOT/ref['file'];assert sha(blob)==ref['sha256']
                (destination/'native-sentiment-assembly.json').write_bytes(blob.read_bytes())
            else:
                ref = json.loads((ROOT / '.local/rust-migration/latest-backup-report.json').read_text())
                report = ROOT / ref['file']
                assert sha(report) == ref['sha256']
                assert json.loads(report.read_text())['passed']
                shutil.copytree(report.parent, destination / 'backup-crossread')
        assert databases() == empty_names, 'Disposable test databases remain'
        complete = True
    finally:
        # An already-running owned cluster belongs to the caller; leave it running.
        if started:
            control('stop')
        (destination / 'python-packages.txt').write_text(subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True))
        unchanged = source_hashes == native_sources()
        report = {'at': datetime.now(UTC).isoformat(), 'passed': complete and len(checks) == 2 and all(c['exit_code'] == 0 and c['passed_tests'] == c['required_tests'] for c in checks) and unchanged,
                  'checks': checks, 'source_unchanged': unchanged, 'source_files': source_hashes,
                  'python': sys.version, 'postgres': cluster['version'], 'started_owned_cluster': started,
                  'full_linux_validation_complete': False, 'deployment_allowed': False,
                  'outputs': {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob('*') if p.is_file()}}
        (destination / 'result.json').write_text(json.dumps(report, indent=2))
    return report['passed']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside-wsl', action='store_true')
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.inside_wsl:
        assert args.destination and args.destination.is_dir()
        return 0 if inside(args.destination) else 1
    assert os.name == 'nt', 'Use the explicit local WSL entrypoint'
    destination = ROOT / '.local/rust-migration/linux-postgres' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    destination.mkdir(parents=True, exist_ok=False)
    project = '/mnt/' + ROOT.drive[0].lower() + '/' + ROOT.relative_to(ROOT.anchor).as_posix()
    relative = destination.relative_to(ROOT).as_posix()
    reference = '/home/erosimcity/.local/share/mainkraft-rust-validation/python-reference/bin/python'
    result = subprocess.run(['wsl', '-d', 'Ubuntu', '--', reference, project + '/scripts/test-rust-linux-postgres.py', '--inside-wsl', '--destination', project + '/' + relative], cwd=ROOT, capture_output=True, text=True)
    (destination / 'launcher.txt').write_text(result.stdout + result.stderr)
    if result.returncode:
        print(result.stdout + result.stderr)
        return 1
    report = json.loads((destination / 'result.json').read_text())
    assert report['passed']
    (ROOT / '.local/rust-migration/latest-linux-postgres.json').write_text(json.dumps({'directory': relative, 'sha256': sha(destination / 'result.json')}))
    print(json.dumps({'passed': True, 'postgres_suites': 24, 'backup_suites': 1, 'directory': relative}), flush=True)
    return 0

if __name__ == '__main__':
    sys.exit(main())
