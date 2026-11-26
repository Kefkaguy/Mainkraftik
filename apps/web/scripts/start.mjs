import { cpSync, existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root=resolve(import.meta.dirname,'..');
const output=resolve(root,'.next/standalone');
if(!existsSync(resolve(output,'server.js'))) throw new Error('Run npm run build first');
mkdirSync(resolve(output,'.next'),{recursive:true});
cpSync(resolve(root,'.next/static'),resolve(output,'.next/static'),{recursive:true});
process.env.NODE_ENV='production';
process.env.HOSTNAME='127.0.0.1';
process.env.PORT='3100';
await import(pathToFileURL(resolve(output,'server.js')).href);
