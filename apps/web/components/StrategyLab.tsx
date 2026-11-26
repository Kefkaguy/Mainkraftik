
'use client';

import {useEffect, useState} from 'react';

type Trial = {
  configuration: {family: string; variant: number; horizon: number; volatility_exit: boolean};
  overlay: string;
  size: number;
  net_pnl: number;
  average_daily_pnl: number;
  max_drawdown: number;
  entries: number;
  profit_factor: number | null;
  dates: number;
  paused_at_drawdown: boolean;
  costs: number;
  effective_20_session_blocks: number;
  days_at_20: number;
  stress_and_delay?: {net_pnl: number; average_daily_pnl: number};
};
type Lab = {
  status: string;
  report_id?: string;
  results?: Trial[];
  experiment_count?: number;
  prospective: {status: string; reason?: string};
  simulator_audit?: {status: string};
  feasibility?: {supported: boolean; reason: string};
  limitations?: string[];
  monthly_walk_forward?: {summary?: Trial};
};
const money = (value: number) => value.toLocaleString('en-US', {style: 'currency', currency: 'USD'});

export function StrategyLab() {
  const [data, setData] = useState<Lab | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetch('/intelligence/api/v1/research-lab/results', {signal: controller.signal, cache: 'no-store'})
      .then(async response => {
        if (!response.ok) throw Error('Research results unavailable');
        setData(await response.json());
      })
      .catch(err => {if (!controller.signal.aborted) setError(err.message);});
    return () => controller.abort();
  }, []);
  return <section className="pPanel">
    <div className="pPanelTitle"><h3>Strategy laboratory</h3><span>$20 average daily objective</span></div>
    {error && <p role="status">{error}</p>}
    {!data && !error && <p>Loading research results…</p>}
    {data && <>
      <p>{data.status} · {data.experiment_count ?? 0} recorded experiments · simulator audit {data.simulator_audit?.status ?? 'pending'}</p>
      {data.feasibility && <p>{data.feasibility.reason}</p>}
      {data.monthly_walk_forward?.summary && <p>Monthly walk-forward: {money(data.monthly_walk_forward.summary.net_pnl)} net,
        {' '}{money(data.monthly_walk_forward.summary.average_daily_pnl)} per session.
        {' '}{data.monthly_walk_forward.summary.paused_at_drawdown ? 'Drawdown review triggered.' : 'No drawdown review triggered.'}</p>}
      <details><summary>Historical comparisons and prospective testing</summary>
        <p>Paper-forward status · {data.prospective.status}. {data.prospective.reason}</p>
        <div className="pTableScroll"><table>
          <thead><tr><th>Rules</th><th>News</th><th>Entry</th><th>25bp net / day</th><th>50bp delayed net / day</th><th>Drawdown</th><th>Costs</th><th>Trades / dates / blocks</th><th>$20 days</th></tr></thead>
          <tbody>{data.results?.map((row, i) => <tr key={i}>
            <td>{row.configuration.family} {row.configuration.variant + 1} · {row.configuration.horizon} sessions
              <small>{row.configuration.volatility_exit ? 'Volatility exits' : 'Fixed exits'}</small></td>
            <td>{row.overlay}</td><td>{money(row.size)}</td>
            <td>{money(row.net_pnl)} / {money(row.average_daily_pnl)}</td>
            <td>{row.stress_and_delay ? `${money(row.stress_and_delay.net_pnl)} / ${money(row.stress_and_delay.average_daily_pnl)}` : 'Unavailable'}</td>
            <td>{money(row.max_drawdown)}{row.paused_at_drawdown && ' · paused'}</td>
            <td>{money(row.costs)}</td>
            <td>{row.entries} / {row.dates} / {row.effective_20_session_blocks.toFixed(2)}</td>
            <td>{row.days_at_20} / {row.dates}</td>
          </tr>)}</tbody>
        </table></div>
        {data.limitations?.map(item => <p key={item}>{item}</p>)}
      </details>
    </>}
  </section>;
}
