import EmbeddedPostgres from 'embedded-postgres';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// A separate local development cluster. Never starts Docker or existing services.
const root = resolve(import.meta.dirname, '..');
const env = Object.fromEntries(readFileSync(resolve(root, '.env'), 'utf8').split(/\r?\n/)
  .filter(l => l && !l.startsWith('#') && l.includes('=')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const uri = new URL(env.INTELLIGENCE_DATABASE_URL);
if (!['127.0.0.1', 'localhost'].includes(uri.hostname) || uri.pathname !== '/intelligence') throw new Error('Local database only');
const databaseDir = resolve(root, '.local/postgres');
mkdirSync(resolve(root, '.local'), { recursive: true });
const pg = new EmbeddedPostgres({databaseDir, user: decodeURIComponent(uri.username),
  password: decodeURIComponent(uri.password), port: Number(uri.port), persistent: true,
  initdbFlags: ['--encoding=UTF8', '--locale=C'], postgresFlags: ['-h', '127.0.0.1'],
  onLog: message => { if (/ready to accept/.test(message)) console.log('Independent PostgreSQL ready on 127.0.0.1:' + uri.port); },
  onError: message => console.error(message)});
if (!existsSync(resolve(databaseDir, 'PG_VERSION'))) await pg.initialise();
await pg.start();
try { await pg.createDatabase('intelligence'); } catch (e) { if (!String(e).includes('already exists')) throw e; }
console.log('KefTrade Intelligence local database running. Ctrl+C stops only this cluster.');
let stopping = false;
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => { if (!stopping) { stopping = true; await pg.stop(); process.exit(0); } });
setInterval(() => {}, 60_000);
