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
    market: Arc<FakeMarket>,
    a: Arc<FakeBroker>,
    b: Arc<FakeBroker>,
) -> Engine {
    let brokers: BTreeMap<String, Arc<dyn BrokerApi>> = BTreeMap::from([
        ("control".into(), a as Arc<dyn BrokerApi>),
        ("adaptive".into(), b as Arc<dyn BrokerApi>),
    ]);
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

#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated paper DB"]
async fn two_account_monitor_allocate_fill_exit_restart_and_prequential_learning() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_paper_test_rust_")
    );
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("schema");
    sqlx::query("UPDATE learning_controls SET enabled=true")
        .execute(&pool)
        .await
        .expect("enable fixture");
    let clock = Arc::new(TestClock(AtomicI64::new(0)));
    clock.set("2026-11-19T14:30:00+00:00");
    let price = Arc::new(AtomicI64::new(10000));
    let a = Arc::new(FakeBroker::new("control", clock.clone(), price.clone()));
    let b = Arc::new(FakeBroker::new("adaptive", clock.clone(), price.clone()));
    let market = Arc::new(FakeMarket {
        clock: clock.clone(),
        price: price.clone(),
        prior: AtomicI64::new(5000),
        missing: AtomicBool::new(false),
    });
    let mut first = worker(
        pool.clone(),
        clock.clone(),
        market.clone(),
        a.clone(),
        b.clone(),
    );
    first.tick().await.expect("opening cycle");
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert_eq!(count(&pool, "control", "prediction").await, 1);
    assert_eq!(count(&pool, "adaptive", "prediction").await, 1);
    assert_eq!(count(&pool, "adaptive", "model_update").await, 0);
    assert!(
        a.positions().await.expect("positions")[0]["qty"]
            .as_str()
            .expect("qty")
            .parse::<f64>()
            .expect("numeric")
            > 0.
    );
    first.tick().await.expect("same slot");
    drop(first);
    let mut restarted = worker(
        pool.clone(),
        clock.clone(),
        market.clone(),
        a.clone(),
        b.clone(),
    );
    restarted.tick().await.expect("restart same slot");
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    println!("AUTOMATIC_TWO_ACCOUNT_BUY_FILL_AND_RESTART_DEDUP=PASS");
    clock.set("2026-11-19T14:35:00+00:00");
    market.prior.store(-5000, Ordering::SeqCst);
    restarted.tick().await.expect("direction reversal");
    // The unmodified Python oracle confirms this boundary: closing leaves
    // $1999.20 buying power, while the pre-close target requests $1999.80.
    // Preserve the capacity wait; do not weaken it to force a reversal fill.
    assert_eq!(a.calls.load(Ordering::SeqCst), 2);
    assert_eq!(b.calls.load(Ordering::SeqCst), 2);
    assert!(a.positions().await.expect("flat after close").is_empty());
    assert_eq!(count(&pool, "control", "broker_capacity_wait").await, 1);
    assert_eq!(count(&pool, "adaptive", "broker_capacity_wait").await, 1);
    assert_eq!(count(&pool, "control", "runtime_failure").await, 0);
    clock.set("2026-11-19T14:40:00+00:00");
    restarted
        .tick()
        .await
        .expect("fresh-capacity reversal allocation");
    assert_eq!(a.calls.load(Ordering::SeqCst), 3);
    assert_eq!(b.calls.load(Ordering::SeqCst), 3);
    let position = a.positions().await.expect("positions");
    let qty = position[0]["qty"]
        .as_str()
        .expect("qty")
        .parse::<f64>()
        .expect("numeric");
    assert!(qty < 0. && qty.fract() == 0.);
    assert_eq!(count(&pool, "adaptive", "model_update").await, 0);
    println!("POSITION_MONITORING_CLOSE_BEFORE_SHORT_WITHOUT_FUTURE_LEARNING=PASS");
    clock.set("2026-11-19T15:30:00+00:00");
    price.store(10100, Ordering::SeqCst);
    restarted.tick().await.expect("mature first forecasts");
    assert_eq!(count(&pool, "control", "model_update").await, 0);
    assert_eq!(count(&pool, "adaptive", "model_update").await, 1);
    assert_eq!(count(&pool, "adaptive", "outcome").await, 1);
    let actual:f64=sqlx::query_scalar("SELECT (payload->>'actual_return')::float FROM learning_records WHERE account_id='adaptive' AND kind='outcome'").fetch_one(&pool).await.expect("outcome");
    assert!((actual - 0.01).abs() < 1e-12);
    // A positive midpoint forecast outcome is not the current short position's
    // execution P/L. Keep the two measurements separate in both records.
    let account = b.account().await.expect("account");
    assert!(
        account["equity"]
            .as_str()
            .expect("equity")
            .parse::<f64>()
            .expect("numeric")
            < 1000.
    );
    println!("MATURED_ONLY_RESIDUAL_UPDATE_FIXED_CONTROL_UNCHANGED=PASS");
    println!("FORECAST_RETURN_NOT_EXECUTION_PNL=PASS");
    let before = a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst);
    sqlx::query("UPDATE learning_controls SET enabled=false")
        .execute(&pool)
        .await
        .expect("pause");
    clock.set("2026-11-19T15:31:00+00:00");
    restarted.tick().await.expect("paused");
    assert_eq!(
        a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst),
        before
    );
    sqlx::query("UPDATE learning_controls SET enabled=true")
        .execute(&pool)
        .await
        .expect("enable");
    a.open.store(false, Ordering::SeqCst);
    b.open.store(false, Ordering::SeqCst);
    clock.set("2026-11-19T15:35:00+00:00");
    restarted.tick().await.expect("closed");
    assert_eq!(
        a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst),
        before
    );
    println!("OPERATOR_PAUSE_AND_CLOSED_MARKET=PASS");
    a.open.store(true, Ordering::SeqCst);
    b.open.store(true, Ordering::SeqCst);
    a.blocked.store(true, Ordering::SeqCst);
    clock.set("2026-11-19T15:40:00+00:00");
    restarted.tick().await.expect("isolated blocked account");
    assert!(count(&pool, "control", "runtime_failure").await > 0);
    assert_eq!(count(&pool, "adaptive", "prediction").await, 5);
    println!("BROKER_RESTRICTION_AND_ACCOUNT_FAILURE_ISOLATION=PASS");
    let baseline: f64 = sqlx::query_scalar(
        "SELECT baseline_equity::float FROM learning_accounts WHERE account_id='control'",
    )
    .fetch_one(&pool)
    .await
    .expect("baseline");
    assert_eq!(baseline, 1000.);
    drop(restarted);
    pool.close().await;
}
