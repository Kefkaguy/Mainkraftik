'use client';
import { useEffect, useState } from 'react';
type Data=Record<string,any>;
const pct=(v:any)=>v==null?'Unavailable':`${(v*100).toFixed(3)}%`;
const num=(v:any)=>v==null?'Unavailable':Number(v).toFixed(4);

export function Phase3Lab({symbol,busy,action}:{symbol:string;busy:boolean;action:(kind:string)=>void}) {
  const [data,setData]=useState<Data|null>(null);
  const [error,setError]=useState('');
  const [horizon,setHorizon]=useState(1);
  useEffect(()=>{let active=true;const load=()=>fetch(`/intelligence/api/v1/phase3?symbol=${symbol}`,{cache:'no-store'}).then(async r=>{if(!r.ok)throw new Error('Phase 3 report unavailable');const d=await r.json();if(active){setData(d);setError('')}}).catch(e=>{if(active)setError(e.message)});setData(null);load();const timer=setInterval(load,15000);return()=>{active=false;clearInterval(timer)}},[symbol]);
  return <section className="panel"><div className="sectionTitle"><div><h3>Information features and controlled tuning</h3><p>Nested chronological development · four Optuna trials per study · original holdout sealed.</p></div><div className="buttonGroup"><button className="secondary" disabled={busy} onClick={()=>action('information')}>Refresh information</button><button className="secondary" disabled={busy} onClick={()=>action('tune')}>Read / run frozen tuning</button></div></div>
    {error&&<p role="alert">{error}</p>}{!data?<p>Loading source and research coverage…</p>:<>
      <div className="tableScroll"><table><thead><tr><th>Source</th><th>Stock</th><th>Unique records</th><th>First source date</th><th>Last source date</th></tr></thead><tbody>{data.sources.map((s:Data)=><tr key={s.provider+s.symbol}><td>{s.provider}</td><td>{s.symbol}</td><td>{s.articles}<small>{s.versions} versions</small></td><td>{s.first_created?.slice(0,10)??'Unavailable'}</td><td>{s.last_created?.slice(0,10)??'Unavailable'}</td></tr>)}</tbody></table></div>
      <p className="researchNotice">News features end at 19:00 New York with a 20:00 acquisition cutoff. Later revisions cannot enter earlier inputs. Current-vintage historical news is reconstructed; official IR content with unknown edit times is available only from acquisition. Sector ETFs are named context proxies, not historical constituent membership.</p>
      <div className="reportMetrics"><span>Completed tuning trials<strong>{data.trials.length}</strong><small>Maximum 96; unavailable IR groups consume no searches</small></span><span>Frozen shadow<strong>{data.protocol?`${data.protocol.specification.family} / ${data.protocol.specification.feature_group}`:'Not enrolled'}</strong><small>Experimental · no validated status</small></span><span>Prospective paired dates<strong>{data.progress.paired_dates??data.progress.final_verdict?.dates??0} / 126</strong><small>Both stocks, both horizons, matured outcomes required</small></span><span>Shadow begins<strong>{data.protocol?.start_session??'Not enrolled'}</strong><small>One final predeclared comparison</small></span></div>
      {!data.protocol&&<button className="secondary" disabled={busy||data.candidates.length===0} onClick={()=>action('nominate')}>Freeze the inner-selected shadow candidate</button>}
      <div className="buttonGroup" role="group" aria-label="Tuned forecast horizon">{[1,5].map(h=><button className={horizon===h?'primary':'secondary'} key={h} onClick={()=>setHorizon(h)}>{h}-session development</button>)}</div>
      <div className="tableScroll"><table><thead><tr><th>Family / features</th><th>Status</th><th>MAE / reference</th><th>Brier / reference</th><th>Log loss</th><th>Interval coverage</th><th>Details</th></tr></thead><tbody>{data.candidates.filter((r:Data)=>r.horizon===horizon).map((r:Data)=><tr key={r.id}><td>{r.family}<small>{r.feature_group}</small></td><td>{r.status}</td><td>{pct(r.report.candidate?.mae)}<small>{pct(r.report.baseline?.mae)}</small></td><td>{num(r.report.candidate?.brier)}<small>{num(r.report.baseline?.brier)}</small></td><td>{num(r.report.candidate?.log_loss)}</td><td>{pct(r.report.candidate?.interval_coverage)}</td><td><details><summary>Metrics and uncertainty</summary><pre>{JSON.stringify(r.report,null,2)}</pre></details></td></tr>)}</tbody></table></div>
      <details><summary>Sources, limitations and trial configurations</summary>{data.limitations.map((l:string)=><p key={l}>{l}</p>)}<pre>{JSON.stringify({filings:data.filings,markets:data.markets,trials:data.trials,protocol:data.protocol,progress:data.progress},null,2)}</pre></details>
    </>}
  </section>;
}
