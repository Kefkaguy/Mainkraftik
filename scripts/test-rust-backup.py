"""Original Python/native archive cross-reading on owned, disposable local databases."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import struct
import sys
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
import zipfile

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
import opsbackup
from intelligence.backup import make_backup
from intelligence.config import settings

base = urlsplit(settings.database_url)
assert base.hostname in ('localhost', '127.0.0.1') and base.port == 5543
admin_uri = urlunsplit(base._replace(path='/postgres', query=''))
run = secrets.token_hex(6)
output = ROOT / '.local/rust-migration/backup-runs' / run
output.mkdir(parents=True, exist_ok=False)
owned = []

def uri(name):
    return urlunsplit(base._replace(path='/' + name, query=''))

def create(kind):
    prefix = 'intelligence_restore_' if kind == 'research' else 'intelligence_paper_test_'
    name = prefix + run + '_' + str(len(owned))
    with psycopg.connect(admin_uri, autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    owned.append(name)
    return name

def rows(name):
    with psycopg.connect(uri(name)) as c:
        tables = sorted(r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        return {t: sorted(r[0] for r in c.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier(t)))) for t in tables}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def rewrite(source, name, change):
    path = output / (name + '.zip')
    with zipfile.ZipFile(source) as z:
        members = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(members['manifest.json'])
    change(manifest, members)
    members['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for n, b in members.items():
            z.writestr(n, b)
    return path.name

report = {'local_only': True, 'broker_requests': 0, 'run': run, 'created_at': datetime.now(UTC).isoformat()}
try:
    sources = {}
    before = {}
    for kind in ('research', 'paper'):
        name = create(kind)
        sources[kind] = name
        with opsbackup.database(kind, uri(name)) as (connect, migrate, _):
            migrate()
            if kind == 'paper':
                from paperpilot.experiment import migrate as learning_migrate
                learning_migrate()
            with connect() as c:
                if kind == 'research':
                    c.execute("INSERT INTO source_documents VALUES('00000000-0000-0000-0000-000000000001','fixture','https://fixture.invalid',%s,'2026-11-20T01:00Z',%s)", ('a' * 64, psycopg.types.json.Jsonb({'text': '雪 café', 'nested': [None, True, 1.25]})))
                    c.execute("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES('MSI','2026-11-19','sip','split','2026-11-20',100.000000000000000001,102,99,101.000000000000000002,99999999,'2026-11-20T00:00Z','2026-11-20T01:00Z','00000000-0000-0000-0000-000000000001',%s)", ('b' * 64,))
                    c.execute("SELECT setval('watchlist_versions_id_seq',9876,true)")
                    c.execute("SELECT setval('corporate_actions_id_seq',7654,false)")
                else:
                    for account in ('fixture-fixed', 'fixture-online'):
                        c.execute("INSERT INTO learning_accounts(account_id,label,account_number,baseline_equity) VALUES(%s,%s,%s,15000.000000000000000001)", (account, account, account))
                        c.execute("INSERT INTO learning_records(id,account_id,kind,payload) VALUES(%s,%s,'outcome',%s)", (account, account, psycopg.types.json.Jsonb({'return': -0.0123, 'text': '雪', 'paper': True})))
                    for i, state in enumerate(('unknown', 'partially_filled', 'filled')):
                        c.execute("INSERT INTO learning_intents(client_id,account_id,prediction_id,symbol,phase,state,body,order_payload) VALUES(%s,'fixture-online',%s,'MSI','open',%s,%s,%s)", ('fixture-' + str(i), str(i), state, psycopg.types.json.Jsonb({'qty': '0.250000001', 'side': 'buy'}), psycopg.types.json.Jsonb({'status': state, 'filled_qty': '0.125000001' if state == 'partially_filled' else '0'})))
                    c.execute("INSERT INTO pilot_events(kind,payload) VALUES('restore-fixture','{}')")
                    c.execute("SELECT setval('pilot_events_id_seq',4321,true)")
            opsbackup.export(output / (kind + '-python-v2.zip'), kind)
            if kind == 'research':
                make_backup(output / 'research-python-v1.zip')
        before[kind] = rows(name)
    cases = []
    original = output / 'research-python-v2.zip'
    mutations = {
        'wrong-kind': lambda m, b: m.update(kind='paper'),
        'wrong-major': lambda m, b: m.update(postgres_major=1),
        'wrong-count': lambda m, b: m['tables'][0].update(rows=m['tables'][0]['rows'] + 1),
        'bad-copy': lambda m, b: b.update({'tables/daily_bars.bin': b'invalid binary copy'}),
        'missing-member': lambda m, b: b.pop('tables/daily_bars.bin'),
        'extra-member': lambda m, b: b.update({'../escape': b'never extract'}),
        'wrong-hash': lambda m, b: m['tables'][0].update(sha256='0' * 64),
        'schema-mismatch': lambda m, b: (m['tables'].append({'name': 'unknown_table', 'rows': 0}), b.update({'tables/unknown_table.bin': b''})),
    }
    for label, change in mutations.items():
        cases.append({'name': label, 'archive': rewrite(original, label, change), 'target': create('research')})
    # Preserve the valid central CRC but corrupt a stored COPY body on disk.
    crc_path = output / 'wrong-crc.zip'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(crc_path, 'x', compression=zipfile.ZIP_STORED) as target:
        for member in source.namelist():
            target.writestr(member, source.read(member))
    with zipfile.ZipFile(crc_path) as z:
        info = z.getinfo('tables/daily_bars.bin')
    blob = bytearray(crc_path.read_bytes())
    name_size, extra_size = struct.unpack_from('<HH', blob, info.header_offset + 26)
    blob[info.header_offset + 30 + name_size + extra_size + 25] ^= 1
    crc_path.write_bytes(blob)
    cases.append({'name': 'wrong-crc', 'archive': crc_path.name, 'target': create('research')})
    duplicate_path = output / 'duplicate-member.zip'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(duplicate_path, 'x') as target:
        for member in source.namelist():
            target.writestr(member, source.read(member))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            target.writestr('tables/daily_bars.bin', source.read('tables/daily_bars.bin'))
    cases.append({'name': 'duplicate-member', 'archive': duplicate_path.name, 'target': create('research')})
    bad_migrations = rewrite(output / 'research-python-v1.zip', 'wrong-migrations', lambda m, b: m.update(migrations=['fake.sql']))
    cases.append({'name': 'wrong-migrations', 'archive': bad_migrations, 'target': create('research')})
    targets = {k: create(k) for k in ('research', 'paper')}
    config = {'directory': str(output), 'sources': sources, 'targets': targets, 'legacy_target': create('research'), 'failures': cases}
    (output / 'fixture.json').write_text(json.dumps(config, indent=2))
    env = dict(os.environ, RUST_BACKUP_FIXTURE=str(output / 'fixture.json'), RUST_BACKUP_BASE_URI=uri(sources['research']))
    result = subprocess.run(['cargo', 'test', '--manifest-path', 'apps/backend-rust/Cargo.toml', '--test', 'backup_lifecycle', '--', '--ignored', '--nocapture'], cwd=ROOT, env=env, capture_output=True, text=True)
    (output / 'native-test.txt').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    print(result.stdout, result.stderr)
    native_path = output / 'native-results.json'
    if native_path.exists():
        native = json.loads(native_path.read_text())
        # These names come from the native verifier's owned fresh databases only.
        for item in native.get('verified', []):
            name = item['restored_database']
            assert name.startswith(('intelligence_restore_', 'intelligence_paper_test_bak_')) and len(name) <= 63
            owned.append(name)
    assert result.returncode == 0, 'Native backup lifecycle failed; retained original log'
    with zipfile.ZipFile(output / 'research-python-v2.zip') as original, zipfile.ZipFile(output / 'cancelled-request.zip') as cancelled:
        assert original.read('tables/securities.bin') == cancelled.read('tables/securities.bin'), 'Export lost its original snapshot across a concurrent committed update'
    cross = []
    for kind in ('research', 'paper'):
        assert rows(targets[kind]) == before[kind]
        for restored in native['verified']:
            if restored['kind'] == kind:
                assert rows(restored['restored_database']) == before[kind]
        target = create(kind)
        receipt = opsbackup.restore_empty(output / (kind + '-native.zip'), kind, target, uri(target))
        assert rows(target) == before[kind], 'Original Python reader differs from original source'
        cross.append({'kind': kind, 'python_reads_native': True, 'native_reads_python': True, 'tables': len(before[kind]), 'source_sha256': digest(before[kind]), 'python_receipt': receipt})
        assert rows(sources[kind]) == before[kind], 'Source changed'
    assert rows(config['legacy_target']) == before['research']
    for case in cases:
        assert rows(case['target']) == {}, 'Failed restore must roll back new schema too'
    cli_results = []
    cli_target = create('research')
    for action in ('inspect', 'export', 'restore-new', 'verify-local'):
        env = dict(os.environ, INTELLIGENCE_DATABASE_URL=uri(cli_target if action == 'restore-new' else sources['research']))
        archive = output / ('research-cli.zip' if action == 'export' else 'research-native.zip')
        command = ['cargo', 'run', '--quiet', '--manifest-path', 'apps/backend-rust/Cargo.toml', '--', 'backup', action, '--kind', 'research', '--archive', str(archive)]
        if action == 'restore-new':
            command.extend(['--ack-new-database', cli_target])
        cli = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        (output / ('cli-' + action + '.txt')).write_text(cli.stdout + '\n' + cli.stderr, encoding='utf-8')
        assert cli.returncode == 0, 'Native backup CLI failed: ' + action
        value = json.loads(cli.stdout)
        if action == 'verify-local':
            name = value['restored_database']
            assert name.startswith('intelligence_restore_') and len(name) <= 63
            owned.append(name)
            assert rows(name) == before['research']
        cli_results.append({'action': action, 'result': value})
    assert rows(cli_target) == before['research']
    report.update(passed=True, cross_readers=cross, native=native, cli=cli_results, negative_cases=cases, legacy_v1=True, failed_restore_targets_empty=True, source_rows_unchanged=True, consistent_snapshot_across_concurrent_commit=True)
finally:
    for name in reversed(owned):
        assert name.startswith(('intelligence_restore_', 'intelligence_paper_test_'))
        with psycopg.connect(admin_uri, autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(name)))
    report['owned_databases_removed'] = len(owned)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Backup evidence:', output.relative_to(ROOT).as_posix(), flush=True)
if report.get('passed'):
    reference = {'file': (output / 'report.json').relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256((output / 'report.json').read_bytes()).hexdigest()}
    (ROOT / '.local/rust-migration/latest-backup-report.json').write_text(json.dumps(reference))
