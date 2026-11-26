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

struct Server {
    a: Arc<FakeBroker>,
    b: Arc<FakeBroker>,
    token: String,
    hold: AtomicBool,
    hidden: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
fn partial(broker: &FakeBroker, body: &OrderBody) -> Value {
    broker.calls.fetch_add(1, Ordering::SeqCst);
    let mut book = broker.book.lock().unwrap();
    assert!(!book.orders.contains_key(&body.client_order_id));
    let signed = if body.side == "buy" { 0.25 } else { -0.25 };
    let price = broker.midpoint() + if signed > 0. { 0.01 } else { -0.01 };
    book.cash -= signed * price;
    *book.positions.entry(body.symbol.clone()).or_default() += signed;
    let order = json!({"id":Uuid::new_v4(),"client_order_id":body.client_order_id,"symbol":body.symbol,"side":body.side,"status":"partially_filled","qty":body.qty,"filled_qty":"0.25","filled_avg_price":price.to_string()});
    book.orders
        .insert(body.client_order_id.clone(), order.clone());
    order
}
fn complete(broker: &FakeBroker) {
    let mut book = broker.book.lock().unwrap();
    let key = book.orders.keys().next().unwrap().clone();
    let mut order = book.orders[&key].clone();
    let requested = order["qty"].as_str().unwrap().parse::<f64>().unwrap();
    let done = order["filled_qty"]
        .as_str()
        .unwrap()
        .parse::<f64>()
        .unwrap();
    let sign = if order["side"] == "buy" { 1. } else { -1. };
    let price = order["filled_avg_price"]
        .as_str()
        .unwrap()
        .parse::<f64>()
        .unwrap();
    book.cash -= sign * (requested - done) * price;
    *book
        .positions
        .get_mut(order["symbol"].as_str().unwrap())
        .unwrap() += sign * (requested - done);
    order["filled_qty"] = order["qty"].clone();
    order["status"] = json!("filled");
    book.orders.insert(key, order);
}
async fn route(
    axum::extract::State(state): axum::extract::State<Arc<Server>>,
    request: axum::extract::Request,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    if request
        .headers()
        .get("x-fixture-token")
        .and_then(|h| h.to_str().ok())
        != Some(state.token.as_str())
    {
        return axum::http::StatusCode::FORBIDDEN.into_response();
    }
    let path = request.uri().path().to_owned();
    let query = request.uri().query().unwrap_or("").to_owned();
    let method = request.method().clone();
    let mut split = path.trim_start_matches('/').splitn(2, '/');
    let account = split.next().unwrap_or("");
    let tail = format!("/{}", split.next().unwrap_or(""));
    let broker = match account {
        "control" => state.a.clone(),
        "adaptive" => state.b.clone(),
        _ => return axum::http::StatusCode::NOT_FOUND.into_response(),
    };
    let result: BrokerResult<Value> = match (method.as_str(), tail.as_str()) {
        ("GET", "/v2/account") => broker.account().await,
        ("GET", "/v2/clock") => broker.clock().await,
        ("GET", "/v2/positions") => broker.positions().await.map(|v| json!(v)),
        ("GET", "/v2/orders") => Ok(
            if account == "control" && state.hidden.load(Ordering::SeqCst) {
                json!([])
            } else {
                json!(
                    broker
                        .book
                        .lock()
                        .unwrap()
                        .orders
                        .values()
                        .filter(|row| row["status"] != "filled")
                        .cloned()
                        .collect::<Vec<_>>()
                )
            },
        ),
        ("GET", "/v2/orders:by_client_order_id") => {
            if account == "control" && state.hidden.load(Ordering::SeqCst) {
                return axum::http::StatusCode::NOT_FOUND.into_response();
            }
            let key = url::form_urlencoded::parse(query.as_bytes())
                .find(|(k, _)| k == "client_order_id")
                .map(|(_, v)| v.into_owned())
                .unwrap_or_default();
            match broker.by_client(&key).await {
                Ok(Some(row)) => Ok(row),
                Ok(None) => return axum::http::StatusCode::NOT_FOUND.into_response(),
                Err(e) => Err(e),
            }
        }
        ("POST", "/v2/orders") => {
            let body: OrderBody = serde_json::from_slice(
                &axum::body::to_bytes(request.into_body(), 65536)
                    .await
                    .unwrap(),
            )
            .unwrap();
            assert!(body.client_order_id.starts_with("mkx-"));
            if account == "control" && state.hold.swap(false, Ordering::SeqCst) {
                let order = partial(&broker, &body);
                state.entered.notify_one();
                state.release.notified().await;
                Ok(order)
            } else {
                broker.submit(&body).await
            }
        }
        ("GET", p) if p.starts_with("/v2/assets/") => {
            broker.asset(p.trim_start_matches("/v2/assets/")).await
        }
        _ => return axum::http::StatusCode::NOT_FOUND.into_response(),
    };
    match result {
        Ok(value) => axum::Json(value).into_response(),
        Err(_) => axum::http::StatusCode::UNPROCESSABLE_ENTITY.into_response(),
    }
}
struct Remote {
    base: String,
    id: String,
    token: String,
    client: reqwest::Client,
}
impl Remote {
    fn new(base: String, id: &str, token: String) -> Self {
        let u = url::Url::parse(&base).unwrap();
        assert_eq!(u.host_str(), Some("127.0.0.1"));
        assert_eq!(u.scheme(), "http");
        Self {
            base,
            id: id.into(),
            token,
            client: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(5))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
        }
    }
    async fn call(
        &self,
        method: reqwest::Method,
        path: &str,
        body: Option<&OrderBody>,
        query: &[(&str, &str)],
    ) -> BrokerResult<Option<Value>> {
        let mut request = self
            .client
            .request(method, format!("{}/{}{}", self.base, self.id, path))
            .header("x-fixture-token", &self.token)
            .query(query);
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request.send().await.map_err(|_| BrokerError::Unknown)?;
        if response.status() == 404 && path == "/v2/orders:by_client_order_id" {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(BrokerError::Unavailable);
        }
        Ok(Some(
            response.json().await.map_err(|_| BrokerError::Unknown)?,
        ))
    }
    async fn get(&self, path: &str) -> BrokerResult<Value> {
        self.call(reqwest::Method::GET, path, None, &[])
            .await?
            .ok_or(BrokerError::Unavailable)
    }
}
#[async_trait]
impl BrokerApi for Remote {
    async fn account(&self) -> BrokerResult<Value> {
        self.get("/v2/account").await
    }
    async fn clock(&self) -> BrokerResult<Value> {
        self.get("/v2/clock").await
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        serde_json::from_value(self.get("/v2/positions").await?).map_err(|_| BrokerError::Invalid)
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        serde_json::from_value(self.get("/v2/orders").await?).map_err(|_| BrokerError::Invalid)
    }
    async fn asset(&self, symbol: &str) -> BrokerResult<Value> {
        self.get(&format!("/v2/assets/{symbol}")).await
    }
    async fn by_client(&self, key: &str) -> BrokerResult<Option<Value>> {
        self.call(
            reqwest::Method::GET,
            "/v2/orders:by_client_order_id",
            None,
            &[("client_order_id", key)],
        )
        .await
    }
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value> {
        self.call(reqwest::Method::POST, "/v2/orders", Some(body), &[])
            .await?
            .ok_or(BrokerError::Unknown)
    }
    async fn cancel(&self, _: Uuid) -> BrokerResult<()> {
        panic!("fixture engine must not cancel broker orders")
    }
}
struct Child(Option<std::process::Child>);
impl Drop for Child {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
fn spawn(uri: &str, origin: &str, token: &str) -> Child {
    Child(Some(
        std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "engine_fixture_child",
                "--ignored",
                "--nocapture",
            ])
            .env("RUST_ENGINE_FIXTURE_CHILD", "1")
            .env("RUST_TEST_DATABASE_URL", uri)
            .env("RUST_ENGINE_FIXTURE_ORIGIN", origin)
            .env("RUST_ENGINE_FIXTURE_TOKEN", token)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::inherit())
            .spawn()
            .unwrap(),
    ))
}
async fn finish(child: &mut Child) {
    tokio::time::timeout(std::time::Duration::from_secs(10), async {
        loop {
            if let Some(status) = child.0.as_mut().unwrap().try_wait().unwrap() {
                assert!(status.success(), "owned fixture child failed");
                child.0.take();
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("owned engine child completion");
}
#[tokio::test]
#[ignore = "child only; invoked explicitly by owned parent test"]
async fn engine_fixture_child() {
    assert_eq!(std::env::var("RUST_ENGINE_FIXTURE_CHILD").unwrap(), "1");
    let uri = std::env::var("RUST_TEST_DATABASE_URL").unwrap();
    let u = url::Url::parse(&uri).unwrap();
    assert!(
        matches!(u.host_str(), Some("127.0.0.1" | "localhost"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_paper_test_rust_")
    );
    let pool = db::pool(&uri, true).await.unwrap();
    let clock = Arc::new(TestClock(AtomicI64::new(0)));
    clock.set("2026-11-19T14:30:00Z");
    let market = Arc::new(FakeMarket {
        clock: clock.clone(),
        price: Arc::new(AtomicI64::new(10000)),
        prior: AtomicI64::new(5000),
        missing: AtomicBool::new(false),
    });
    let origin = std::env::var("RUST_ENGINE_FIXTURE_ORIGIN").unwrap();
    let token = std::env::var("RUST_ENGINE_FIXTURE_TOKEN").unwrap();
    let a = Arc::new(Remote::new(origin.clone(), "control", token.clone()));
    let b = Arc::new(Remote::new(origin, "adaptive", token));
    let mut engine = worker(pool.clone(), clock, market, a, b);
    engine.tick().await.unwrap();
    pool.close().await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "owned local PostgreSQL and loopback broker; no Alpaca access"]
async fn engine_process_death_recovery() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").unwrap();
    let u = url::Url::parse(&uri).unwrap();
    assert!(
        matches!(u.host_str(), Some("127.0.0.1" | "localhost"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_paper_test_rust_")
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
    let b = Arc::new(FakeBroker::new("adaptive", clock, price));
    let state = Arc::new(Server {
        a: a.clone(),
        b: b.clone(),
        token: Uuid::new_v4().to_string(),
        hold: AtomicBool::new(true),
        hidden: AtomicBool::new(true),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let router = axum::Router::new()
        .fallback(route)
        .with_state(state.clone());
    let server = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let mut first = spawn(&uri, &origin, &state.token);
    tokio::time::timeout(std::time::Duration::from_secs(10), state.entered.notified())
        .await
        .expect("broker accepted partial fill before acknowledgement");
    let mut rival = spawn(&uri, &origin, &state.token);
    finish(&mut rival).await;
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        b.calls.load(Ordering::SeqCst),
        0,
        "simultaneous engine acquired the live process lease"
    );
    let mut owned = first.0.take().unwrap();
    owned.kill().unwrap();
    owned.wait().unwrap();
    state.release.notify_one();
    tokio::time::timeout(std::time::Duration::from_secs(10), async {
        loop {
            if let Some(lease) = db::CycleLease::acquire(&pool).await.unwrap() {
                lease.finish().await.unwrap();
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("process death released session lease");
    let mut restarted = spawn(&uri, &origin, &state.token);
    finish(&mut restarted).await;
    assert_eq!(
        a.calls.load(Ordering::SeqCst),
        1,
        "unknown acknowledgement was resubmitted"
    );
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert_eq!(db::pending(&pool, "control").await.unwrap().len(), 1);
    assert_eq!(a.positions().await.unwrap()[0]["qty"], "0.25");
    state.hidden.store(false, Ordering::SeqCst);
    let mut restarted = spawn(&uri, &origin, &state.token);
    finish(&mut restarted).await;
    let payload: Value =
        sqlx::query_scalar("SELECT order_payload FROM learning_intents WHERE account_id='control'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(payload["filled_qty"], "0.25");
    assert_eq!(count(&pool, "control", "order_version").await, 1);
    complete(&a);
    let mut restarted = spawn(&uri, &origin, &state.token);
    finish(&mut restarted).await;
    assert!(db::pending(&pool, "control").await.unwrap().is_empty());
    assert_eq!(a.calls.load(Ordering::SeqCst), 1);
    assert_eq!(b.calls.load(Ordering::SeqCst), 1);
    assert_eq!(count(&pool, "control", "order_version").await, 2);
    assert_eq!(count(&pool, "control", "runtime_failure").await, 0);
    server.abort();
    let _ = server.await;
    pool.close().await;
}
