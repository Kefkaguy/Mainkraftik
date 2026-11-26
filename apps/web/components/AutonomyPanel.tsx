'use client';

import {useEffect, useState} from 'react';

type Gate={status:'PASSED'|'FAILED'|'NOT_EVALUATED';reason:string};
type Trace={version:string;context:Record<string,string>;gates:Record<string,Gate>};
type TraceData={signals:{id:string;symbol:string;strategy:string;role:string;action:string;trace:Trace|null}[];
  events:{created_at:string;kind:string;payload:{decision_trace?:Trace;context?:Record<string,string>;gates?:Record<string,Gate>;version?:string}}[]};
type Prediction={symbol:string;decision:string;evidence_type:string;forecast:{expected_return:number;probability_up:number;probability_target_before_stop:number;expected_downside:number;lower_return:number;upper_return:number}};
type Research={research:{status:string;models?:Record<string,{mae:number;brier:number;coverage:number;dates:number}>;optimization?:{regressor_converged:boolean;classifier_converged:boolean};limitations?:string[]};
  monitoring:{status:string;prediction?:Prediction[];controller_run?:{observed_at:string;monitored:string[];monitoring:{status:string};errors:unknown[]}[]};
  deployment:{status:string}};
const pct=(n:number)=>`${(n*100).toFixed(2)}%`;

async function read(path:string){
  const r=await fetch(`/intelligence/api/v1/${path}`,{cache:'no-store',credentials:'same-origin'});
  if(!r.ok)throw Error(`Evidence unavailable (${r.status})`);
  return r.json();
}

function Gates({trace}:{trace:Trace}){
  return <details><summary>{trace.version} · inspect gates</summary><table><thead><tr><th>Gate</th><th>Status</th><th>Evidence</th></tr></thead>
    <tbody>{Object.entries(trace.gates).map(([key,gate])=><tr key={key}><td>{key.replaceAll('_',' ')}</td><td>{gate.status}</td><td>{gate.reason}</td></tr>)}</tbody></table></details>;
}

export function AutonomyPanel(){
  const [research,setResearch]=useState<Research|null>(null);
  const [traces,setTraces]=useState<TraceData|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    const load=async()=>{
      const results=await Promise.allSettled([read('chart-intelligence/status'),read('paper/decision-traces')]);
      if(!active)return;
      if(results[0].status==='fulfilled')setResearch(results[0].value); else setResearch(null);
      if(results[1].status==='fulfilled')setTraces(results[1].value); else setTraces(null);
      setError(results.some(r=>r.status==='rejected')?'Some evidence is unavailable. Missing records are not evidence that a gate passed.':'');
    };
    load();const timer=setInterval(load,30000);return()=>{active=false;clearInterval(timer)};
  },[]);
  const last=research?.monitoring.controller_run?.[0];
  return <section className="pPanel" aria-label="Autonomous research and decision evidence">
    <div className="pPanelTitle"><h3>Research and decision evidence</h3><span>{research?.deployment.status??'Status unavailable'}</span></div>
    <p>Actual Alpaca Paper orders, positions and realized / unrealized P/L appear in the session report above. Research observations and simulations below never submit orders.</p>
    {error&&<p role="status">{error}</p>}
    <h4>Prospective research · monitoring</h4>
    <p>Monitored stocks: {last?.monitored.join(', ')??'No controller run recorded'} · reliability: {last?.monitoring.status??'NOT_EVALUATED'} · last run: {last?.observed_at??'Unavailable'}</p>
    {!!last?.errors.length&&<p role="status">{last.errors.length} data or model failures recorded in the last run.</p>}
    {research?.monitoring.prediction?.length?<div className="pTableWrap"><table><thead><tr><th>Stock / decision</th><th>Expected return</th><th>Up probability</th><th>Target before stop</th><th>Downside</th><th>Return range</th></tr></thead><tbody>
      {research.monitoring.prediction.slice(0,12).map(p=><tr key={p.symbol+p.decision}><td>{p.symbol}<small>{p.decision} · {p.evidence_type}</small></td><td>{pct(p.forecast.expected_return)}</td><td>{pct(p.forecast.probability_up)}</td><td>{pct(p.forecast.probability_target_before_stop)}</td><td>{pct(p.forecast.expected_downside)}</td><td>{pct(p.forecast.lower_return)} to {pct(p.forecast.upper_return)}</td></tr>)}
    </tbody></table></div>:<p>No prospective Chart Intelligence predictions recorded.</p>}
    <h4>Historical simulation · Chart Intelligence</h4>
    <p>{research?.research.status??'NOT_RUN'} · model promotion requires separate review.</p>
    {research?.research.optimization&&(!research.research.optimization.classifier_converged||!research.research.optimization.regressor_converged)&&<p>Neural training reached its fixed iteration budget. Convergence is not established.</p>}
    {research?.research.models&&<div className="pTableWrap"><table><thead><tr><th>Model</th><th>Return error</th><th>Brier error ↓</th><th>Interval coverage</th><th>Evaluation dates</th></tr></thead><tbody>
      {Object.entries(research.research.models).map(([name,m])=><tr key={name}><td>{name.replaceAll('_',' ')}</td><td>{pct(m.mae)}</td><td>{m.brier.toFixed(4)}</td><td>{pct(m.coverage)}</td><td>{m.dates}</td></tr>)}
    </tbody></table></div>}
    {research?.research.limitations?.map(l=><p key={l}><small>{l}</small></p>)}
    <h4>Candidate acceptance and rejection</h4>
    {traces?.signals.length?traces.signals.slice(0,20).map(s=><article key={s.id}><p><strong>{s.symbol} · {s.strategy} · {s.action}</strong> · {s.role==='challenger'?'SIMULATION':'ALPACA_PAPER signal (not an order)'}</p>{s.trace?<Gates trace={s.trace}/>:<p>NOT_RECORDED · original historical record retained.</p>}</article>):<p>No strategy signal traces recorded.</p>}
    {traces?.events.slice(0,12).map((e,i)=>{
      const t=e.payload.decision_trace??(e.payload.gates?e.payload as Trace:null);
      return t?<article key={e.created_at+i}><p>{t.context.evidence_type??'Execution evidence'} · {t.context.symbol??''} · {e.created_at}</p><Gates trace={t}/></article>:null;
    })}
    <p><small>DRY_RUN means a fixture exchange. SIMULATION means historical or virtual results. ALPACA_PAPER means broker paper evidence; a passed signal alone is not a fill.</small></p>
  </section>;
}
