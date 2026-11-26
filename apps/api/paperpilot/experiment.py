"""Two-account PAPER learning mode. No fixed capital/count/confidence/day-loss caps.

Broker limits, account identity, fresh quotes, order reconciliation and the
operator stop are correctness boundaries. Paper P/L is not evidence of live alpha.
"""
import asyncio
import json
import math
import logging
from datetime import datetime,timedelta,time
from decimal import Decimal,ROUND_DOWN
from pathlib import Path

import httpx
import numpy as np

from intelligence.calendar import EASTERN,schedule
from intelligence.providers import fetch_bars,normalize_bars
from intelligence.util import digest,now
from paperpilot.config import PAPER_BASE,SYMBOLS,settings
from paperpilot.db import connect,jb
from paperpilot.strategy import bundle,features,forecast
from paperpilot.intraday import COST

VERSION='paper-learning-v1'
TERMINAL={'filled','canceled','expired','rejected','replaced'}
SCHEMA="""
CREATE TABLE IF NOT EXISTS learning_accounts(account_id TEXT PRIMARY KEY,label TEXT NOT NULL,account_number TEXT NOT NULL,
 baseline_equity NUMERIC NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS learning_records(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,kind TEXT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),payload JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS learning_records_lookup ON learning_records(account_id,kind,recorded_at DESC);
CREATE TABLE IF NOT EXISTS learning_intents(client_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,prediction_id TEXT NOT NULL,
 symbol TEXT NOT NULL,phase TEXT NOT NULL,state TEXT NOT NULL,body JSONB NOT NULL,order_payload JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),UNIQUE(account_id,prediction_id,symbol,phase));
CREATE TABLE IF NOT EXISTS learning_controls(id INTEGER PRIMARY KEY CHECK(id=1),enabled BOOLEAN NOT NULL DEFAULT false);
INSERT INTO learning_controls VALUES(1,false) ON CONFLICT DO NOTHING;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_learning_records') THEN
CREATE TRIGGER immutable_learning_records BEFORE UPDATE OR DELETE ON learning_records
FOR EACH ROW EXECUTE FUNCTION reject_pilot_evidence_mutation(); END IF; END $$;
"""


def migrate():
    with connect() as c:c.execute(SCHEMA,prepare=False)


def record(account,kind,payload,c=None):
    key=digest([account,kind,payload])
    if c is None:
        with connect() as db:record(account,kind,payload,db)
    else:c.execute('INSERT INTO learning_records(id,account_id,kind,payload) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                   (key,account,kind,jb(payload)))
    return key


def accounts():
    second=json.loads(Path(settings.paper_experiment_accounts_file).read_text())
    first={'id':settings.paper_account_id,'key':settings.paper_key,'secret':settings.paper_secret,
           'label':'Original account · fixed model','policy':'control'}
    second={'id':second['account_id'],'key':second['key'],'secret':second['secret'],
            'label':'Second account · online learning','policy':'adaptive','account_number':second['expected_account_number']}
    if not first['id'] or not second['id'] or first['id']==second['id']:raise ValueError('Distinct pinned paper accounts required')
    return [first,second]


class Broker:
    def __init__(self,account,client=None):
        self.config=account;self.client=client or httpx.Client(timeout=15,follow_redirects=False)

    def request(self,method,path,**kwargs):
        import re
        if method!='GET' and not(method=='POST' and path=='/v2/orders') and not(method=='DELETE' and re.fullmatch(r'/v2/orders/[a-fA-F0-9-]{36}',path)):
            raise ValueError('Unsupported paper operation')
        if not(path in {'/v2/account','/v2/clock','/v2/positions','/v2/orders','/v2/orders:by_client_order_id'} or re.fullmatch(r'/v2/assets/[A-Z0-9.\-]{1,10}',path) or re.fullmatch(r'/v2/orders/[a-fA-F0-9-]{36}',path)):
            raise ValueError('Unsupported paper route')
        if method in {'POST','DELETE'}:
            a=self.account()
            if a['id']!=self.config['id'] or a.get('status')!='ACTIVE' or a.get('trading_blocked') or a.get('account_blocked'):
                raise ValueError('Pinned active paper account required')
        r=self.client.request(method,PAPER_BASE+path,headers={'APCA-API-KEY-ID':self.config['key'],
                               'APCA-API-SECRET-KEY':self.config['secret']},**kwargs)
        if r.status_code==404 and path=='/v2/orders:by_client_order_id':return None
        if method=='POST' and 400<=r.status_code<500:
            try:reason=str(r.json().get('message',''))[:300]
            except (ValueError,AttributeError):reason=''
            for value in (self.config['key'],self.config['secret']):
                if value:reason=reason.replace(value,'[redacted]')
            raise OrderRejected(f'Paper API HTTP {r.status_code}: {reason}')
        if r.status_code>=400:raise ValueError(f'Paper API HTTP {r.status_code}')
        return r.json() if r.content else {}

    def account(self):
        a=self.request('GET','/v2/account')
        if a['id']!=self.config['id']:raise ValueError('Different paper account returned')
        if self.config.get('account_number') and a.get('account_number')!=self.config['account_number']:raise ValueError('Different account number')
        return a

    def positions(self):return self.request('GET','/v2/positions')
    def orders(self):return self.request('GET','/v2/orders',params={'status':'open','limit':500})
    def by_client(self,key):return self.request('GET','/v2/orders:by_client_order_id',params={'client_order_id':key})

    def submit(self,body):
        qty=Decimal(str(body.get('qty',0)))
        if not qty.is_finite() or qty<=0 or body.get('side') not in {'buy','sell'} or body.get('type')!='market' or body.get('time_in_force')!='day' or not body.get('client_order_id','').startswith('mkx-'):
            raise ValueError('Invalid experiment order')
        return self.request('POST','/v2/orders',json=body)


class OrderRejected(ValueError):
    """Broker explicitly declined the submission; there is no unknown fill."""


def allocate(observations,capacity,adaptive=False,slot='',correction=None):
    """Normalize model preference to broker-provided capacity, with no dollar cap."""
    if not math.isfinite(capacity) or capacity<0:raise ValueError('Invalid broker capacity')
    eligible=[]
    for row in observations:
        mu=row['prior_return'];x=np.asarray(row['x'])
        if adaptive and correction is not None:mu+=float(x@np.asarray(correction))
        sigma=max(row['uncertainty'],.0001)
        if not math.isfinite(mu) or not math.isfinite(sigma):raise ValueError('Invalid forecast')
        cost=row.get('cost_allowance',0.)
        score=math.copysign(max(0.,abs(mu)-cost),mu)/sigma
        direction=1 if mu>=0 else -1
        # Explore relative allocation, not random bullish/bearish reversals.
        # Reproducible variation never changes the recorded price forecast.
        exploration=0.
        if adaptive:
            seed=int(digest([slot,row['symbol']])[:8],16)
            exploration=float(np.random.default_rng(seed).normal(0,.10))
        if direction<0 and not row['shortable']:continue
        strength=math.exp(min(abs(score),10)+exploration)
        eligible.append((row,mu,direction,strength,cost))
    total=sum(t[3] for t in eligible)
    return {row['symbol']:{'target_value':direction*capacity*strength/total,'forecast_return':mu,
                           'source_id':row['source_id'],'x':row['x'],'prior_return':row['prior_return'],
                           'price':row['price'],'midpoint':row['midpoint'],'asset':row['asset'],'cost_allowance':cost}
            for row,mu,direction,strength,cost in eligible} if total else {}


def quantity_change(position,target,price,fractionable,shortable):
    """Close a sign reversal before opening its other side; never duplicate covers."""
    current=Decimal(str(position.get('qty',0))) if position else Decimal(0)
    desired=Decimal(str(target))/Decimal(str(price))
    if desired<0:
        if not shortable:return None
        desired=desired.quantize(Decimal('1'),rounding=ROUND_DOWN)
    elif not fractionable:desired=desired.quantize(Decimal('1'),rounding=ROUND_DOWN)
    if current*desired<0:desired=Decimal(0)
    delta=desired-current
    if not delta:return None
    qty=abs(delta).quantize(Decimal('0.000000001'),rounding=ROUND_DOWN)
    if qty<=0:return None
    reduction=(current>0 and delta<0) or (current<0 and delta>0)
    return {'side':'buy' if delta>0 else 'sell','qty':format(qty,'f'),'phase':'reduce' if reduction else 'increase'}


def target_time(instant):
    """One trading hour, carrying remaining minutes across exchange closure."""
    remaining=timedelta(hours=1)
    for day,session in schedule(instant.astimezone(EASTERN).date(),instant.date()+timedelta(days=14)).items():
        start=max(instant,session['open'])
        available=session['close']-start
        if available<=timedelta():continue
        if available>=remaining:return start+remaining
        remaining-=available
    raise ValueError('Exchange calendar unavailable')


class Market:
    def __init__(self):self.day=None;self.daily={};self.models=None

    def universe(self):
        names=set(SYMBOLS)
        with connect() as c:
            r=c.execute('SELECT payload FROM pilot_research_runs ORDER BY completed_at DESC LIMIT 1').fetchone()
        if r:
            names.update(r['payload'].get('candidates',{}))
            names.update(p['symbol'] for p in r['payload'].get('picks',[]))
        return sorted(names)

    def daily_inputs(self,symbol,instant):
        if self.day!=instant.astimezone(EASTERN).date():self.daily={};self.day=instant.astimezone(EASTERN).date()
        if symbol not in self.daily:
            raw,_,_=fetch_bars(symbol,(instant-timedelta(days=60)).date(),instant-timedelta(minutes=15),instant.date())
            self.daily[symbol]=normalize_bars(symbol,raw,settings.feed,instant.date(),now())
        return self.daily[symbol]

    def quote(self,symbol):
        with httpx.Client(timeout=10,follow_redirects=False) as client:
            r=client.get(f'https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest',params={'feed':'iex'},
               headers={'APCA-API-KEY-ID':settings.alpaca_key,'APCA-API-SECRET-KEY':settings.alpaca_secret})
            r.raise_for_status();q=r.json()['quote']
        stamp=datetime.fromisoformat(q['t'].replace('Z','+00:00'));instant=now()
        bid,ask=float(q['bp']),float(q['ap'])
        if not all(map(math.isfinite,(bid,ask))) or not 0<bid<=ask or not 0<=(instant-stamp).total_seconds()<=15:
            raise ValueError('Fresh valid execution quote unavailable')
        return {'bid':bid,'ask':ask,'source_time':stamp,'acquired_at':instant,'feed':'iex'}

    def observations(self,symbols,broker,instant):
        if self.models is None:self.models=bundle()
        spy=self.daily_inputs('SPY',instant);tech=self.daily_inputs('XLK',instant)
        spyq=self.quote('SPY');result=[]
        for symbol in symbols:
            try:
                own=self.daily_inputs(symbol,instant);row=features(instant.astimezone(EASTERN).date(),own,spy,tech)
                score=forecast(self.models['strategies']['day']['model'],row)
                asset=broker.request('GET','/v2/assets/'+symbol)
                if not asset or not asset.get('tradable') or asset.get('status')!='active' or asset.get('class')!='us_equity':raise ValueError('Broker asset unavailable')
                q=self.quote(symbol);mid=(q['bid']+q['ask'])/2
                prior=[r for r in own if str(r['session_date'])<str(instant.astimezone(EASTERN).date())][-1]
                sp=[r for r in spy if str(r['session_date'])<str(instant.astimezone(EASTERN).date())][-1]
                x=[1.,*row['x'],mid/float(prior['close'])-1,(spyq['bid']+spyq['ask'])/2/float(sp['close'])-1,q['ask']/q['bid']-1]
                source={'symbol':symbol,'quote':q,'daily_last':prior,'market_last':sp,'features':x,'model_bundle_id':self.models['bundle_id']}
                source_id=record('shared','source',source)
                result.append({'symbol':symbol,'x':x,'prior_return':(score['expected_net_return']+COST)/6.5,
                    'uncertainty':max((score['upper_return']-score['lower_return'])/2.56/math.sqrt(6.5),.0001),
                    'price':mid,'midpoint':mid,'shortable':bool(asset.get('shortable') and asset.get('easy_to_borrow')),
                    'asset':asset,'source_id':source_id,'quote_time':q['source_time'],'cost_allowance':q['ask']/q['bid']-1+.0010})
            except (ValueError,httpx.HTTPError,KeyError) as exc:
                record('shared','data_failure',{'symbol':symbol,'at':instant,'reason':str(exc)[:160]})
        return result


class Engine:
    def __init__(self,configs=None,brokers=None,market=None,clock=now):
        self.configs=configs or accounts();self.brokers=brokers or {a['id']:Broker(a) for a in self.configs}
        self.market=market or Market();self.clock=clock;self.last_slot={};self.last_snapshot={};self.last_enabled={}

    def reconcile(self,account,broker):
        with connect() as c:pending=c.execute("SELECT * FROM learning_intents WHERE account_id=%s AND state NOT IN ('filled','canceled','expired','rejected','replaced')",(account,)).fetchall()
        for intent in pending:
            order=broker.by_client(intent['client_id'])
            if order:
                previous=intent.get('order_payload') or {}
                if Decimal(str(order.get('filled_qty',0)))<Decimal(str(previous.get('filled_qty',0))):
                    raise ValueError('Broker cumulative fill regressed')
                record(account,'order_version',{'client_id':intent['client_id'],'order':order})
                with connect() as c:c.execute('UPDATE learning_intents SET state=%s,order_payload=%s WHERE client_id=%s',
                                              (order['status'],jb(order),intent['client_id']))
        with connect() as c:
            return c.execute("SELECT count(*) n FROM learning_intents WHERE account_id=%s AND state NOT IN ('filled','canceled','expired','rejected','replaced')",(account,)).fetchone()['n']

    def resolve_and_learn(self,config,observations,instant):
        account=config['id'];prices={r['symbol']:r['midpoint'] for r in observations}
        with connect() as c:
            pending=c.execute("SELECT id,payload FROM learning_records p WHERE account_id=%s AND kind='prediction' AND NOT EXISTS(SELECT 1 FROM learning_records o WHERE o.kind IN ('outcome','outcome_unavailable') AND o.payload->>'prediction_id'=p.id)",(account,)).fetchall()
            for row in pending:
                p=row['payload'];target=datetime.fromisoformat(p['target_time'])
                observed=next((o for o in observations if o['symbol']==p['symbol']),None)
                stamp=observed.get('quote_time',instant) if observed else instant
                if target<=stamp<=target+timedelta(minutes=5) and p['symbol'] in prices:
                    y=prices[p['symbol']]/p['midpoint']-1
                    record(account,'outcome',{'prediction_id':row['id'],'symbol':p['symbol'],'x':p['x'],
                        'prior_return':p['prior_return'],'forecast_return':p['forecast_return'],'actual_return':y,'resolved_at':instant,
                        'mark_basis':'IEX midpoint; hypothetical forecast outcome, not fill P/L'},c)
                elif instant>target+timedelta(minutes=5):
                    record(account,'outcome_unavailable',{'prediction_id':row['id'],'at':instant,'reason':'Fresh target-window quote unavailable'},c)
            rows=c.execute("SELECT payload FROM learning_records WHERE account_id=%s AND kind='outcome' ORDER BY recorded_at",(account,)).fetchall()
            previous=c.execute("SELECT payload FROM learning_records WHERE account_id=%s AND kind='model_update' ORDER BY recorded_at DESC LIMIT 1",(account,)).fetchone()
        if config['policy']!='adaptive' or not rows:return None
        if previous and previous['payload']['observations']==len(rows):return previous['payload']['coefficients']
        # Online residual ridge: each point is predicted before it can train.
        # Prior coefficients stay zero; fixed shrinkage prevents a single loss
        # from producing an extreme update. Prequential errors remain immutable.
        x=np.asarray([r['payload']['x'] for r in rows],dtype=float)
        y=np.asarray([r['payload']['actual_return']-r['payload']['prior_return'] for r in rows])
        scales=np.asarray([1.,*([.05]*11),.05,.05,.005])
        z=x/scales
        beta=np.linalg.solve(z.T@z+100*np.eye(z.shape[1]),z.T@y)/scales
        if not np.isfinite(beta).all():raise ValueError('Invalid learner update')
        record(account,'model_update',{'observations':len(rows),'outcome_identity':digest([r['payload']['prediction_id'] for r in rows]),
                                      'coefficients':beta.tolist(),'method':'prequential residual ridge alpha=100','at':instant})
        return beta.tolist()

    def submit(self,account,broker,prediction,symbol,change):
        key='mkx-'+digest([account,prediction,symbol,change['phase']])[:38]
        body={'symbol':symbol,'side':change['side'],'qty':change['qty'],'type':'market','time_in_force':'day','client_order_id':key}
        with connect() as c:
            enabled=c.execute('SELECT enabled FROM learning_controls WHERE id=1').fetchone()['enabled']
            if not enabled:return
            inserted=c.execute("INSERT INTO learning_intents(client_id,account_id,prediction_id,symbol,phase,state,body) VALUES(%s,%s,%s,%s,%s,'submitting',%s) ON CONFLICT DO NOTHING RETURNING client_id",
                        (key,account,prediction,symbol,change['phase'],jb(body))).fetchone()
        if not inserted:return
        try:
            order=broker.submit(body)
            if not order:raise ValueError('Order acknowledgement unknown')
            record(account,'order_version',{'client_id':key,'order':order})
            with connect() as c:c.execute('UPDATE learning_intents SET state=%s,order_payload=%s WHERE client_id=%s',(order['status'],jb(order),key))
        except OrderRejected as exc:
            with connect() as c:c.execute("UPDATE learning_intents SET state='rejected' WHERE client_id=%s",(key,))
            record(account,'order_rejected',{'client_id':key,'at':self.clock(),'reason':str(exc)})
        except (httpx.HTTPError,ValueError):
            with connect() as c:c.execute("UPDATE learning_intents SET state='unknown' WHERE client_id=%s",(key,))
            record(account,'unknown_acknowledgement',{'client_id':key,'at':self.clock(),'resubmitted':False})

    def tick(self):
        with connect() as lease:
            if not lease.execute('SELECT pg_try_advisory_lock(753194199) acquired').fetchone()['acquired']:return
            lease.commit()
            try:self._tick()
            finally:lease.execute('SELECT pg_advisory_unlock(753194199)')

    def _tick(self):
        instant=self.clock()
        for config in self.configs:
            instant=self.clock()
            account=config['id'];broker=self.brokers[account]
            try:
                pending=self.reconcile(account,broker);a=broker.account();positions=broker.positions();open_orders=broker.orders()
                with connect() as c:
                    c.execute('INSERT INTO learning_accounts(account_id,label,account_number,baseline_equity) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                              (account,config['label'],a['account_number'],a['equity']))
                    enabled=c.execute('SELECT enabled FROM learning_controls WHERE id=1').fetchone()['enabled']
                clock=broker.request('GET','/v2/clock')
                if abs((instant-datetime.fromisoformat(clock['timestamp'].replace('Z','+00:00'))).total_seconds())>30:raise ValueError('Broker clock mismatch')
                if self.last_enabled.get(account)!=enabled or self.last_snapshot.get(account) is None or instant-self.last_snapshot[account]>=timedelta(seconds=60 if clock['is_open'] else 180):
                    record(account,'snapshot',{'at':instant,'account':{k:a.get(k) for k in ('account_number','cash','equity','buying_power','regt_buying_power','multiplier','daytrading_buying_power','pattern_day_trader','daytrade_count','status','trading_blocked','account_blocked','shorting_enabled')},
                           'positions':positions,'open_orders':open_orders,'enabled':enabled,'market_open':clock['is_open'],
                           'next_open':clock.get('next_open'),'policy':config['policy']})
                    self.last_snapshot[account]=instant
                    self.last_enabled[account]=enabled
                slot=str(int(instant.timestamp())//300)
                if not enabled or not clock['is_open'] or self.last_slot.get(account)==slot:continue
                if a.get('account_blocked') or a.get('trading_blocked'):raise ValueError('Broker account blocks trading')
                if len(open_orders)>=500:raise ValueError('Broker open-order page is incomplete')
                self.last_slot[account]=slot
                symbols=sorted(set(self.market.universe())|{p['symbol'] for p in positions})
                observations=self.market.observations(symbols,broker,instant)
                if not observations:raise ValueError('No causal observations with fresh quotes')
                beta=self.resolve_and_learn(config,observations,self.clock())
                if pending or open_orders:continue # Observe outcomes while reservations block reallocation.
                # A durable cycle marker prevents replaying allocations after a
                # process restart, including a crash between prediction and POST.
                cycle=digest([account,VERSION,slot])
                with connect() as c:
                    if not c.execute("INSERT INTO learning_records(id,account_id,kind,payload) VALUES(%s,%s,'cycle',%s) ON CONFLICT DO NOTHING RETURNING id",
                                     (cycle,account,jb({'slot':slot,'at':self.clock()}))).fetchone():continue
                exposure=sum(abs(float(p['market_value'])) for p in positions)
                # Reg-T power is the broker's overnight constraint; no hard-coded
                # app dollar, position-count, daily-loss or confidence limit.
                available=min(float(a['buying_power']),float(a.get('regt_buying_power') or a['buying_power']))
                capacity=max(0.,available)+exposure
                plan=allocate(observations,capacity,config['policy']=='adaptive',account+slot,beta)
                prediction_ids={}
                for symbol,target in plan.items():
                    issuance_quote=self.market.quote(symbol)
                    target['midpoint']=(issuance_quote['bid']+issuance_quote['ask'])/2
                    prediction_ids[symbol]=record(account,'prediction',{'at':self.clock(),'target_time':target_time(self.clock()),
                        'symbol':symbol,'x':target['x'],'midpoint':target['midpoint'],'prior_return':target['prior_return'],
                        'forecast_return':target['forecast_return'],'target_value':target['target_value'],'slot':slot,
                        'source_id':target['source_id'],'policy':config['policy'],'policy_version':VERSION,
                        'round_trip_cost_allowance':target['cost_allowance'],
                        'issuance_quote':issuance_quote,
                        'forecast_basis':'Uncalibrated daily gross-return prior scaled to one trading hour; residual update from prior matured midpoint returns'})
                # Reduce reversals first, then use freshly observed broker power.
                for phase in ('reduce','increase'):
                    for symbol,target in plan.items():
                        if not broker.request('GET','/v2/clock')['is_open']:break
                        quote=self.market.quote(symbol)
                        price=quote['ask'] if target['target_value']>=0 else quote['bid']
                        positions=broker.positions();position=next((p for p in positions if p['symbol']==symbol),None)
                        change=quantity_change(position,target['target_value'],price,target['asset'].get('fractionable',False),target['asset'].get('shortable',False))
                        if not change or change['phase']!=phase:continue
                        fresh=broker.account();gross=Decimal(change['qty'])*Decimal(str(price))
                        if phase=='increase' and gross>Decimal(str(fresh['buying_power'])):
                            record(account,'broker_capacity_wait',{'symbol':symbol,'at':self.clock()});continue
                        self.submit(account,broker,prediction_ids[symbol],symbol,change)
                        if self.reconcile(account,broker):break
                    if self.reconcile(account,broker):break
                self.last_slot[account]=slot
            except (ValueError,KeyError,httpx.HTTPError) as exc:
                record(account,'runtime_failure',{'at':instant,'reason':str(exc)[:160]})

    async def run(self):
        while True:
            try:await asyncio.to_thread(self.tick)
            except Exception as exc:
                logging.getLogger(__name__).error('Paper experiment cycle failed: %s',type(exc).__name__)
            await asyncio.sleep(15)


def status():
    result=[]
    with connect() as c:
        enabled=c.execute('SELECT enabled FROM learning_controls WHERE id=1').fetchone()['enabled']
        for a in c.execute('SELECT * FROM learning_accounts ORDER BY created_at').fetchall():
            key=a['account_id'];last=c.execute("SELECT payload FROM learning_records WHERE account_id=%s AND kind='snapshot' ORDER BY recorded_at DESC LIMIT 1",(key,)).fetchone()
            counts={r['kind']:r['n'] for r in c.execute('SELECT kind,count(*) n FROM learning_records WHERE account_id=%s GROUP BY kind',(key,))}
            recent=c.execute('SELECT kind,payload FROM learning_records WHERE account_id=%s ORDER BY recorded_at DESC LIMIT 30',(key,)).fetchall()
            intents=c.execute('SELECT symbol,state,body,order_payload,created_at FROM learning_intents WHERE account_id=%s ORDER BY created_at DESC LIMIT 50',(key,)).fetchall()
            metrics=c.execute("""SELECT count(*) n,avg(abs((payload->>'actual_return')::float-(payload->>'forecast_return')::float)) mae,
                avg(CASE WHEN ((payload->>'actual_return')::float>0)=((payload->>'forecast_return')::float>0) THEN 1.0 ELSE 0.0 END) directional_accuracy
                FROM learning_records WHERE account_id=%s AND kind='outcome'""",(key,)).fetchone()
            result.append({'label':a['label'],'account_number':a['account_number'],'baseline_equity':a['baseline_equity'],
                           'snapshot':last['payload'] if last else None,'counts':counts,'recent':recent,'orders':intents,'forecast_metrics':metrics})
    return {'experimental':True,'paper_only':True,'policy_version':VERSION,'enabled':enabled,'accounts':result,
            'limits':'Broker buying power and asset permissions; no app capital/count/confidence/daily-loss limits',
            'learning':'Original account: fixed prior. Second: reproducible exploration and online residual learning from matured, pre-recorded forecasts.',
            'profitability':'Unvalidated experiment. Paper fills do not establish a live trading advantage.'}


def control(enabled):
    if enabled:
        for config in accounts():
            account=Broker(config).account()
            if account.get('status')!='ACTIVE' or account.get('account_blocked') or account.get('trading_blocked'):
                raise ValueError('Active unblocked paper accounts required')
    with connect() as c:c.execute('UPDATE learning_controls SET enabled=%s WHERE id=1',(enabled,))
    return {'enabled':enabled,'mode':VERSION,'positions':'Remain owned and visible; no automatic liquidation on operator pause'}


async def research_loop():
    """Retain the existing sourced premarket discovery, without its order worker."""
    from paperpilot.discovery import research
    from paperpilot.universe import selection
    attempted=None
    while True:
        instant=now();local=instant.astimezone(EASTERN)
        if settings.paper_discovery_enabled and local.date() in schedule(local.date(),local.date()) and time(7,30)<=local.time().replace(tzinfo=None)<time(9):
            if attempted is None or instant-attempted>=timedelta(minutes=20):
                attempted=instant
                try:
                    if not await asyncio.to_thread(selection,local.date()):
                        result=await asyncio.to_thread(research,local.date())
                        await asyncio.to_thread(record,'shared','research_update',{'at':now(),'result':result})
                except Exception as exc:
                    await asyncio.to_thread(record,'shared','research_failure',{'at':now(),'reason':type(exc).__name__})
        await asyncio.sleep(60)
