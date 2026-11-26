'use client';
import { useEffect, useState } from 'react';
type Data = Record<string, any>;
const pct=(v:any)=>v==null?'Unavailable':`${(v*100).toFixed(2)}%`;
const timestamp=(v:any)=>v?new Date(v).toLocaleString('en-US',{timeZone:'America/New_York'})+' New York':'Unavailable';

export function ForecastContext({company}:{company:Data}) {
  const history=company.forecasts??[];
  return <section className="panel"><div className="sectionTitle"><div><h3>Forecast context and uncertainty</h3><p>Latest completed observation: {company.coverage.last??'unavailable'}. Acquired {timestamp(company.coverage.last_acquired_at)}.</p></div><span className="tag neutral">Reference model · prospective ledger</span></div><div className="reportMetrics">{[1,5].map(h=>{const f=history.find((r:Data)=>r.horizon===h&&r.model_id==='historical-baseline-v1');return <span key={h}>{h}-session historical distribution<strong>{f?`${pct(f.lower_return)} to ${pct(f.upper_return)}`:'Awaiting eligible issuance'}</strong><small>{f?`Cutoff ${timestamp(f.data_cutoff)} · target ${f.target_session}`:'No retrospective insertion'}</small></span>})}<span>20-session realized volatility, annualized<strong>{pct(company.risk?.realized_volatility_20_annualized)}</strong><small>Descriptive past returns</small></span><span>60-session drawdown<strong>{pct(company.risk?.drawdown_60)}</strong><small>From the highest closing price in the window</small></span></div><p className="muted">Baseline positive-return frequency is an empirical historical rate. Its 10th–90th return percentiles are not a calibrated prediction-coverage guarantee. These past-price summaries do not explain future returns.</p></section>;
}

export function OperationalStatus() {
  const [health,setHealth]=useState<Data|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{let live=true;const load=()=>fetch('/intelligence/api/v1/monitor',{cache:'no-store'}).then(async r=>{if(!r.ok)throw new Error('Health report unavailable');const d=await r.json();if(live){setHealth(d);setError('')}}).catch(e=>{if(live)setError(e.message)});load();const timer=setInterval(load,30000);return()=>{live=false;clearInterval(timer)}},[]);
  return <section className="panel"><div className="sectionTitle"><div><h3>Forecast workflow health</h3><p>Local scheduling requires this computer, PostgreSQL, and the worker to remain running.</p></div><span className="tag neutral">{health?(health.worker_fresh?'Worker responding':'Worker stale or stopped'):'Checking worker'}</span></div>{error&&<p role="alert">{error}</p>}{health&&<div className="tableScroll"><table><thead><tr><th>Security</th><th>Latest completed data</th><th>Freshness</th><th>Continuity</th><th>Overdue outcomes</th></tr></thead><tbody>{health.symbols.map((s:Data)=><tr key={s.symbol}><td>{s.symbol}</td><td>{s.last_session??'Unavailable'}</td><td>{s.fresh?'Current':'Stale / unavailable'}</td><td>{s.contiguous?'Verified':s.reason}</td><td>{s.overdue_outcomes}</td></tr>)}</tbody></table></div>}<p className="muted">Refresh attempts at 19:00, 19:30 and 19:50; cutoff 20:00; issuance 20:30; monitoring after 20:35 America/New_York. Failed work and missed cutoffs remain visible.</p></section>;
}
