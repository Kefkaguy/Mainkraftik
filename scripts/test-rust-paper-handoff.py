"""Original Python -> Rust -> Python recovery on one disposable local database.

Only two in-memory broker books exist. Both implementations use the same owned
loopback fixture. No production endpoint, account, credentials or service is used.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
from paperpilot import experiment
from paperpilot.config import settings
from paperpilot.db import connect, migrate
from intelligence.util import digest

INSTANT = datetime(2026, 11, 19, 14, 30, tzinfo=UTC)
base = urlsplit(settings.paper_database_url)
assert base.hostname in ('127.0.0.1', 'localhost') and base.port == 5543
run = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
output = ROOT / '.local/rust-migration/paper-handoff' / run
output.mkdir(parents=True, exist_ok=False)
database = 'intelligence_paper_test_rust_' + secrets.token_hex(6)
uri = urlunsplit(base._replace(path='/' + database, query=''))
admin = urlunsplit(base._replace(path='/postgres', query=''))
token = secrets.token_hex(24)
guard = threading.RLock()
books = {a: {'cash': 1000., 'positions': {}, 'orders': {}, 'posts': 0} for a in ('control', 'adaptive')}
requests = []
hold = threading.Event()
entered = threading.Event()
release = threading.Event()


def account(a):
    book = books[a]
    equity = book['cash'] + sum(book['positions'].values()) * 100
    gross = sum(abs(q) for q in book['positions'].values()) * 100
    power = max(0., 2 * equity - gross)
    return {'id': a, 'account_number': 'TEST-' + a, 'status': 'ACTIVE',
            'cash': str(book['cash']), 'equity': str(equity), 'buying_power': str(power),
            'regt_buying_power': str(power), 'account_blocked': False, 'trading_blocked': False}


def fill(a, order, cumulative):
    book = books[a]
    sign = 1 if order['side'] == 'buy' else -1
    delta = cumulative - float(order['filled_qty'])
    book['cash'] -= sign * delta * float(order['filled_avg_price'])
    book['positions'][order['symbol']] = book['positions'].get(order['symbol'], 0) + sign * delta
    order['filled_qty'] = str(cumulative)
    order['status'] = 'filled' if cumulative == float(order['qty']) else 'partially_filled'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        if self.headers.get('x-fixture-token') != token:
            self.send_error(403)
            return
        parsed = urlsplit(self.path)
        parts = parsed.path.strip('/').split('/', 1)
        if len(parts) != 2 or parts[0] not in books:
            self.send_error(404)
            return
        a, path = parts
        if hold.is_set() and a == 'control' and path == 'v2/account':
            entered.set()
            assert release.wait(10), 'Owned fixture acknowledgement barrier timed out'
        with guard:
            requests.append([self.command, a, path])
            book = books[a]
            if self.command == 'POST' and path == 'v2/orders':
                n = int(self.headers.get('Content-Length', '0'))
                assert 0 < n <= 65536
                body = json.loads(self.rfile.read(n))
                key = body['client_order_id']
                assert key.startswith('mkx-') and key not in book['orders']
                assert body['type'] == 'market' and body['time_in_force'] == 'day'
                assert body['symbol'] == 'AMD' and body['side'] in ('buy', 'sell')
                assert float(body['qty']) > 0
                book['posts'] += 1
                value = {**body, 'id': str(uuid5(NAMESPACE_URL, key)), 'status': 'new',
                         'filled_qty': '0', 'filled_avg_price': '100.01' if body['side'] == 'buy' else '99.99'}
                book['orders'][key] = value
                fill(a, value, .25 if a == 'control' else float(body['qty']))
            elif self.command != 'GET':
                self.send_error(405)
                return
            elif path == 'v2/account':
                value = account(a)
            elif path == 'v2/clock':
                value = {'timestamp': INSTANT.isoformat(), 'is_open': True, 'next_open': '2026-11-20T14:30:00+00:00'}
            elif path == 'v2/positions':
                value = [{'symbol': s, 'qty': str(q), 'market_value': str(q * 100)} for s, q in book['positions'].items() if q]
            elif path == 'v2/orders':
                value = [o for o in book['orders'].values() if o['status'] != 'filled']
            elif path == 'v2/orders:by_client_order_id':
                value = book['orders'].get(parse_qs(parsed.query).get('client_order_id', [''])[0])
                if value is None:
                    self.send_error(404)
                    return
            elif path == 'v2/assets/AMD':
                value = {'tradable': True, 'status': 'active', 'class': 'us_equity', 'fractionable': True, 'shortable': True, 'easy_to_borrow': True}
            else:
                self.send_error(404)
                return
            blob = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server.daemon_threads = True
origin = f'http://127.0.0.1:{server.server_port}'
assert urlsplit(origin).hostname == '127.0.0.1'
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()


class Broker:
    """Test-only adapter; never constructs experiment.Broker or changes PAPER_BASE."""
    def __init__(self, a, lose_ack=False):
        self.a, self.lose_ack = a, lose_ack
        self.client = httpx.Client(timeout=5, follow_redirects=False, trust_env=False)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, origin + '/' + self.a + path, headers={'x-fixture-token': token}, **kwargs)
        if response.status_code == 404 and path == '/v2/orders:by_client_order_id':
            return None
        response.raise_for_status()
        return response.json()

    def account(self): return self.request('GET', '/v2/account')
    def positions(self): return self.request('GET', '/v2/positions')
    def orders(self): return self.request('GET', '/v2/orders')
    def by_client(self, key): return self.request('GET', '/v2/orders:by_client_order_id', params={'client_order_id': key})
    def submit(self, body):
        order = self.request('POST', '/v2/orders', json=body)
        if self.lose_ack:
            self.lose_ack = False
            raise ValueError('Synthetic acknowledgement lost after broker accepted 0.25 shares')
        return order


class Market:
    def universe(self): return ['AMD']
    def quote(self, _): return {'bid': 99.99, 'ask': 100.01, 'source_time': INSTANT, 'acquired_at': INSTANT, 'feed': 'iex'}
    def observations(self, _, broker, instant):
        assert instant == INSTANT
        return [{'symbol': 'AMD', 'x': [1., *([.01] * 14)], 'prior_return': .005, 'uncertainty': .01,
                 'price': 100., 'midpoint': 100., 'shortable': True, 'asset': broker.request('GET', '/v2/assets/AMD'),
                 'source_id': 'fixture-source', 'cost_allowance': .0012, 'quote_time': INSTANT}]


def evidence():
    with connect() as c:
        return {r['id']: digest(r['payload']) for r in c.execute('SELECT id,payload FROM learning_records')}


def state():
    with connect() as c:
        return {r['account_id']: (r['state'], r['order_payload']) for r in c.execute('SELECT * FROM learning_intents')}


def child(binary):
    env = dict(os.environ, RUST_ENGINE_FIXTURE_CHILD='1', RUST_TEST_DATABASE_URL=uri,
               RUST_ENGINE_FIXTURE_ORIGIN=origin, RUST_ENGINE_FIXTURE_TOKEN=token)
    return subprocess.Popen([str(binary), '--exact', 'engine_fixture_child', '--ignored', '--nocapture'], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def finish(process, label):
    try:
        text, _ = process.communicate(timeout=15)
    except BaseException:
        process.kill(); process.wait()
        raise
    (output / (label + '.txt')).write_text(text, encoding='utf-8')
    assert process.returncode == 0, 'Owned fixture engine failed: ' + label


report = {'at': datetime.now(UTC).isoformat(), 'synthetic_only': True, 'production_changes': False,
          'alpaca_requests': 0, 'new_accounts': 0, 'deployment_allowed': False}
created = False
original_uri = settings.paper_database_url
brokers = {}
try:
    built = subprocess.run(['cargo', 'test', '--locked', '--manifest-path', 'apps/backend-rust/Cargo.toml',
                            '--test', 'process_recovery_lifecycle', '--no-run', '--message-format=json'],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
    (output / 'build.txt').write_text(built.stdout + '\n' + built.stderr, encoding='utf-8')
    assert built.returncode == 0
    binaries = [Path(v['executable']) for line in built.stdout.splitlines() if line.startswith('{')
                and (v := json.loads(line)).get('reason') == 'compiler-artifact'
                and v.get('target', {}).get('name') == 'process_recovery_lifecycle' and v.get('executable')]
    assert len(binaries) == 1
    binary = binaries[0]
    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    created = True
    settings.paper_database_url = uri
    migrate(); experiment.migrate()
    with connect() as c: c.execute('UPDATE learning_controls SET enabled=true')
    brokers = {a: Broker(a, lose_ack=a == 'control') for a in books}
    configs = [{'id': a, 'label': a, 'policy': a} for a in books]
    def python_engine():
        return experiment.Engine(configs=configs, brokers=brokers, market=Market(), clock=lambda: INSTANT)
    python_engine().tick()
    assert state()['control'][0] == 'partially_filled'
    assert books['control']['positions']['AMD'] == .25
    assert [b['posts'] for b in books.values()] == [1, 1]
    before = evidence()
    finish(child(binary), 'rust-partial-reconcile')
    assert state()['control'][0] == 'partially_filled'
    assert state()['control'][1]['filled_qty'] == '0.25'
    assert all(evidence()[key] == value for key, value in before.items())
    with guard:
        order = next(iter(books['control']['orders'].values()))
        fill('control', order, float(order['qty']))
    finish(child(binary), 'rust-full-reconcile')
    assert all(s[0] == 'filled' for s in state().values())
    before_rollback = evidence()
    python_engine().tick()
    assert all(evidence()[key] == value for key, value in before_rollback.items())
    assert [b['posts'] for b in books.values()] == [1, 1]
    with connect() as c:
        assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='cycle'").fetchone()['n'] == 2
        assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='prediction'").fetchone()['n'] == 2
        assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='runtime_failure'").fetchone()['n'] == 0
    # Source Python holds the exact shared session lease; Rust must do no work.
    with connect() as lease:
        assert lease.execute('SELECT pg_try_advisory_lock(753194199) acquired').fetchone()['acquired']
        lease.commit()
        previous_requests = len(requests)
        finish(child(binary), 'rust-excluded-by-python-lease')
        assert len(requests) == previous_requests
    # Reverse direction: suspend the Rust process inside its owned account GET.
    hold.set()
    active = child(binary)
    try:
        assert entered.wait(10)
        previous_requests = len(requests)
        python_engine().tick()
        assert len(requests) == previous_requests
    finally:
        release.set()
        finish(active, 'python-excluded-by-rust-lease')
    assert [b['posts'] for b in books.values()] == [1, 1]
    report.update(passed=True, python_to_rust_to_python=True, partial_shares=.25,
                  physical_submissions_per_fixture_account=[1, 1], duplicate_submissions=0,
                  immutable_records_preserved=True, python_excludes_rust=True, rust_excludes_python=True,
                  shared_durable_cycles=2, forecasts=2, native_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  source_files={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                [Path(__file__), ROOT/'apps/api/paperpilot/experiment.py', ROOT/'apps/backend-rust/tests/process_recovery_lifecycle.rs', ROOT/'apps/backend-rust/src/engine.rs', ROOT/'apps/backend-rust/src/db.rs']},
                  limitations=['Synthetic broker books and market observations', 'No container/image/configuration rollback or production cutover tested'])
finally:
    hold.clear(); release.set()
    for broker in brokers.values(): broker.client.close()
    settings.paper_database_url = original_uri
    server.shutdown(); server.server_close(); thread.join(timeout=5)
    if created:
        assert database.startswith('intelligence_paper_test_rust_')
        with psycopg.connect(admin, autocommit=True, connect_timeout=5) as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(database)))
    report['owned_database_removed'] = created
    report['outputs'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    (output/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
if report.get('passed'):
    pointer={'file':(output/'result.json').relative_to(ROOT).as_posix(),'sha256':hashlib.sha256((output/'result.json').read_bytes()).hexdigest()}
    (ROOT/'.local/rust-migration/latest-paper-handoff.json').write_text(json.dumps(pointer),encoding='utf-8')
    print(json.dumps(report, indent=2))
