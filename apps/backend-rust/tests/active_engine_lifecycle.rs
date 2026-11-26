//! Full worker loop with two in-memory accounts and an isolated PostgreSQL DB.
//! No HTTP client, Alpaca credentials, or additional brokerage accounts exist.
use async_trait::async_trait;
use chrono::{DateTime, TimeZone, Utc};
use mainkraft::{
    Result,
    broker::{BrokerApi, BrokerError, BrokerResult, OrderBody},
    calendar::Calendar,
    db,
    engine::{AccountPolicy, Clock, Engine, MarketApi, stamp},
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

struct CausalData {
    fixture: Value,
    clock: Arc<TestClock>,
    price: Arc<AtomicI64>,
    valid_bars: AtomicUsize,
    stale: AtomicBool,
}
#[async_trait]
impl mainkraft::market::DataApi for CausalData {
    async fn bars(
        &self,
        symbol: &str,
        _: chrono::NaiveDate,
        _: DateTime<Utc>,
        _: chrono::NaiveDate,
    ) -> Result<Vec<Value>> {
        let Some(rows) = self.fixture["raw_bars"][symbol].as_array() else {
            return Err(mainkraft::Error::Invalid(
                "Fixture history unavailable".into(),
            ));
        };
        self.valid_bars.fetch_add(1, Ordering::SeqCst);
        Ok(rows.clone())
    }
    async fn latest_quote(&self, symbol: &str) -> Result<Value> {
        let (bid, ask) = if symbol == "AMD" {
            let mid = self.price.load(Ordering::SeqCst) as f64 / 100.;
            (mid - 0.01, mid + 0.01)
        } else {
            let quote = &self.fixture["quotes"][symbol];
            (
                quote["bp"]
                    .as_f64()
                    .ok_or_else(|| mainkraft::Error::Invalid("Fixture quote unavailable".into()))?,
                quote["ap"].as_f64().unwrap(),
            )
        };
        let instant = self.clock.now()
            - chrono::Duration::seconds(if self.stale.load(Ordering::SeqCst) {
                16
            } else {
                0
            });
        Ok(json!({"bp":bid,"ap":ask,"t":stamp(instant)}))
    }
}
fn actual_market(
    pool: PgPool,
    data: Arc<CausalData>,
    clock: Arc<TestClock>,
    bundle: Arc<mainkraft::market::Bundle>,
) -> Arc<mainkraft::market::Market> {
    Arc::new(mainkraft::market::Market::new(
        pool,
        data,
        clock,
        Arc::new(Calendar::embedded().unwrap()),
        bundle,
        "sip".into(),
    ))
}
#[tokio::test]
#[ignore = "isolated real-model/market/engine chain; only two synthetic broker books"]
async fn deployed_frozen_bundle_full_market_engine_and_matured_learning_recover() {
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
    let reference: Value = serde_json::from_str(if cfg!(target_os = "linux") {
        include_str!("fixtures/active-engine-parity-linux.json")
    } else {
        include_str!("fixtures/active-engine-parity.json")
    })
    .unwrap();
    let bytes = include_bytes!("fixtures/deployed-paper-bundle.json");
    use sha2::{Digest, Sha256};
    assert_eq!(
        format!("{:x}", Sha256::digest(bytes)),
        reference["bundle_sha256"]
    );
    let bundle = Arc::new(mainkraft::market::Bundle::from_json(bytes).unwrap());
    assert_eq!(bundle.id, reference["bundle_id"]);
    let instant: DateTime<Utc> = reference["instant"].as_str().unwrap().parse().unwrap();
    let clock = Arc::new(TestClock(AtomicI64::new(instant.timestamp())));
    let price = Arc::new(AtomicI64::new(
        (reference["initial_midpoint"].as_f64().unwrap() * 100.).round() as i64,
    ));
    let data = Arc::new(CausalData {
        fixture: reference.clone(),
        clock: clock.clone(),
        price: price.clone(),
        valid_bars: AtomicUsize::new(0),
        stale: AtomicBool::new(false),
    });
    let a = Arc::new(FakeBroker::new("control", clock.clone(), price.clone()));
    let b = Arc::new(FakeBroker::new("adaptive", clock.clone(), price.clone()));
    let market = actual_market(pool.clone(), data.clone(), clock.clone(), bundle.clone());
    let observed = market
        .observations(&["AMD".into()], a.as_ref(), instant)
        .await
        .unwrap();
    assert_eq!(observed.len(), 1);
    let row = &observed[0];
    let expected = &reference["expected_observation"];
    assert_eq!(row.source_id, expected["source_id"]);
    for (actual, expected) in row.x.iter().zip(expected["x"].as_array().unwrap()) {
        assert!((actual - expected.as_f64().unwrap()).abs() < 1e-12);
    }
    assert!((row.prior_return - expected["prior_return"].as_f64().unwrap()).abs() < 1e-12);
    let mut engine = worker(pool.clone(), clock.clone(), market, a.clone(), b.clone());
    engine.tick().await.unwrap();
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert_eq!(count(&pool, "control", "runtime_failure").await, 0);
    assert_eq!(count(&pool, "adaptive", "runtime_failure").await, 0);
    assert_eq!(count(&pool, "adaptive", "model_update").await, 0);
    assert_eq!(count(&pool, "adaptive", "outcome").await, 0);
    let skipped: i64 = sqlx::query_scalar(
        "SELECT count(DISTINCT payload->>'symbol') FROM learning_records WHERE kind='data_failure'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        skipped, 5,
        "unavailable universe members were silently lost"
    );
    let prediction: (String, Value) = sqlx::query_as(
        "SELECT id,payload FROM learning_records WHERE account_id='adaptive' AND kind='prediction'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(
        prediction.1["prior_return"].as_f64().unwrap().abs()
            < prediction.1["round_trip_cost_allowance"].as_f64().unwrap()
    );
    assert_ne!(
        prediction.1["target_value"],
        json!(0.),
        "immutable legacy allocation baseline was silently changed"
    );
    drop(engine);
    let market = actual_market(pool.clone(), data.clone(), clock.clone(), bundle.clone());
    let mut restarted = worker(pool.clone(), clock.clone(), market, a.clone(), b.clone());
    restarted.tick().await.unwrap();
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        data.valid_bars.load(Ordering::SeqCst),
        6,
        "restart did not reload the three required daily series once"
    );
    clock.0.store(
        (instant + chrono::Duration::hours(1)).timestamp(),
        Ordering::SeqCst,
    );
    price.fetch_add(100, Ordering::SeqCst);
    restarted.tick().await.unwrap();
    assert_eq!(count(&pool, "adaptive", "outcome").await, 1);
    assert_eq!(count(&pool, "control", "outcome").await, 1);
    assert_eq!(count(&pool, "control", "model_update").await, 0);
    assert_eq!(count(&pool, "adaptive", "model_update").await, 1);
    let update: Value = sqlx::query_scalar(
        "SELECT payload FROM learning_records WHERE account_id='adaptive' AND kind='model_update'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(update["observations"], 1);
    assert_eq!(
        update["outcome_identity"],
        mainkraft::canonical::digest(&json!([prediction.0])).unwrap()
    );
    for (actual, expected) in update["coefficients"]
        .as_array()
        .unwrap()
        .iter()
        .zip(reference["expected_beta"].as_array().unwrap())
    {
        assert!(
            (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() < 1e-10,
            "original residual coefficients differ"
        );
    }
    let actual:f64=sqlx::query_scalar("SELECT (payload->>'actual_return')::float FROM learning_records WHERE account_id='adaptive' AND kind='outcome'").fetch_one(&pool).await.unwrap();
    assert!((actual - reference["expected_actual_return"].as_f64().unwrap()).abs() < 1e-12);
    assert!(
        a.account().await.unwrap()["equity"]
            .as_str()
            .unwrap()
            .parse::<f64>()
            .unwrap()
            < 1000.,
        "forecast midpoint gain was incorrectly treated as short execution profit"
    );
    let orders = a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst);
    drop(restarted);
    let market = actual_market(pool.clone(), data.clone(), clock.clone(), bundle);
    let mut restarted = worker(pool.clone(), clock.clone(), market, a.clone(), b.clone());
    restarted.tick().await.unwrap();
    assert_eq!(
        a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst),
        orders
    );
    assert_eq!(count(&pool, "adaptive", "model_update").await, 1);
    data.stale.store(true, Ordering::SeqCst);
    clock.0.store(
        (instant + chrono::Duration::minutes(65)).timestamp(),
        Ordering::SeqCst,
    );
    let predictions = count(&pool, "adaptive", "prediction").await;
    restarted.tick().await.unwrap();
    assert_eq!(count(&pool, "adaptive", "prediction").await, predictions);
    assert_eq!(
        a.calls.load(Ordering::SeqCst) + b.calls.load(Ordering::SeqCst),
        orders
    );
    assert_eq!(count(&pool, "adaptive", "model_update").await, 1);
    assert_eq!(count(&pool, "adaptive", "runtime_failure").await, 1);
    pool.close().await;
}
