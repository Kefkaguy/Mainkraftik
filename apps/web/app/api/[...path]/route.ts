import { NextRequest } from 'next/server';
export const dynamic = 'force-dynamic';

async function proxy(request: NextRequest, context: {params:Promise<{path:string[]}>}) {
  const {path} = await context.params;
  if (path.some(p => !/^[a-zA-Z0-9-]+$/.test(p)) || path[0] !== 'v1')
    return Response.json({detail:'Invalid API resource'}, {status:400});
  const headers = new Headers();
  for (const name of ['cookie','origin','content-type','x-csrf-token']) {
    const value=request.headers.get(name); if(value) headers.set(name,value);
  }
  const internal=process.env.INTELLIGENCE_INTERNAL_API_URL ?? 'http://127.0.0.1:8100';
  const body = ['GET','HEAD'].includes(request.method) ? undefined : await request.text();
  if(body && Buffer.byteLength(body)>16384) return Response.json({detail:'Request is too large'}, {status:413});
  try {
    const upstream=await fetch(`${internal}/${path.join('/')}${request.nextUrl.search}`, {
      method:request.method, headers,body,cache:'no-store',redirect:'error',signal:path.at(-1)==='events'?request.signal:AbortSignal.timeout(50000),
    });
    const outgoing = new Headers({'Content-Type':upstream.headers.get('content-type')??'application/json','Cache-Control':'no-store'});
    const cookie=upstream.headers.get('set-cookie'); if(cookie)outgoing.set('set-cookie',cookie);
    if(upstream.headers.get('content-type')?.startsWith('text/event-stream')) {
      outgoing.set('X-Accel-Buffering','no');
      return new Response(upstream.body,{status:upstream.status,headers:outgoing});
    }
    return new Response(await upstream.arrayBuffer(), {status:upstream.status,headers:outgoing});
  } catch {
    return Response.json({detail:'The research service is unavailable. Start the independent API and database.'},{status:503});
  }
}
export {proxy as GET, proxy as POST, proxy as PUT};
