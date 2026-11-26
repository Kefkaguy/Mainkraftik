
'use client';

import { useEffect, useState } from 'react';

type Decision = {
  symbol: string;
  strategy: 'day' | 'swing';
  role: 'paper' | 'challenger';
  action: string;
  reasons: string[];
  execution_rejection?: string;
  expected_net_return: number | null;
  probability_positive: number | null;
  model: string;
  entry_notional?: number | null;
  shadow_selected: boolean;
  simulated_net_return: number | null;
  virtual_net_return: number | null;
};
type Report = {
  session_date: string;
  status: 'provisional' | 'final' | 'closed_pending' | 'no_session';
  bundle_id: string | null;
  selected: string[];
  decisions: Decision[];
  legacy_decisions: { symbol: string; action: string; reason: string }[];
  errors: { kind: string; reason: string }[];
  orders: { symbol: string; strategy: string; side: string; status: string }[];
  fills: { symbol: string; strategy: string; side: string; quantity: number; average_price: number }[];
  positions: { symbol: string; strategy: string }[] | null;
  strategy_pnl: Partial<Record<'day' | 'swing', { broker_paper_pnl: number; estimated_net_pnl: number }>>;
  broker_paper_pnl: number | null;
  estimated_net_pnl: number | null;
  estimated_cost_basis: string;
  shadow_frozen: boolean;
  shadow_pending: number;
  comparison: { status: string; elapsed_dates: number; required_dates: number };
};

const statusLabel = {
  provisional: 'Provisional',
  final: 'Reconciled',
  closed_pending: 'Closed · reconciliation pending',
  no_session: 'No exchange session',
};

function dollars(value: number | null) {
  return value === null ? 'Unavailable' : value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

function pct(value: number | null) {
  return value === null ? '—' : (value * 100).toFixed(2) + '%';
}

export function SessionReport({ selectedSymbol }: { selectedSymbol: string }) {
  const [date, setDate] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setReport(null);
    setError('');
    async function load() {
      try {
        const url = '/intelligence/api/v1/paper/daily-report' + (date ? '?date=' + encodeURIComponent(date) : '');
        const response = await fetch(url, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw Error(response.status === 422 ? 'Choose today or an earlier session.' : 'Report unavailable.');
        const next = await response.json();
        if (!controller.signal.aborted) {
          setReport(next);
          setError('');
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Report unavailable.');
      }
    }
    void load();
    const timer = setInterval(load, 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [date]);

  const decisions = report?.decisions ?? [];
  const errorCounts = new Map<string, number>();
  const tally = (reason: string) => errorCounts.set(reason, (errorCounts.get(reason) ?? 0) + 1);
  report?.errors.forEach(({ reason }) => tally(reason));
  decisions.filter(row => row.role === 'paper').forEach(row => {
    row.reasons.forEach(tally);
    if (row.execution_rejection) tally(row.execution_rejection);
  });

  return <section className="pPanel pSessionReport" aria-label="Daily session report">
    <div className="pPanelTitle">
      <div>
        <h3>Daily session report</h3>
        <p>{report ? report.session_date + ' · ' + statusLabel[report.status] : 'New York exchange session'}</p>
      </div>
      <label>Session <input aria-label="Report session date" type="date" value={date || report?.session_date || ''}
        onChange={event => setDate(event.target.value)} /></label>
    </div>
    {error && <p className="pError" role="status">{error}</p>}
    {!report && !error && <p role="status">Loading session evidence…</p>}
    {report && <>
      <div className="pRiskGrid">
        <span>Research shortlist<strong>{report.selected.length} stocks</strong></span>
        <span>Orders / fill updates<strong>{report.orders.length} / {report.fills.length}</strong></span>
        <span>Broker paper P/L<strong>{dollars(report.broker_paper_pnl)}</strong></span>
        <span title={report.estimated_cost_basis}>Estimated net P/L<strong>{dollars(report.estimated_net_pnl)}</strong></span>
      </div>
      <div className="pSessionStrategies">
        {(['day', 'swing'] as const).map(kind => {
          const rows = decisions.filter(row => row.strategy === kind && row.role === 'paper');
          const candidate = rows.find(row => row.symbol === selectedSymbol);
          const pnl = report.strategy_pnl[kind];
          return <div key={kind}>
            <strong>{kind === 'day' ? 'Day AI' : 'Swing AI'}</strong>
            <small>{rows.length ? ['BUY', 'HOLD', 'SKIP'].map(action =>
              rows.filter(row => row.action === action).length + ' ' + action).join(' · ') : 'Awaiting decisions'}</small>
            {pnl && <small>{dollars(pnl.broker_paper_pnl)} paper · {dollars(pnl.estimated_net_pnl)} estimated net</small>}
            {candidate && <small>{candidate.symbol} · {pct(candidate.probability_positive)} positive · {pct(candidate.expected_net_return)} estimated net</small>}
          </div>;
        })}
        <div>
          <strong>Challenger AI</strong>
          <small>{report.shadow_frozen ? decisions.filter(row => row.role === "challenger" && row.shadow_selected).length + ' virtual entries' : 'Awaiting virtual selection'}</small>
          {report.shadow_pending > 0 && <small>{report.shadow_pending} outcomes pending</small>}
          {report.bundle_id && <small>{report.comparison.elapsed_dates}/{report.comparison.required_dates} comparison sessions · {report.comparison.status}</small>}
        </div>
      </div>
      {errorCounts.size > 0 && <p className="pSessionReasons">{Array.from(errorCounts).sort((a, b) => b[1] - a[1])
        .slice(0, 3).map(([reason, count]) => reason + ' (' + count + ')').join(' · ')}</p>}
      <details>
        <summary>Decisions, errors and executions</summary>
        {report.bundle_id && <p>Model bundle <code>{report.bundle_id.slice(0, 12)}</code></p>}
        {report.selected.length > 0 && <p>Shortlist · {report.selected.join(', ')}</p>}
        {report.errors.map((error, index) => <p key={index}>{error.reason}</p>)}
        {decisions.length > 0 && <div className="pTableScroll"><table>
          <thead><tr><th>Stock</th><th>Strategy</th><th>Decision</th><th>Reason</th><th>Expected net</th>
            <th>Simulated outcome</th><th>Virtual entry outcome</th></tr></thead>
          <tbody>{decisions.map(row => <tr key={row.symbol + row.strategy + row.role}>
            <td>{row.symbol}</td><td>{row.strategy} · {row.role} <small>{row.model}{row.entry_notional ? " · " + dollars(row.entry_notional) + " entry" : ""}</small></td>
            <td>{row.action}{row.shadow_selected && ' · virtual entry'}</td>
            <td>{[...row.reasons, row.execution_rejection].filter(Boolean).join(' · ') || 'Model gates passed'}</td>
            <td>{pct(row.expected_net_return)}</td>
            <td>{row.simulated_net_return == null ? (row.expected_net_return == null ? 'Unavailable' : 'Pending') : pct(row.simulated_net_return)}</td>
            <td>{row.shadow_selected ? row.virtual_net_return == null ? 'Pending' : pct(row.virtual_net_return) : '—'}</td>
          </tr>)}</tbody>
        </table></div>}
        {report.legacy_decisions.map(row => <p key={row.symbol}>{row.symbol} · {row.action} · {row.reason}</p>)}
        {report.orders.map((order, index) => <p key={index}>{order.symbol} · {order.strategy ?? 'legacy'} · {order.side} · {order.status}</p>)}
        {report.fills.map((fill, index) => <p key={index}>{fill.symbol} · {fill.strategy ?? 'legacy'} · {fill.side} · {fill.quantity} at {dollars(fill.average_price)}</p>)}
        <p>Open positions · {report.positions?.map(p => p.symbol + ' (' + p.strategy + ')').join(', ') || (report.positions ? 'None' : 'Unavailable')}</p>
        <p>{report.estimated_cost_basis}</p>
        <p>Swing positions can remain open overnight. Gaps and outages can exceed software stop levels.</p>
      </details>
    </>}
  </section>;
}
