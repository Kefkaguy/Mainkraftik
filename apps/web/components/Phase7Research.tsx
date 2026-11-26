'use client';
import {useEffect,useState} from 'react';
type Result={status:string;winner?:string;audit_classification?:string;
  audit:Record<string,{prediction:{mae:number;brier:number;direction_accuracy:number};base_scenario:{net_pnl:number;trade_count:number}}>};
export function Phase7Research(){
  const [data,setData]=useState<Result|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{let active=true;
    fetch('/intelligence/api/v1/chart-intelligence/phase7',{cache:'no-store',credentials:'same-origin'})
      .then(async r=>{if(!r.ok)throw Error('Phase 7 evidence unavailable');return r.json()})
      .then(r=>{if(active)setData(r)}).catch(e=>{if(active)setError(e.message)});
    return()=>{active=false};
  },[]);
  return <section className="pPanel" aria-label="Phase 7 strategy validation">
    <div className="pPanelTitle"><h3>Phase 7 · strategy validation</h3><span>LOCAL RESEARCH</span></div>
    {error&&<p role="status">{error}</p>}
    {data&&<><p><strong>{data.status.replaceAll('_',' ')}</strong></p><p>{data.audit_classification}</p>
      <p>Frozen forecast reference: {data.winner??'None'}. No candidate is authorized for execution or promotion.</p>
      <div className="pTableScroll"><table><thead><tr><th>Candidate</th><th>Return MAE</th><th>Brier ↓</th><th>Direction accuracy</th><th>Hypothetical trades</th><th>Assumed-cost net P/L</th></tr></thead><tbody>
        {Object.entries(data.audit).map(([name,r])=><tr key={name}><td>{name.replaceAll('_',' ')}</td><td>{(r.prediction.mae*100).toFixed(3)}%</td><td>{r.prediction.brier.toFixed(4)}</td><td>{(r.prediction.direction_accuracy*100).toFixed(1)}%</td><td>{r.base_scenario.trade_count}</td><td>${r.base_scenario.net_pnl.toFixed(2)}</td></tr>)}
      </tbody></table></div>
      <p><small>These are synthetic execution scenarios. Missing executable quote history prevents independent cost validation. A zero-trade result is not proof of profitability. Broker paper results remain in the separate session report.</small></p></>}
  </section>;
}
