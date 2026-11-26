'use client';

import { useCallback, useEffect, useState } from 'react';

import { Activity, ArrowDownRight, ArrowUpRight, ChevronRight, Clock3, Database, FlaskConical, Layers3, LockKeyhole, LogOut, Radio, RefreshCw, ShieldCheck, Square, TrendingUp } from 'lucide-react';

import {SessionReport} from './SessionReport';
import {StrategyLab} from './StrategyLab';
import {AutonomyPanel} from './AutonomyPanel';
import {Phase7Research} from './Phase7Research';
import {PaperLearning} from './PaperLearning';
import './paper.css';
import './paper-learning.css';

type Data = Record<string, any>;

const base='/intelligence';



const knownNames:Record<string,string>={AMD:'Advanced Micro Devices',MSFT:'Microsoft',GOOG:'Alphabet',AMZN:'Amazon',TSM:'Taiwan Semiconductor',META:'Meta Platforms'};

const money=(v:unknown)=>v==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(v));

const pct=(v:unknown)=>v==null?'—':`${Number(v)>=0?'+':''}${(Number(v)*100).toFixed(2)}%`;

const stamp=(v:unknown)=>v?new Date(String(v)).toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'} )+' ET':'Unavailable';

const number=(v:unknown)=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:5});

async function request(path:string,options:RequestInit={}) {

  const r=await fetch(`${base}/api/v1/${path}`,{...options,credentials:'same-origin',cache:'no-store'});

  const d=await r.json();if(!r.ok)throw Object.assign(new Error(d.detail??'Service unavailable'),{status:r.status});return d;

}

function Mark(){return <span className="pMark" aria-hidden="true"><i/><i/><i/></span>}

function Empty({title,children}:{title:string;children:React.ReactNode}) {return <div className="pEmpty"><Activity size={24}/><strong>{title}</strong><p>{children}</p></div>}

function Line({values,labels,kind='price'}:{values:number[];labels?:string[];kind?:string}) {

  const [hover,setHover]=useState<number|null>(null);

  if(values.length<2)return <Empty title="The record starts with real observations">{kind==='price'?'Daily history is unavailable.':'Account observations will appear after dedicated account verification. No simulated performance is shown.'}</Empty>;

  const min=Math.min(...values),max=Math.max(...values),pad=(max-min)*.12||1,low=min-pad,high=max+pad;

  const y=(v:number)=>222-(v-low)/(high-low)*190;

  const points=values.map((v,i)=>`${54+i/(values.length-1)*800},${y(v)}`).join(' ');

  const ticks=[low+(high-low)*.85,low+(high-low)*.5,low+(high-low)*.15];

  return <svg className="pChart" viewBox="0 0 900 270" role="img" aria-label={`${kind} chart, ${values.length} actual observations`} onPointerMove={e=>{const r=e.currentTarget.getBoundingClientRect();setHover(Math.max(0,Math.min(values.length-1,Math.round(((e.clientX-r.left)/r.width*900-54)/800*(values.length-1)))) )}} onPointerLeave={()=>setHover(null)}>

    <defs><linearGradient id={`wash-${kind}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#87d5b0" stopOpacity=".14"/><stop offset="100%" stopColor="#87d5b0" stopOpacity="0"/></linearGradient></defs>

    {ticks.map((v,i)=><g key={i}><line x1="54" x2="854" y1={y(v)} y2={y(v)} stroke="#283342" strokeDasharray="3 5"/><text x="890" y={y(v)+4} textAnchor="end" fill="#7e8b9d" fontSize="10">{kind==='drawdown'?pct(v):money(v)}</text></g>)}

    <polygon points={`54,235 ${points} 854,235`} fill={`url(#wash-${kind})`}/><polyline points={points} fill="none" stroke="#87d5b0" strokeWidth="2" strokeLinejoin="round"/><circle cx="854" cy={y(values.at(-1)!)} r="3" fill="#a6ebc7"/>

    {hover!=null&&hover<values.length&&<g><line x1={54+hover/(values.length-1)*800} x2={54+hover/(values.length-1)*800} y1="28" y2="235" stroke="#8bada0" strokeDasharray="3 3"/><circle cx={54+hover/(values.length-1)*800} cy={y(values[hover])} r="4" fill="#b5efcf"/><rect x="55" y="4" width="360" height="22" rx="4" fill="#243c34"/><text x="65" y="19" fill="#c6efd9" fontSize="11">{labels?.[hover]?.slice(0,19)} · {kind==='drawdown'?pct(values[hover]):money(values[hover])}</text></g>}

    <text x="54" y="260" fill="#7e8b9d" fontSize="10">{labels?.[0]?.slice(0,10)}</text><text x="854" y="260" textAnchor="end" fill="#7e8b9d" fontSize="10">{labels?.at(-1)?.slice(0,10)}</text>

  </svg>;

}

function Performance({data,fresh}:{data:Data|null;fresh:boolean}){
  const symbols:string[]=(data?.stocks??[]).map((s:Data)=>s.symbol);

  const [view,setView]=useState('realized');

  const s=data?.snapshot?.payload,valid=fresh&&s?.cash_consistent,curve=data?.curve??[];

  const deployed=s?.positions?.reduce((sum:number,p:Data)=>sum+Number(p.market_value),0);

  const trades=s?.trades?.filter((t:Data)=>t.side==='sell')??[];

  const closed=(s?.wins??0)+(s?.losses??0);

  const benchmarks=data?.benchmarks??[];

  const values=view==='spy'?benchmarks.map((r:Data)=>r.payload.spy_equity):view==='passive'?benchmarks.map((r:Data)=>r.payload.passive_six_equity):curve.map((r:Data)=>r[view]);

  const labels=(view==='spy'||view==='passive')?benchmarks.map((r:Data)=>r.observed_at):curve.map((r:Data)=>r.time);

  return <><section className="pPanel"><div className="pPanelTitle"><div><h3>Experiment account & allocation</h3><p>The $600 reference allocation is used for comparisons. P/L requires reconciled cash and fills.</p></div></div><div className="pRiskGrid pPerformanceStats"><span>Starting paper account equity<strong>{money(fresh?s?.starting_paper_equity:null)}</strong></span><span>Currently deployed capital<strong>{money(fresh?deployed:null)}</strong></span><span>Available cash<strong>{money(fresh?s?.account?.cash:null)}</strong></span><span>Strategy P/L since inception<strong className={valid&&s.strategy_pnl>=0?'pGain':'pLoss'}>{money(valid?s?.strategy_pnl:null)}</strong></span><span>Today / allocation<strong>{pct(valid?s?.daily_pnl/600:null)}</strong></span><span>Observed win rate<strong>{valid&&closed?`${(s.wins/closed*100).toFixed(1)}%`:'—'}</strong></span><span>Average exit fill P/L<strong>{money(valid&&trades.length?trades.reduce((sum:number,t:Data)=>sum+Number(t.realized_pnl??0),0)/trades.length:null)}</strong></span><span>Cash reconciliation<strong>{fresh?(s?.cash_consistent?'Consistent':'Blocked'):'Unavailable'}</strong></span></div></section><section className="pPanel"><div className="pPanelTitle"><div><h3>Performance observations</h3><p>Cumulative P/L from broker fills and marks. Benchmark curves are hypothetical passive allocations at genuine inception.</p></div><div className="pSegment">{[['realized','Realized'],['unrealized','Unrealized'],['spy','SPY'],['passive','Passive selection']].map(([v,l])=><button key={v} className={view===v?'active':''} onClick={()=>setView(v)}>{l}</button>)}</div></div><Line values={values} labels={labels} kind={view}/></section><div className="pBottomGrid"><section className="pPanel"><div className="pPanelTitle"><h3>Exposure by stock</h3><span className="pBadge neutral">ACTUAL MARKET VALUE</span></div>{fresh?<div className="pBars">{symbols.map(symbol=>{const value=Number(s?.positions?.find((p:Data)=>p.symbol===symbol)?.market_value??0);return <div key={symbol}><span>{symbol}</span><i><b style={{width:`${Math.min(100,value)}%`}}/></i><strong>{money(value)}</strong></div>})}</div>:<Empty title="Holdings unavailable">Verify the dedicated paper account to inspect exposure.</Empty>}</section><section className="pPanel"><div className="pPanelTitle"><h3>Closed trades</h3></div>{valid?<div className="pBars">{[['Winning',s.wins],['Losing',s.losses]].map(([label,n])=><div key={label}><span>{label}</span><i><b className={label==='Losing'?'loss':''} style={{width:`${closed?Number(n)/closed*100:0}%`}}/></i><strong>{n}</strong></div>)}<p>{closed?`${closed} winning/losing closed lots. Break-even lots are excluded.`:'No winning or losing closed lots yet.'}</p></div>:<Empty title="No verified performance yet">Results will use reconciled fills; account resets are excluded.</Empty>}</section></div><section className="pPanel"><div className="pPanelTitle"><div><h3>Original open-to-close forecast evidence</h3><p>Open-to-close forecasting outcomes differ from fill-to-exit trading P/L. No automatic promotion.</p></div></div><div className="pTableScroll"><table><thead><tr><th>Horizon</th><th>Resolved / dates</th><th>Return MAE</th><th>Brier / log loss</th><th>Interval coverage</th><th>Verdict</th></tr></thead><tbody>{data?.forecast_metrics?.map((r:Data)=><tr key={r.horizon}><td>{r.horizon} sessions</td><td>{r.observations} / {r.distinct_dates??0}</td><td>{r.mae==null?'—':pct(r.mae)}</td><td>{r.brier==null?'—':`${r.brier.toFixed(4)} / ${r.log_loss.toFixed(4)}`}</td><td>{r.interval_coverage==null?'—':`${(r.interval_coverage*100).toFixed(1)}%`}</td><td>{r.status}<small>{r.reason}</small></td></tr>)}</tbody></table></div></section></>;

}

export function PaperDashboard(){

  const [auth,setAuth]=useState<boolean|null>(null),[csrf,setCsrf]=useState(''),[username,setUsername]=useState('admin'),[password,setPassword]=useState('');

  const [data,setData]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[transport,setTransport]=useState('Connecting');

  const symbols:string[]=(data?.stocks??[]).map((s:Data)=>s.symbol);
  const names:Record<string,string>={...knownNames,...Object.fromEntries((data?.stocks??[]).map((s:Data)=>[s.symbol,s.name??s.symbol]))};
  const [selected,setSelected]=useState('AMD'),[chart,setChart]=useState('price'),[tab,setTab]=useState('Overview'),[notice,setNotice]=useState('');

  const load=useCallback(async()=>{try{setData(await request('paper/status'));setError('')}catch(e:any){if(e.status===401){setAuth(false);setData(null)}setError(e.message)}},[]);

  useEffect(()=>{if(data?.stocks?.length&&!data.stocks.some((s:Data)=>s.symbol===selected))setSelected(data.stocks[0].symbol)},[data,selected]);
  useEffect(()=>{request('auth/session').then(s=>{setCsrf(s.csrf_token);setAuth(true)}).catch(()=>setAuth(false))},[]);

  useEffect(()=>{

    if(!auth)return;load();const timer=setInterval(load,15000);

    const stream=new EventSource(`${base}/api/v1/paper/events`);

    stream.onmessage=e=>{try{setData(JSON.parse(e.data));setTransport('Connected · live page updates')}catch{setTransport('Polling · 15 sec')}};

    stream.onerror=()=>setTransport('Polling · 15 sec');

    return()=>{clearInterval(timer);stream.close()};

  },[auth,load]);

  async function signIn(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{const s=await request('auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});setPassword('');setCsrf(s.csrf_token);setAuth(true)}catch(e:any){setError(e.message)}finally{setBusy(false)}}

  async function control(enabled:boolean){setBusy(true);setNotice('');try{const s=await request('paper/control',{method:'POST',headers:{'Content-Type':'application/json','x-csrf-token':csrf},body:JSON.stringify({enabled})});setNotice(data?.experimental?(enabled?'Both PAPER accounts enabled. Trading waits for an open market and fresh valid quotes.':'New submissions paused. Existing orders and positions remain open.'):(enabled?'PAPER pilot enabled. Session and risk gates still apply.':`Execution disabled. ${s.cancellation_unconfirmed?.length?'Some cancellations could not be confirmed. Review broker orders.':'Owned-order cancellation requested.'} Open positions remain open.`));await load()}catch(e:any){setError(e.message)}finally{setBusy(false)}}

  async function logout(){try{await request('auth/logout',{method:'POST',headers:{'x-csrf-token':csrf}});setAuth(false);setData(null)}catch(e:any){setError(e.message)}}

  if(auth!==true)return <main className="paperApp pLogin"><section className="pLoginStory"><a href={base} className="pBrand"><Mark/><span>Mainkraft</span></a><div><span className="pEyebrow">A PERSONAL RESEARCH WORKSPACE</span><h1>Every forecast.<br/>Every decision.<br/><em>On the record.</em></h1><p>Company research and a tightly bounded paper experiment. Follow the evidence, including the days the model stays out.</p><div className="pLoginTicks"><span><ShieldCheck size={16}/>Paper only</span><span><Database size={16}/>Source linked</span><span><FlaskConical size={16}/>Experimental</span></div></div><small>MSI / AXON research · News-driven paper research</small></section><section className="pLoginForm"><form onSubmit={signIn}><span className="pLock"><LockKeyhole size={24}/></span><span className="pEyebrow">PRIVATE ACCESS</span><h2>Open your workspace</h2><p>Sign in to view your research and paper account.</p><label htmlFor="username">Username</label><input id="username" autoComplete="username" required value={username} onChange={e=>setUsername(e.target.value)} maxLength={100}/><label htmlFor="password">Password</label><input id="password" autoComplete="current-password" type="password" required value={password} onChange={e=>setPassword(e.target.value)} maxLength={200}/>{error&&<p className="pError" role="alert">{error}</p>}<button className="pPrimary" disabled={busy||auth===null}>{auth===null?'Checking session…':busy?'Signing in…':'Sign in'}<ChevronRight size={17}/></button><small><ShieldCheck size={14}/>Secure session. Credentials stay on the server.</small></form></section></main>;

  // Do not mount the legacy research panels while the active paper mode is
  // still loading: those panels request routes absent from this deployment.
  if(!data)return <main className="paperApp pMain"><section className="pPanel" aria-live="polite"><h2>Loading paper accounts…</h2><p>Fetching the current trading mode and verified account observations.</p>{error&&<p className="pError" role="alert">{error}</p>}<button className="pSecondary" onClick={load}>Retry</button><button className="pSecondary" onClick={logout}>Sign out</button></section></main>;

  const snapshot=data?.snapshot?.payload,controls=data?.controls;

  const paper=data?.connections?.find((c:Data)=>c.id==='paper');

  const verified=paper?.payload?.status==='verified';

  const fresh=verified&&Date.now()-new Date(paper.observed_at).getTime()<60000;

  const pnl=snapshot?.account_pnl_available&&fresh?snapshot:null;

  const stock=data?.stocks?.find((s:Data)=>s.symbol===selected),decision=stock?.decision;

  const bars=stock?.daily_bars??[],curve=data?.curve??[];

  const quotesFresh=stock?.quote&&Date.now()-new Date(stock.quote.source_time).getTime()<15000;

  const enabled=controls?.enabled&&data?.execution_permitted;

  const plotValues=chart==='price'?bars.map((b:Data)=>Number(b.close)):curve.map((c:Data)=>Number(c[chart]));

  const plotLabels=chart==='price'?bars.map((b:Data)=>b.session_date):curve.map((c:Data)=>c.time);

  if(data?.experimental)return <PaperLearning data={data} busy={busy} error={error} notice={notice} onControl={control} onRefresh={load} onLogout={logout}/>;

  return <div className="paperApp pLayout"><aside className="pSidebar"><a href={base} className="pBrand"><Mark/><span>Mainkraft</span></a><div className="pWorkspace"><span>ER</span><div>Personal workspace<small>Research + paper pilot</small></div><LockKeyhole size={13}/></div><span className="pNavLabel">WORKSPACE</span>{['Overview','Portfolio','Orders & activity','Data & protocol'].map((t,i)=>{const Icon=[Layers3,TrendingUp,Activity,Database][i];return <button key={t} className={`pNav ${tab===t?'isActive':''}`} onClick={()=>setTab(t)}><Icon size={17}/>{t}{tab===t&&<i/>}</button>})}<span className="pNavLabel pWatchLabel">DAILY RESEARCH <b>{symbols.length.toString().padStart(2,'0')}</b></span><div className="pWatch">{symbols.map(s=>{const row=data?.stocks?.find((r:Data)=>r.symbol===s);return <button key={s} onClick={()=>{setSelected(s);setChart('price');setTab('Overview')}} className={selected===s?'selected':''}><span className={`pTickerLogo ticker-${s}`}>{s.slice(0,1)}</span><span><strong>{s}</strong><small>{names[s]}</small></span><b className={row?.daily_change>=0?'pGain':'pLoss'}>{pct(row?.daily_change)}</b></button>})}</div><div className="pSidebarBottom"><a href={`${base}/companies/MSI`}><FlaskConical size={16}/>MSI / AXON research<ChevronRight size={14}/></a><div><ShieldCheck size={17}/><span>Paper trading only<small>No live-money execution</small></span></div><button className="pNav" onClick={logout}><LogOut size={16}/>Sign out</button></div></aside>

    <div className="pShell"><header className="pTopbar"><span>Workspace <ChevronRight size={12}/><strong>{tab}</strong></span><span><i className={`pDot ${fresh?'pDotLive':''}`}/>{fresh?'PAPER account verified':'PAPER account unavailable'}<span className="pAvatar">ER</span></span></header><main className="pMain"><div className="pPageHeading"><div><span className="pEyebrow">RESEARCH / PAPER EXPERIMENT</span><h1>Your market workspace<span className="pBadge">PAPER</span></h1><p>Up to 20 stocks, researched before the open. Every decision stays on record.</p></div><div className="pHeadingActions"><button className="pSecondary" onClick={load}><RefreshCw size={14}/>Refresh</button><button className="pStop" onClick={()=>control(false)} disabled={busy||!data}><Square size={12} fill="currentColor"/>Emergency stop</button></div></div>

      {error&&<div className="pError" role="alert">{error}</div>}{notice&&<div className="pNotice" role="status">{notice}</div>}

      {data?.discovery_enabled&&<section className="pPanel" style={{marginBottom:20}}><div className="pPanelTitle"><div><h3>Premarket news research · up to 20 stocks</h3><p>{data.selection_active?(data.mainkraft_enabled ? 'Today’s selection is frozen. Day and Swing entries depend on estimated net return and risk checks.' : 'Today’s selection is frozen. Orders still require the independent numerical 70% gate.'):data.research?'Research preview · daily selection will be refreshed before the next session; preview is not authorization to trade.':'Research starts 4:30 AM Pacific on trading days. No fixed watchlist fallback.'}</p></div><span className="pBadge neutral">{data.research?.payload?.model?.model??'Awaiting research'}</span></div>{data.research&&<div className="pTableScroll"><table><thead><tr><th>Stock</th><th>News assessment</th><th>Sourced rationale</th></tr></thead><tbody>{data.research.payload.picks.map((p:Data)=><tr key={p.symbol}><td>{p.symbol}</td><td>{p.stance}</td><td style={{whiteSpace:'normal',minWidth:250}}>{p.rationale}<small>News IDs: {p.source_ids.join(', ')}</small></td></tr>)}</tbody></table></div>}</section>}
      <div className={`pGate ${enabled?'enabled':''}`}><ShieldCheck size={19}/><span><strong>{enabled?'Paper pilot enabled · risk gates enforced':'Execution is disabled'}</strong><small>{controls?.reason??'Connecting to the isolated paper service.'}{!data?.execution_permitted?' · Deployment/account acceptance is required.':''}</small></span><span className="pGateTag">{enabled?'PAPER ONLY':'NO ORDERS'}</span></div>

      <div className="pStats">{[['Paper account equity',fresh?snapshot?.account?.equity:null,'Actual broker balance'],['Reference allocation',600,'Used for benchmark comparisons'],['Daily P/L',pnl?.daily_pnl,'Since first observed balance today'],['Realized / unrealized',pnl?.realized_pnl,pnl?`Unrealized ${money(pnl.unrealized_pnl)}`:'Awaiting verified account']].map(([label,value,caption],i)=><section key={String(label)}><span>{label}</span><strong className={i>=2&&value!=null?(Number(value)>=0?'pGain':'pLoss'):''}>{money(value)}</strong><small>{caption}</small></section>)}</div>

      {tab==='Overview'&&<><SessionReport selectedSymbol={selected}/><AutonomyPanel/><Phase7Research/><StrategyLab/><div className={data?.mainkraft_enabled ? "pChartGrid pSingleChart" : "pChartGrid"}><section className="pPanel"><div className="pPanelTitle"><div><span className="pEyebrow">{chart==='price'?'MARKET HISTORY':'OBSERVED ACCOUNT RECORD'}</span><h2>{chart==='price'?`${selected} · Daily price`:'Strategy performance'}</h2></div><div className="pSegment">{[['price','Price'],['equity','Equity'],['daily_pnl','Day P/L'],['drawdown','Drawdown']].map(([v,l])=><button key={v} onClick={()=>setChart(v)} className={chart===v?'active':''}>{l}</button>)}</div></div><div className="pChartHeadline"><strong>{chart==='price'?money(bars.at(-1)?.close):money(curve.at(-1)?.[chart])}</strong>{chart==='price'?<span className={stock?.daily_change>=0?'pGain':'pLoss'}>{stock?.daily_change>=0?<ArrowUpRight size={15}/>:<ArrowDownRight size={15}/>} {pct(stock?.daily_change)} <small>last completed session</small></span>:<span><i className="pDot"/>Actual observations only</span>}</div><Line values={plotValues} labels={plotLabels} kind={chart}/><div className="pChartFooter"><span>{chart==='price'?`SIP · split adjusted · ${bars.at(-1)?.session_date??'unavailable'} · excludes dividends`:'Last 360 observations · not a backtest'}</span><span>{chart==='price'?'90 SESSIONS':`${curve.length} OBSERVATIONS`}</span></div></section>{!data?.mainkraft_enabled&&<section className="pPanel pSignal"><div className="pPanelTitle"><h3>Model decision</h3><FlaskConical size={17}/></div><div className="pSignalState"><span className="pBadge">{decision?.action??'PENDING'}</span><h2>{decision?.action==='BUY'?'Entry signal':decision?.action==='HOLD'?'Staying out':decision?.action==='SKIP'?'Session skipped':'Waiting for premarket'}</h2><p>{decision?.payload?.reason??'The pilot records decisions at 09:15–09:25 ET. Missing readiness produces a skip; no trade is forced.'}</p></div>{[1,5].map(h=>{const f=decision?.payload?.horizons?.[String(h)];return <div className="pHorizon" key={h}><span>{h}-session estimate<strong>{pct(f?.expected_return)}</strong></span><span>Positive return<strong>{f?`${(f.probability_positive*100).toFixed(1)}%`:'—'}</strong></span></div>})}<div className="pSignalFoot"><Clock3 size={14}/><span>{decision?stamp(decision.issued_at):'Cutoff 09:15 ET · next eligible session'}<small>Experimental · no established predictive edge</small></span></div></section>}</div>

      <section className="pPanel pUniverse"><div className="pPanelTitle"><div><h3>Daily stock selection</h3><p>Daily performance and coverage. Select a stock to inspect its chart.</p></div><span className="pBadge neutral">{data?.risk?.swing_trial_exclusive ? "$800 SWING TRIAL" : "$600 REGULAR EXPOSURE CAP"}</span></div><div className="pTableScroll"><table><thead><tr><th>Company</th><th>Available IEX quote</th><th>Last daily close</th><th>Daily change</th><th>History</th><th>Signal</th><th>Position</th><th>Pending orders</th></tr></thead><tbody>{symbols.map(s=>{const row=data?.stocks?.find((r:Data)=>r.symbol===s),pos=snapshot?.positions?.find((p:Data)=>p.symbol===s);return <tr key={s} className={s===selected?'selected':''} onClick={()=>{setSelected(s);setChart('price')}}><td><button className="pCompany" onClick={()=>{setSelected(s);setChart('price')}}><span className={`pTickerLogo ticker-${s}`}>{s[0]}</span><span><strong>{s}</strong><small>{names[s]}</small></span></button></td><td>{money(row?.quote?.price)}<small>{stamp(row?.quote?.source_time)} · {row?.quote&&Date.now()-new Date(row.quote.source_time).getTime()<15000?'fresh IEX':'stale / unavailable'}</small></td><td>{money(row?.daily_bars?.at(-1)?.close)}</td><td className={row?.daily_change>=0?'pGain':'pLoss'}>{pct(row?.daily_change)}</td><td>{row?.sessions?`${number(row.sessions)} sessions`:'Unavailable'}<small>{row?.daily_bars?.at(-1)?.session_date??'—'}</small></td><td><span className="pBadge neutral">{row?.decision?.action??'Pending'}</span></td><td>{fresh?(pos?money(pos.market_value):'No position'):'Unavailable'}</td><td>{data?.intents?.filter((o:Data)=>o.symbol===s&&!['filled','canceled','expired','rejected'].includes(o.state)).length??'—'}</td></tr>})}</tbody></table></div></section>

      <div className="pBottomGrid"><section className="pPanel"><div className="pPanelTitle"><h3>Execution & connectivity</h3><Radio size={17}/></div><dl className="pDetails"><div><dt>Live quote · {selected}</dt><dd>{money(stock?.quote?.price)}<small>{quotesFresh?'Fresh IEX quote':'Stale / market closed / unavailable'}</small></dd></div><div><dt>Quote timestamp</dt><dd>{stamp(stock?.quote?.source_time)}</dd></div><div><dt>Page updates</dt><dd>{transport}</dd></div><div><dt>Broker stream</dt><dd>{data?.connections?.find((c:Data)=>c.id==='orders_stream')?.payload?.status??'Awaiting dedicated account'}</dd></div></dl></section><section className="pPanel"><div className="pPanelTitle"><h3>Bounded by design</h3><ShieldCheck size={17}/></div><div className="pRiskGrid"><span>Per regular stock<strong>$100</strong></span><span>Regular exposure<strong>$600</strong></span>{data?.risk?.swing_trial_exclusive&&<span>Exclusive Swing trial<strong>{money(data.risk.swing_trial_notional)}</strong></span>}<span>Daily loss breaker<strong>{money(data?.risk?.daily_loss??12)}</strong></span><span>Stop / take profit<strong>2% / 3%</strong></span></div><p className="pFootnote">Long only · no leverage · one entry per stock per session. Emergency stop disables submissions and cancels owned pending orders; positions remain open.</p></section></div></>}

      {tab==='Portfolio'&&<><Performance data={data} fresh={fresh}/><section className="pPanel"><div className="pPanelTitle"><div><h3>Positions & reconciled performance</h3><p>{snapshot?.pnl_basis??'Actual broker holdings appear after account verification.'}</p></div><span className="pBadge neutral">{fresh?'VERIFIED':'UNAVAILABLE'}</span></div>{!fresh?<Empty title="Dedicated account not available">Balances, positions, wins, losses and profits remain unavailable.</Empty>:<><div className="pRiskGrid"><span>Cash<strong>{money(snapshot?.account.cash)}</strong></span><span>Buying power<strong>{money(snapshot?.account.buying_power)}</strong></span><span>Closed wins / losses<strong>{pnl?`${snapshot.wins} / ${snapshot.losses}`:'—'}</strong></span><span>Observed drawdown<strong>{pct(data?.max_observed_drawdown)}</strong></span></div><div className="pTableScroll"><table><thead><tr><th>Symbol</th><th>Quantity</th><th>Average entry</th><th>Cost basis</th><th>Market value</th><th>Unrealized P/L</th></tr></thead><tbody>{snapshot.positions.map((p:Data)=><tr key={p.symbol}><td>{p.symbol}</td><td>{number(p.qty)}</td><td>{money(p.avg_entry_price)}</td><td>{money(p.cost_basis)}</td><td>{money(p.market_value)}</td><td className={Number(p.unrealized_pl)>=0?'pGain':'pLoss'}>{money(p.unrealized_pl)}</td></tr>)}</tbody></table></div>{!snapshot.positions.length&&<Empty title="No open positions">No qualifying position is currently reported by the dedicated paper account.</Empty>}</>}</section><section className="pPanel"><div className="pPanelTitle"><div><h3>Same-inception benchmarks</h3><p>Hypothetical passive $600 allocations using actual IEX mid observations. Excludes dividends and costs.</p></div></div>{data?.benchmarks?.length?<div className="pRiskGrid"><span>Strategy allocation equity<strong>{money(pnl?.strategy_equity)}</strong></span><span>SPY passive<strong>{money(data.benchmarks.at(-1).payload.spy_equity)}</strong></span><span>Inception selection passive<strong>{money(data.benchmarks.at(-1).payload.passive_six_equity)}</strong></span></div>:<Empty title="Benchmark tracking has not started">It begins with fresh marks for the benchmark and inception selection during an enabled market session.</Empty>}</section></>}

      {tab==='Orders & activity'&&<><section className="pPanel"><div className="pPanelTitle"><div><h3>Durable order record</h3><p>Intent is persisted before submission. Uncertain acknowledgements block a repeat submission.</p></div></div>{data?.intents?.length?<div className="pTableScroll"><table><thead><tr><th>Submitted</th><th>Symbol / side</th><th>State</th><th>Notional / quantity</th><th>Client order ID</th></tr></thead><tbody>{data.intents.map((o:Data)=><tr key={o.client_id}><td>{stamp(o.created_at)}</td><td>{o.symbol} · {o.side}</td><td>{o.state}</td><td>{o.notional?money(o.notional):number(o.quantity)}</td><td><details><summary>{o.client_id.slice(0,24)}…</summary><pre>{JSON.stringify(o,null,2)}</pre></details></td></tr>)}</tbody></table></div>:<Empty title="No orders have been submitted">The pilot records HOLD and SKIP decisions without generating an order.</Empty>}</section><section className="pPanel"><div className="pPanelTitle"><h3>Recent activity</h3></div><div className="pActivity">{data?.events?.length?data.events.map((e:Data)=><details key={e.id}><summary><span className="pDot"/><strong>{e.kind.replaceAll('_',' ')}</strong><small>{stamp(e.created_at)}</small></summary><pre>{JSON.stringify(e.payload,null,2)}</pre></details>):<Empty title="No pilot events yet">Connection failures and skipped decisions remain visible here.</Empty>}</div></section></>}

      {tab==='Data & protocol'&&<><section className="pPanel"><div className="pPanelTitle"><div><h3>Source coverage</h3><p>Facts are informational. The pilot uses only the predeclared price and market features.</p></div></div><div className="pTableScroll"><table><thead><tr><th>Stock</th><th>Daily sessions</th><th>SEC financials</th><th>Recent news records</th></tr></thead><tbody>{data?.stocks?.map((s:Data)=><tr key={s.symbol}><td>{s.symbol}</td><td>{number(s.sessions)}<small>SIP · splits only · through {s.daily_bars.at(-1)?.session_date}</small></td><td>{s.fundamentals?.status==='available'?'Source-linked GAAP facts':s.fundamentals?.reason??'Unavailable'}<details><summary>Inspect source facts</summary><pre>{JSON.stringify(s.fundamentals,null,2)}</pre></details></td><td>{s.news_records}<small>Recent metadata only · capped per retrieval</small></td></tr>)}</tbody></table></div></section><section className="pPanel"><div className="pPanelTitle"><h3>Predeclared pilot protocol</h3><FlaskConical size={18}/></div><dl className="pDetails"><div><dt>Model</dt><dd>Fixed per-stock ridge + logistic regression</dd></div><div><dt>Cutoff / issuance</dt><dd>09:15 / 09:15–09:25 ET</dd></div><div><dt>Entry window</dt><dd>09:35–10:00 ET</dd></div><div><dt>Ordinary exit</dt><dd>15 minutes before exchange close</dd></div><div><dt>Training & calibration</dt><dd>80 / 20 chronological · 5-session purge</dd></div><div><dt>Entry rule</dt><dd>{data?.mainkraft_enabled ? "Net model thresholds: 50% Day / 55% Swing · positive net margin · sourced bullish news" : "Return > 0.15% · estimated P(positive) ≥ 70% · bullish sourced news · annual volatility ≤ 80%"}</dd></div><div><dt>Order size</dt><dd>$25 regular entry · $100 regular per-stock cap · 20 concurrent stocks maximum</dd></div><div><dt>Acceptance status</dt><dd>Experimental · no demonstrated predictive edge</dd></div></dl><p className="pFootnote">Missing pre-cutoff readiness or data skips the session. The frozen MSI/AXON research and its statistical acceptance rules remain independent.</p><div className="pControl"><button className="pPrimary" disabled={busy||!data?.execution_permitted||enabled} onClick={()=>control(true)}>Enable verified PAPER pilot</button><p>Requires dedicated credentials, a distinct pinned account ID, deployment acceptance, and successful reconciliation.</p></div></section><section className="pPanel"><div className="pPanelTitle"><h3>Limitations & status</h3></div><ul className="pLimitations">{data?.limitations?.map((l:string)=><li key={l}>{l}</li>)}</ul><div className="pActivity">{data?.connections?.map((c:Data)=><div key={c.id}><strong>{c.id.replaceAll('_',' ')} · {c.payload.status}</strong><small>{c.payload.reason??stamp(c.observed_at)}</small></div>)}</div></section></>}

      <footer className="pFooter"><span><ShieldCheck size={14}/>Private research · experimental PAPER execution</span><span>Updated {stamp(data?.generated_at)} · Times in New York</span></footer></main></div></div>;

}

