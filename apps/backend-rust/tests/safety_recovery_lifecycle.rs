//! Full worker loop with two in-memory accounts and an isolated PostgreSQL DB.
//! No HTTP client, Alpaca credentials, or additional brokerage accounts exist.
use async_trait::async_trait;
use chrono::{DateTime, TimeZone, Utc};
use mainkraft::{
    Result,
    allocation::Observation,
    broker::{BrokerApi, BrokerError, BrokerResult, OrderBody},
    calendar::Calendar,
    db,
    engine::{AccountPolicy, Clock, Engine, MarketApi, stamp},
    features::Quote,
};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicI64, AtomicUsize, Ordering},
    },
};
use uuid::Uuid;

struct TestClock(AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> DateTime<Utc> {
        Utc.timestamp_opt(self.0.load(Ordering::SeqCst), 0)
            .single()
            .expect("fixture time")
    }
}
impl TestClock {
    fn set(&self, t: &str) {
        self.0.store(
            t.parse::<DateTime<Utc>>().expect("time").timestamp(),
            Ordering::SeqCst,
        );
    }
}
struct Book {
    cash: f64,
    positions: BTreeMap<String, f64>,
    orders: BTreeMap<String, Value>,
}
struct FakeBroker {
    id: String,
    clock: Arc<TestClock>,
    price: Arc<AtomicI64>,
    book: Mutex<Book>,
    calls: AtomicUsize,
    open: AtomicBool,
    blocked: AtomicBool,
}
impl FakeBroker {
    fn new(id: &str, clock: Arc<TestClock>, price: Arc<AtomicI64>) -> Self {
        Self {
            id: id.into(),
            clock,
            price,
            book: Mutex::new(Book {
                cash: 1000.,
                positions: BTreeMap::new(),
                orders: BTreeMap::new(),
            }),
            calls: AtomicUsize::new(0),
            open: AtomicBool::new(true),
            blocked: AtomicBool::new(false),
        }
    }
    fn midpoint(&self) -> f64 {
        self.price.load(Ordering::SeqCst) as f64 / 100.
    }
}
#[async_trait]
impl BrokerApi for FakeBroker {
    async fn account(&self) -> BrokerResult<Value> {
        let book = self.book.lock().expect("book");
        let price = self.midpoint();
        let equity = book.cash + book.positions.values().sum::<f64>() * price;
        let gross = book
            .positions
            .values()
            .map(|q| q.abs() * price)
            .sum::<f64>();
        let power = (2. * equity - gross).max(0.);
        Ok(
            json!({"id":self.id,"account_number":format!("TEST-{}",self.id),"status":"ACTIVE","equity":equity.to_string(),"cash":book.cash.to_string(),"buying_power":power.to_string(),"regt_buying_power":power.to_string(),"account_blocked":self.blocked.load(Ordering::SeqCst),"trading_blocked":false}),
        )
    }
    async fn clock(&self) -> BrokerResult<Value> {
        Ok(
            json!({"timestamp":stamp(self.clock.now()),"is_open":self.open.load(Ordering::SeqCst),"next_open":"2026-11-20T14:30:00+00:00"}),
        )
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        let book = self.book.lock().expect("book");
        Ok(book.positions.iter().map(|(symbol,qty)|json!({"symbol":symbol,"qty":qty.to_string(),"market_value":(qty*self.midpoint()).to_string()})).collect())
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        Ok(vec![])
    }
    async fn asset(&self, _: &str) -> BrokerResult<Value> {
        Ok(
            json!({"tradable":true,"status":"active","class":"us_equity","fractionable":true,"shortable":true,"easy_to_borrow":true}),
        )
    }
    async fn by_client(&self, key: &str) -> BrokerResult<Option<Value>> {
        Ok(self.book.lock().expect("book").orders.get(key).cloned())
    }
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let mut book = self.book.lock().expect("book");
        if book.orders.contains_key(&body.client_order_id) {
            return Err(BrokerError::Rejected("duplicate fixture client ID".into()));
        }
        let qty: f64 = body.qty.parse().map_err(|_| BrokerError::Invalid)?;
        let signed = if body.side == "buy" { qty } else { -qty };
        let price = self.midpoint() + if signed > 0. { 0.01 } else { -0.01 };
        book.cash -= signed * price;
        let remaining = book.positions.get(&body.symbol).copied().unwrap_or(0.) + signed;
        if remaining.abs() < 1e-8 {
            book.positions.remove(&body.symbol);
        } else {
            book.positions.insert(body.symbol.clone(), remaining);
        }
        let order = json!({"id":Uuid::new_v4(),"client_order_id":body.client_order_id,"symbol":body.symbol,"side":body.side,"status":"filled","qty":body.qty,"filled_qty":body.qty,"filled_avg_price":price.to_string()});
        book.orders
            .insert(body.client_order_id.clone(), order.clone());
        Ok(order)
    }
    async fn cancel(&self, _: Uuid) -> BrokerResult<()> {
        Ok(())
    }
}

struct FakeMarket {
    clock: Arc<TestClock>,
    price: Arc<AtomicI64>,
    prior: AtomicI64,
    missing: AtomicBool,
}
#[async_trait]
impl MarketApi for FakeMarket {
    async fn universe(&self) -> Result<Vec<String>> {
        Ok(vec!["AMD".into()])
    }
    async fn observations(
        &self,
        _: &[String],
        broker: &dyn BrokerApi,
        _: DateTime<Utc>,
    ) -> Result<Vec<Observation>> {
        if self.missing.load(Ordering::SeqCst) {
            return Ok(vec![]);
        }
        let quote = self.quote("AMD").await?;
        let mut x = vec![0.01; 15];
        x[0] = 1.;
        Ok(vec![Observation {
            symbol: "AMD".into(),
            x,
            prior_return: self.prior.load(Ordering::SeqCst) as f64 / 1e6,
            uncertainty: 0.01,
            price: quote.midpoint(),
            midpoint: quote.midpoint(),
            shortable: true,
            asset: broker.asset("AMD").await?,
            source_id: "fixture-source".into(),
            cost_allowance: 0.0012,
            quote_time: Some(self.clock.now()),
        }])
    }
    async fn quote(&self, _: &str) -> Result<Quote> {
        let mid = self.price.load(Ordering::SeqCst) as f64 / 100.;
        Ok(Quote {
            bid: mid - 0.01,
            ask: mid + 0.01,
            source_time: self.clock.now(),
            acquired_at: self.clock.now(),
            feed: "iex".into(),
        })
    }
}
fn worker(
    pool: PgPool,
    clock: Arc<TestClock>,
    market: Arc<dyn MarketApi>,
    a: Arc<dyn BrokerApi>,
    b: Arc<dyn BrokerApi>,
) -> Engine {
    let brokers: BTreeMap<String, Arc<dyn BrokerApi>> =
        BTreeMap::from([("control".into(), a), ("adaptive".into(), b)]);
    Engine::new(
        pool,
        vec![
            AccountPolicy {
                id: "control".into(),
                label: "Fixed fixture".into(),
                adaptive: false,
            },
            AccountPolicy {
                id: "adaptive".into(),
                label: "Adaptive fixture".into(),
                adaptive: true,
            },
        ],
        brokers,
        market,
        clock,
        Arc::new(Calendar::embedded().expect("calendar")),
    )
    .expect("worker")
}
async fn count(pool: &PgPool, account: &str, kind: &str) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM learning_records WHERE account_id=$1 AND kind=$2")
        .bind(account)
        .bind(kind)
        .fetch_one(pool)
        .await
        .expect("count")
}

struct HeldBroker {
    inner: Arc<FakeBroker>,
    hold: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait]
impl BrokerApi for HeldBroker {
    async fn account(&self) -> BrokerResult<Value> {
        self.inner.account().await
    }
    async fn clock(&self) -> BrokerResult<Value> {
        self.inner.clock().await
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        self.inner.positions().await
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        self.inner.orders().await
    }
    async fn asset(&self, s: &str) -> BrokerResult<Value> {
        self.inner.asset(s).await
    }
    async fn by_client(&self, key: &str) -> BrokerResult<Option<Value>> {
        self.inner.by_client(key).await
    }
    async fn cancel(&self, id: Uuid) -> BrokerResult<()> {
        self.inner.cancel(id).await
    }
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value> {
        let response = self.inner.submit(body).await?;
        if self.hold.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        Ok(response)
    }
}
struct HeldMarket {
    inner: Arc<FakeMarket>,
    hold: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait]
impl MarketApi for HeldMarket {
    async fn universe(&self) -> Result<Vec<String>> {
        self.inner.universe().await
    }
    async fn quote(&self, s: &str) -> Result<Quote> {
        self.inner.quote(s).await
    }
    async fn observations(
        &self,
        s: &[String],
        b: &dyn BrokerApi,
        t: DateTime<Utc>,
    ) -> Result<Vec<Observation>> {
        let rows = self.inner.observations(s, b, t).await?;
        if self.hold.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        Ok(rows)
    }
}
// The service fixture must never acquire external data or rank news at 09:30 ET.
struct NoData;
#[async_trait]
impl mainkraft::market::DataApi for NoData {
    async fn bars(
        &self,
        _: &str,
        _: chrono::NaiveDate,
        _: DateTime<Utc>,
        _: chrono::NaiveDate,
    ) -> Result<Vec<Value>> {
        panic!("unexpected service market request")
    }
    async fn latest_quote(&self, _: &str) -> Result<Value> {
        panic!("unexpected service quote request")
    }
}
struct NoRank;
#[async_trait]
impl mainkraft::ranking::Transport for NoRank {
    async fn complete(&self, _: &Value) -> Result<(u16, Vec<u8>)> {
        panic!("unexpected service ranking request")
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires owned disposable paper database; no broker HTTP or credentials"]
async fn graceful_stop_retains_acknowledgement_ownership_and_pause_blocks_late_orders() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").unwrap();
    let url = url::Url::parse(&uri).unwrap();
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_paper_test_rust_")
    );
    let pool = db::pool(&uri, true).await.unwrap();
    db::migrate_paper(&pool).await.unwrap();
    sqlx::query("UPDATE learning_controls SET enabled=true")
        .execute(&pool)
        .await
        .unwrap();
    let clock = Arc::new(TestClock(AtomicI64::new(0)));
    clock.set("2026-11-19T14:30:00Z");
    let price = Arc::new(AtomicI64::new(10000));
    let a = Arc::new(FakeBroker::new("control", clock.clone(), price.clone()));
    let b = Arc::new(FakeBroker::new("adaptive", clock.clone(), price.clone()));
    let market = Arc::new(FakeMarket {
        clock: clock.clone(),
        price,
        prior: AtomicI64::new(5000),
        missing: AtomicBool::new(false),
    });
    let held = Arc::new(HeldBroker {
        inner: a.clone(),
        hold: AtomicBool::new(true),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
    let engine = worker(
        pool.clone(),
        clock.clone(),
        market.clone(),
        held.clone(),
        b.clone(),
    );
    let (stop, receiver) = tokio::sync::watch::channel(false);
    let research = Arc::new(mainkraft::discovery::Research {
        pool: pool.clone(),
        broker: held.clone(),
        data: Arc::new(NoData),
        ranker: Arc::new(mainkraft::ranking::Ranker {
            pool: pool.clone(),
            model: "fixture".into(),
            transport: Arc::new(NoRank),
            clock: clock.clone(),
        }),
        sentiment: None,
        clock: clock.clone(),
        calendar: Arc::new(Calendar::embedded().unwrap()),
        feed: "iex".into(),
    });
    let scheduler = Arc::new(mainkraft::discovery::Scheduler {
        research,
        enabled: true,
        attempted: tokio::sync::Mutex::new(None),
    });
    let router = mainkraft::paper_api::router(mainkraft::paper_api::PaperState {
        pool: pool.clone(),
        token: "fixture-private-token".into(),
        brokers: vec![held.clone(), b.clone()],
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let mut running = tokio::spawn(mainkraft::service::paper(
        engine, scheduler, router, listener, receiver,
    ));
    tokio::time::timeout(std::time::Duration::from_secs(5), held.entered.notified())
        .await
        .expect("broker accepted order, acknowledgement pending");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap();
    let base = format!("http://{address}");
    assert_eq!(
        client
            .get(format!("{base}/health"))
            .send()
            .await
            .unwrap()
            .json::<Value>()
            .await
            .unwrap(),
        json!({"status":"ok","execution":"paper_only"})
    );
    assert_eq!(
        client
            .get(format!("{base}/status"))
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(
        client
            .get(format!("{base}/status"))
            .header("x-pilot-token", "fixture-private-token")
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    stop.send(true).unwrap();
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(200), &mut running)
            .await
            .is_err(),
        "shutdown cancelled the in-flight acknowledgement"
    );
    let mut rival = worker(
        pool.clone(),
        clock.clone(),
        market.clone(),
        a.clone(),
        b.clone(),
    );
    rival.tick().await.unwrap();
    assert_eq!(
        b.calls.load(Ordering::SeqCst),
        0,
        "competing engine acquired a live cycle"
    );
    held.release.notify_one();
    tokio::time::timeout(std::time::Duration::from_secs(5), running)
        .await
        .expect("drained shutdown")
        .unwrap()
        .expect("all three service roles drained");
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        b.calls.load(Ordering::SeqCst),
        0,
        "shutdown started another account submission"
    );
    assert_eq!(db::pending(&pool, "control").await.unwrap().len(), 0);
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM learning_intents")
            .fetch_one(&pool)
            .await
            .unwrap(),
        "filled"
    );
    assert_eq!(count(&pool, "control", "order_version").await, 1);
    let mut restarted = worker(
        pool.clone(),
        clock.clone(),
        market.clone(),
        a.clone(),
        b.clone(),
    );
    restarted.tick().await.unwrap();
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    // Pause while the next allocation is still acquiring its causal inputs.
    clock.set("2026-11-19T14:35:00Z");
    market.prior.store(-5000, Ordering::SeqCst);
    let paused = Arc::new(HeldMarket {
        inner: market.clone(),
        hold: AtomicBool::new(true),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
    let mut engine = worker(
        pool.clone(),
        clock.clone(),
        paused.clone(),
        a.clone(),
        b.clone(),
    );
    let task = tokio::spawn(async move { engine.tick().await });
    tokio::time::timeout(std::time::Duration::from_secs(5), paused.entered.notified())
        .await
        .unwrap();
    sqlx::query("UPDATE learning_controls SET enabled=false")
        .execute(&pool)
        .await
        .unwrap();
    paused.release.notify_one();
    tokio::time::timeout(std::time::Duration::from_secs(5), task)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(
        a.calls.load(Ordering::SeqCst),
        1,
        "pause allowed a late reduction order"
    );
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert!(!a.positions().await.unwrap().is_empty());
    assert!(!b.positions().await.unwrap().is_empty());
    // A closed supervisor channel is also a stop, including at startup.
    sqlx::query("UPDATE learning_controls SET enabled=true")
        .execute(&pool)
        .await
        .unwrap();
    clock.set("2026-11-19T14:40:00Z");
    let (sender, receiver) = tokio::sync::watch::channel(false);
    drop(sender);
    let engine = worker(pool.clone(), clock, market, a.clone(), b.clone());
    tokio::time::timeout(std::time::Duration::from_secs(5), engine.run(receiver))
        .await
        .unwrap();
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    pool.close().await;
}
