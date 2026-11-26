from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import numpy as np
import psycopg
import pytest
from test_paperpilot import paperdb  # noqa

from paperpilot import experiment as e
from paperpilot.db import connect, jb

AT=datetime(2026,11,19,15,0,tzinfo=UTC)
CONFIG={'id':'account-a','label':'Fixed','policy':'control','key':'test','secret':'test'}


@pytest.fixture
def db(paperdb):
    e.migrate()
    with connect() as c:c.execute('UPDATE learning_controls SET enabled=true')


def observation():
    return {'symbol':'AMD','x':[1.,*([.01]*14)],'prior_return':.001,'uncertainty':.01,
            'midpoint':100.,'price':100.,'quote_time':AT,'shortable':True,
            'asset':{'shortable':True,'fractionable':True},'source_id':'fixture'}


def fake_broker():
    b=Mock()
    b.account.return_value={'id':'account-a','account_number':'TEST','equity':'1000000','buying_power':'2000000','regt_buying_power':'2000000'}
    b.positions.return_value=[];b.orders.return_value=[]
    b.request.return_value={'timestamp':AT.isoformat(),'is_open':True}
    b.by_client.return_value=None
    b.submit.side_effect=lambda body:{**body,'id':'order','status':'filled','filled_qty':body['qty']}
    return b


def engine(b=None, config=None):
    market=Mock();market.universe.return_value=['AMD'];market.observations.return_value=[observation()]
    market.quote.return_value={'bid':99.99,'ask':100.01,'source_time':AT}
    return e.Engine([config or CONFIG],{'account-a':b or fake_broker()},market,lambda:AT)


def test_allocation_uses_broker_capacity_without_old_caps():
    rows=[observation(),{**observation(),'symbol':'MSFT','prior_return':-.001}]
    plan=e.allocate(rows,2000000)
    assert sum(abs(p['target_value']) for p in plan.values())==pytest.approx(2000000)
    assert plan['MSFT']['target_value']<0
    assert e.allocate(rows,2000000,True,'slot')==e.allocate(rows,2000000,True,'slot')
    with pytest.raises(ValueError):e.allocate(rows,float('inf'))


def test_sign_reversal_closes_before_short_and_short_uses_whole_shares():
    change=e.quantity_change({'qty':'2.25'},-1000,100,True,True)
    assert change=={'side':'sell','qty':'2.250000000','phase':'reduce'}
    assert e.quantity_change(None,-1050,100,True,True)['qty']=='10.000000000'
    assert e.quantity_change(None,-1050,100,True,False) is None


def test_broker_identity_routes_and_known_rejection():
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.host=='paper-api.alpaca.markets'
        if req.method=='GET':return httpx.Response(200,json={'id':'different','status':'ACTIVE'})
        return httpx.Response(422,json={'message':'declined'})
    broker=e.Broker(CONFIG,httpx.Client(transport=httpx.MockTransport(handler)))
    body={'qty':'1','side':'buy','type':'market','time_in_force':'day','client_order_id':'mkx-test'}
    with pytest.raises(ValueError,match='Different'):broker.submit(body)
    assert len(calls)==1
    with pytest.raises(ValueError):broker.request('DELETE','/v2/orders')
    with pytest.raises(ValueError):broker.request('GET','https://api.alpaca.markets/v2/account')
    broker.account=lambda:{'id':'account-a','status':'ACTIVE'}
    with pytest.raises(e.OrderRejected):broker.submit(body)


def test_restart_does_not_duplicate_cycle_or_order(db):
    b=fake_broker();engine(b).tick();engine(b).tick()
    assert b.submit.call_count==1
    with connect() as c:
        assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='prediction'").fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM learning_intents').fetchone()['n']==1
        with pytest.raises(psycopg.Error):c.execute("UPDATE learning_records SET kind='tampered'")


def test_unknown_ack_partial_fill_restart_and_final_fill(db):
    b=fake_broker();b.submit.side_effect=httpx.ReadTimeout('unknown')
    en=engine(b);en.submit('account-a',b,'prediction','AMD',{'side':'buy','qty':'10','phase':'increase'})
    engine(b).submit('account-a',b,'prediction','AMD',{'side':'buy','qty':'10','phase':'increase'})
    assert b.submit.call_count==1
    b.by_client.return_value={'status':'partially_filled','filled_qty':'3'}
    assert en.reconcile('account-a',b)==1
    b.by_client.return_value={'status':'partially_filled','filled_qty':'2'}
    with pytest.raises(ValueError,match='regressed'):en.reconcile('account-a',b)
    b.by_client.return_value={'status':'filled','filled_qty':'10'}
    assert engine(b).reconcile('account-a',b)==0


def test_rejected_order_does_not_block_future_cycles(db):
    b=fake_broker();b.submit.side_effect=e.OrderRejected('Paper API HTTP 422')
    en=engine(b);en.submit('account-a',b,'p','AMD',{'side':'buy','qty':'10','phase':'increase'})
    assert en.reconcile('account-a',b)==0
    with connect() as c:assert c.execute('SELECT state FROM learning_intents').fetchone()['state']=='rejected'


def test_future_labels_never_train_and_static_model_never_updates(db):
    config={**CONFIG,'policy':'adaptive'};en=engine(config=config)
    payload={'symbol':'AMD','at':AT,'target_time':AT+timedelta(hours=1),'midpoint':100.,'x':[1.,*([.01]*14)],'prior_return':.001,'forecast_return':.001}
    key=e.record('account-a','prediction',payload)
    assert en.resolve_and_learn(config,[observation()],AT) is None
    future={**observation(),'midpoint':99.,'quote_time':AT+timedelta(hours=1)}
    beta=en.resolve_and_learn(config,[future],AT+timedelta(hours=1))
    assert np.isfinite(beta).all()
    assert en.resolve_and_learn(config,[future],AT+timedelta(hours=1))==beta
    with connect() as c:
        outcome=c.execute("SELECT payload FROM learning_records WHERE kind='outcome'").fetchone()['payload']
        assert outcome['prediction_id']==key and outcome['actual_return']==pytest.approx(-.01)
        assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='model_update'").fetchone()['n']==1
    assert en.resolve_and_learn(CONFIG,[future],AT+timedelta(hours=1)) is None


def test_missing_outcomes_are_explicit_and_do_not_train(db):
    e.record('account-a','prediction',{'symbol':'AMD','target_time':AT-timedelta(minutes=6)})
    assert engine().resolve_and_learn({**CONFIG,'policy':'adaptive'},[],AT) is None
    with connect() as c:assert c.execute("SELECT count(*) n FROM learning_records WHERE kind='outcome_unavailable'").fetchone()['n']==1


def test_pause_market_closed_open_reservations_and_stale_data_prevent_orders(db):
    for condition in ('paused','closed','open_order','stale'):
        with connect() as c:c.execute('UPDATE learning_controls SET enabled=%s',(condition!='paused',))
        b=fake_broker();en=engine(b)
        if condition=='closed':b.request.return_value={'timestamp':AT.isoformat(),'is_open':False}
        if condition=='open_order':b.orders.return_value=[{'id':'existing'}]
        if condition=='stale':en.market.observations.side_effect=ValueError('stale quote')
        en.tick();assert not b.submit.called


def test_target_carries_across_close_and_holiday():
    at=datetime(2026,11,25,20,30,tzinfo=UTC)
    assert e.target_time(at)==datetime(2026,11,27,15,0,tzinfo=UTC)


def test_failed_account_does_not_stop_other_account(db):
    b1=fake_broker();b1.account.side_effect=ValueError('Account unavailable')
    b2=fake_broker();b2.account.return_value={**b2.account.return_value,'account_number':'SECOND'}
    en=engine(b2)
    en.configs=[{**CONFIG,'id':'failed'},CONFIG];en.brokers={'failed':b1,'account-a':b2}
    en.tick()
    assert not b1.submit.called and b2.submit.call_count==1


def test_blocked_broker_account_records_reason_without_intent(db):
    b=fake_broker();b.account.return_value['trading_blocked']=True
    engine(b).tick()
    assert not b.submit.called
    with connect() as c:
        assert c.execute('SELECT count(*) n FROM learning_intents').fetchone()['n']==0
        assert 'blocks trading' in c.execute("SELECT payload FROM learning_records WHERE kind='runtime_failure'").fetchone()['payload']['reason']


def test_quote_freshness_and_invalid_prices_are_not_relaxed(monkeypatch):
    original=httpx.Client
    for quote in ({'t':(AT-timedelta(seconds=16)).isoformat(),'bp':100,'ap':101},
                  {'t':AT.isoformat(),'bp':101,'ap':100}):
        monkeypatch.setattr(e,'now',lambda:AT)
        monkeypatch.setattr(e.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'quote':quote}))))
        with pytest.raises(ValueError,match='Fresh valid'):e.Market().quote('AMD')


def test_experiment_evidence_survives_verified_backup_restore(db,tmp_path):
    import opsbackup
    e.record('account-a','prediction',{'symbol':'AMD','at':AT})
    archive=tmp_path/'paper.zip'
    exported=opsbackup.export(archive,'paper')
    restored=opsbackup.verify_local(archive,'paper')
    assert restored['restore_verified'] and restored['sha256']==exported['sha256']
    assert restored['row_counts']['learning_records']==1
