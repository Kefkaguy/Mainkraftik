'use client';
import { useState } from 'react';

export type Bar = {session_date:string; open:number|string; high:number|string; low:number|string; close:number|string; volume:number|string};
export function PriceChart({bars,feed}:{bars:Bar[];feed:string}) {
  const [range,setRange]=useState(90);
  const visible=bars.slice(-range);
  if(visible.length<2) return <div className="chartEmpty"><strong>Price history is not available yet</strong><span>Refresh data to load verified daily bars from the configured feed.</span></div>;
  const min=Math.min(...visible.map(b=>Number(b.low))), max=Math.max(...visible.map(b=>Number(b.high)));
  const pad=(max-min)*.12||1, bottom=min-pad, top=max+pad;
  const x=(i:number)=>18+i*(820/visible.length), y=(value:number)=>22+(top-value)/(top-bottom)*236;
  const step=820/visible.length;
  return <>
    <div className="chartToolbar"><div className="legend"><i />Daily prices <span>{feed.toUpperCase()} · split adjusted</span></div><div className="range" aria-label="Chart range">{[45,90,252].map(n=><button key={n} aria-pressed={range===n} className={range===n?'selected':''} onClick={()=>setRange(n)}>{n===252?'1Y':`${n}D`}</button>)}</div></div>
    <svg className="priceChart" viewBox="0 0 920 310" role="img" aria-label={`Daily candlestick prices from ${visible[0].session_date} to ${visible.at(-1)?.session_date}`}>
      {[0,1,2,3,4].map(i=>{const value=bottom+(top-bottom)*i/4;return <g key={i}><line x1="10" x2="848" y1={y(value)} y2={y(value)} stroke="#e6edf2" strokeDasharray="3 5"/><text x="860" y={y(value)+4} fill="#788a98" fontSize="11">{value.toFixed(2)}</text></g>})}
      {visible.map((bar,i)=>{const up=Number(bar.close)>=Number(bar.open);const a=y(Number(bar.open)),b=y(Number(bar.close));return <g key={bar.session_date}><title>{`${bar.session_date} · Open ${Number(bar.open).toFixed(2)} · Close ${Number(bar.close).toFixed(2)}`}</title><line x1={x(i)} x2={x(i)} y1={y(Number(bar.high))} y2={y(Number(bar.low))} stroke={up?'#188575':'#bd6770'}/><rect x={x(i)-Math.max(1,step*.28)} y={Math.min(a,b)} width={Math.max(1,step*.56)} height={Math.max(1.5,Math.abs(a-b))} fill={up?'#188575':'#bd6770'} rx=".5"/></g>})}
      {[0,.25,.5,.75,1].map((f,i)=>{const index=Math.min(visible.length-1,Math.round(f*(visible.length-1)));return <text key={i} x={x(index)} y="294" fill="#788a98" fontSize="11" textAnchor={i===0?'start':i===4?'end':'middle'}>{new Date(visible[index].session_date+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'})}</text>})}
    </svg><div className="chartFoot">{visible.length} completed sessions<span>Dividends excluded · No price interpolation</span></div>
  </>;
}
