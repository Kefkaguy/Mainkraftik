#![cfg(feature = "sentiment-inference")]
use async_trait::async_trait;
use chrono::{DateTime, Duration, NaiveDate, TimeZone, Utc};
use mainkraft::{
    Result,
    broker::{BrokerApi, BrokerResult, OrderBody},
    calendar::Calendar,
    db,
    discovery::{Research, Scheduler},
    engine::Clock,
    market::DataApi,
    ranking,
};
use serde_json::{Value, json};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicI64, AtomicUsize, Ordering},
};
use tokio::sync::Mutex as AsyncMutex;
use uuid::Uuid;

struct TestClock(AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> DateTime<Utc> {
        Utc.timestamp_opt(self.0.load(Ordering::SeqCst), 0)
            .single()
            .expect("time")
    }
}
struct Catalog(Value);
#[async_trait]
impl BrokerApi for Catalog {
    async fn active_equities(&self) -> BrokerResult<Vec<Value>> {
        Ok(self.0.as_array().expect("catalog").clone())
    }
    async fn account(&self) -> BrokerResult<Value> {
        panic!("discovery has no account authority")
    }
    async fn clock(&self) -> BrokerResult<Value> {
        panic!("not used")
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        panic!("not used")
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        panic!("not used")
    }
    async fn asset(&self, _: &str) -> BrokerResult<Value> {
        panic!("not used")
    }
    async fn by_client(&self, _: &str) -> BrokerResult<Option<Value>> {
        panic!("not used")
    }
    async fn submit(&self, _: &OrderBody) -> BrokerResult<Value> {
        panic!("research cannot submit")
    }
    async fn cancel(&self, _: Uuid) -> BrokerResult<()> {
        panic!("research cannot cancel")
    }
}
struct Data {
    fixture: Value,
    queries: Mutex<Vec<Value>>,
    news: AtomicUsize,
    missing_spy: bool,
}
#[async_trait]
impl DataApi for Data {
    async fn bars(
        &self,
        symbol: &str,
        start: NaiveDate,
        end: DateTime<Utc>,
        asof: NaiveDate,
    ) -> Result<Vec<Value>> {
        self.queries.lock().expect("queries").push(json!({"symbol":symbol,"start":start.to_string(),"end":end.to_rfc3339(),"asof":asof.to_string()}));
        if self.missing_spy && symbol == "SPY" {
            return Ok(vec![]);
        }
        Ok(self.fixture["raw"][symbol]
            .as_array()
            .expect("bars")
            .clone())
    }
    async fn latest_quote(&self, _: &str) -> Result<Value> {
        panic!("research never executes quotes")
    }
    async fn news_page(
        &self,
        _: DateTime<Utc>,
        _: DateTime<Utc>,
        _: Option<&str>,
    ) -> Result<Value> {
        self.news.fetch_add(1, Ordering::SeqCst);
        Ok(self.fixture["news"].clone())
    }
}
struct RankTransport {
    fixture: Value,
    requests: Mutex<Vec<Value>>,
    clock: Arc<TestClock>,
    advance: AtomicI64,
}
#[async_trait]
impl ranking::Transport for RankTransport {
    async fn complete(&self, body: &Value) -> Result<(u16, Vec<u8>)> {
        self.requests.lock().expect("requests").push(body.clone());
        let advance = self.advance.load(Ordering::SeqCst);
        if advance != 0 {
            self.clock.0.fetch_add(advance, Ordering::SeqCst);
        }
        Ok((200, serde_json::to_vec(&self.fixture["completion"])?))
    }
}

struct LocalSentiment {
    worker: mainkraft::sentiment_worker::Worker,
    calls: Mutex<Vec<Value>>,
}
#[async_trait]
impl mainkraft::sentiment::Transport for LocalSentiment {
    async fn score(&self, items: &[Value]) -> Result<Value> {
        let response = mainkraft::sentiment::Transport::score(&self.worker, items).await?;
        self.calls
            .lock()
            .unwrap()
            .push(json!({"items":items,"response":response}));
        Ok(response)
    }
}
#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated paper DB"]
async fn real_frozen_sentiment_and_premarket_assembly_match_original() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(matches!(url.host_str(), Some("localhost" | "127.0.0.1")));
    assert_eq!(url.port(), Some(5543));
    assert!(url.path().starts_with("/intelligence_paper_test_rust_"));
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("migrate");
    let fixture: Value = serde_json::from_str(if cfg!(target_os = "linux") {
        include_str!("fixtures/research-sentiment-parity-linux.json")
    } else {
        include_str!("fixtures/research-sentiment-parity.json")
    })
    .expect("fixture");
    let instant = fixture["instant"]
        .as_str()
        .expect("instant")
        .parse::<DateTime<Utc>>()
        .expect("time");
    let clock = Arc::new(TestClock(AtomicI64::new(instant.timestamp())));
    let data = Arc::new(Data {
        fixture: fixture.clone(),
        queries: Mutex::new(vec![]),
        news: AtomicUsize::new(0),
        missing_spy: false,
    });
    let transport = Arc::new(RankTransport {
        fixture: fixture.clone(),
        requests: Mutex::new(vec![]),
        clock: clock.clone(),
        advance: AtomicI64::new(0),
    });
    let model = std::path::PathBuf::from(std::env::var("RUST_SENTIMENT_MODEL_DIR").expect("model"));
    let library =
        std::path::PathBuf::from(std::env::var("RUST_SENTIMENT_LIBRARY").expect("library"));
    use sha2::{Digest, Sha256};
    assert_eq!(
        format!("{:x}", Sha256::digest(std::fs::read(&library).unwrap())),
        fixture["runtime_library_sha256"]
    );
    let local_sentiment = Arc::new(LocalSentiment {
        worker: mainkraft::sentiment_worker::Worker::new(
            mainkraft::sentiment_worker::Inference::load(&model, &library)
                .expect("frozen inference"),
        ),
        calls: Mutex::new(Vec::new()),
    });
    let research = Arc::new(Research {
        pool: pool.clone(),
        broker: Arc::new(Catalog(fixture["catalog"].clone())),
        data: data.clone(),
        ranker: Arc::new(ranking::Ranker {
            pool: pool.clone(),
            model: "fixture-model".into(),
            transport: transport.clone(),
            clock: clock.clone(),
        }),
        sentiment: Some(local_sentiment.clone()),
        clock: clock.clone(),
        calendar: Arc::new(Calendar::embedded().expect("calendar")),
        feed: "sip".into(),
    });
    let result = research.research(None, false).await.expect("research");
    if let Ok(path) = std::env::var("RUST_SENTIMENT_ASSEMBLY_OUTPUT") {
        let sources:Vec<Value>=sqlx::query_scalar("SELECT jsonb_build_object('id',id,'provider',provider,'symbol',symbol,'payload',payload) FROM pilot_sources ORDER BY provider,symbol,id").fetch_all(&pool).await.unwrap();
        let report: Option<Value> = sqlx::query_scalar(
            "SELECT payload FROM pilot_research_runs ORDER BY completed_at DESC LIMIT 1",
        )
        .fetch_optional(&pool)
        .await
        .unwrap();
        let trace = json!({"result":result,"sentiment_calls":*local_sentiment.calls.lock().unwrap(),"ranking_requests":*transport.requests.lock().unwrap(),"sources":sources,"research_payload":report});
        std::fs::write(path, serde_json::to_vec_pretty(&trace).unwrap()).unwrap();
    }
    for (actual, expected) in local_sentiment
        .calls
        .lock()
        .unwrap()
        .iter()
        .zip(fixture["sentiment_calls"].as_array().unwrap())
    {
        assert_eq!(actual["items"], expected["items"]);
        println!(
            "REAL_NATIVE_SENTIMENT_SCORE_BYTES_EXACT={}",
            actual["response"] == expected["expected"]
        );
    }
    assert_eq!(result, fixture["expected"]["result"]);
    assert_eq!(
        json!(*transport.requests.lock().expect("requests")),
        fixture["expected"]["ranking_requests"]
    );
    assert_eq!(
        json!(*data.queries.lock().expect("queries")),
        fixture["expected"]["bar_queries"]
    );
    let payload: Value = sqlx::query_scalar("SELECT payload FROM pilot_research_runs WHERE id=$1")
        .bind(result["research_id"].as_str().expect("id"))
        .fetch_one(&pool)
        .await
        .expect("research row");
    assert_eq!(payload, fixture["expected"]["inserts"][0]["values"][4]);
    let symbols: Value = sqlx::query_scalar("SELECT symbols FROM pilot_selections")
        .fetch_one(&pool)
        .await
        .expect("selection");
    assert_eq!(symbols, fixture["expected"]["inserts"][1]["values"][3]);
    for source in fixture["expected"]["sources"].as_array().expect("sources") {
        let payload: Value = sqlx::query_scalar("SELECT payload FROM pilot_sources WHERE id=$1")
            .bind(source["id"].as_str().expect("id"))
            .fetch_one(&pool)
            .await
            .expect("source");
        assert_eq!(payload, source["payload"]);
    }
    let events: Vec<(String, Value)> =
        sqlx::query_as("SELECT kind,payload FROM pilot_events ORDER BY id")
            .fetch_all(&pool)
            .await
            .expect("events");
    assert_eq!(json!(events), fixture["expected"]["events"]);
    assert_eq!(
        research.research(None, false).await.expect("restart"),
        json!({"status":"already_frozen"})
    );
    assert_eq!(data.news.load(Ordering::SeqCst), 1);
    assert!(
        sqlx::query("UPDATE pilot_selections SET symbols='[]'")
            .execute(&pool)
            .await
            .is_err()
    );
    // Scheduler reuses a frozen session and leaves research/forecast records unchanged.
    let scheduler = Scheduler {
        research: research.clone(),
        enabled: true,
        attempted: AsyncMutex::new(None),
    };
    scheduler.tick().await.expect("scheduler");
    assert_eq!(data.news.load(Ordering::SeqCst), 1);
    // No backdating, no early or post-cutoff issue, including non-session weekends.
    assert_eq!(
        research
            .research(Some(instant.date_naive() - Duration::days(1)), false)
            .await
            .expect("no backdate")["status"],
        "skipped"
    );
    for stamp in [
        "2026-11-19T12:29:59+00:00",
        "2026-11-19T14:00:00+00:00",
        "2026-11-21T13:00:00+00:00",
    ] {
        clock.0.store(
            stamp.parse::<DateTime<Utc>>().expect("time").timestamp(),
            Ordering::SeqCst,
        );
        assert_eq!(
            research.research(None, false).await.expect("window")["status"],
            "skipped"
        );
    }
    // Preview is explicitly separate and never replaces the frozen scheduled selection.
    clock.0.store(instant.timestamp(), Ordering::SeqCst);
    assert_eq!(
        research.research(None, true).await.expect("preview")["status"],
        "preview"
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_selections")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(count, 1);
    // A run that starts on time but finishes at 09:00 is retained as missed cutoff.
    clock
        .0
        .store((instant + Duration::days(1)).timestamp(), Ordering::SeqCst);
    transport.advance.store(3600, Ordering::SeqCst);
    assert_eq!(
        research
            .research(None, false)
            .await
            .expect("late completion")["status"],
        "missed_cutoff"
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_selections")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(count, 1);
    // Required benchmark failure rolls back research+selection together, retaining prior evidence.
    clock
        .0
        .store((instant + Duration::days(4)).timestamp(), Ordering::SeqCst);
    transport.advance.store(0, Ordering::SeqCst);
    let missing = Research {
        pool: pool.clone(),
        broker: research.broker.clone(),
        data: Arc::new(Data {
            fixture: fixture.clone(),
            queries: Mutex::new(vec![]),
            news: AtomicUsize::new(0),
            missing_spy: true,
        }),
        ranker: research.ranker.clone(),
        sentiment: None,
        clock: clock.clone(),
        calendar: research.calendar.clone(),
        feed: "sip".into(),
    };
    assert!(
        missing
            .research(None, false)
            .await
            .expect_err("missing benchmark")
            .to_string()
            .contains("Benchmark history unavailable")
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_research_runs")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(count, 3);
    let orders: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_intents")
        .fetch_one(&pool)
        .await
        .expect("no orders");
    assert_eq!(orders, 0);
    let failed = Scheduler {
        research: Arc::new(missing),
        enabled: true,
        attempted: AsyncMutex::new(None),
    };
    failed.tick().await.expect("failure recorded");
    failed.tick().await.expect("retry throttled");
    let failures: i64 =
        sqlx::query_scalar("SELECT count(*) FROM learning_records WHERE kind='research_failure'")
            .fetch_one(&pool)
            .await
            .expect("failures");
    assert_eq!(failures, 1);
    clock.0.fetch_add(20 * 60, Ordering::SeqCst);
    failed.tick().await.expect("twenty-minute retry");
    let failures: i64 =
        sqlx::query_scalar("SELECT count(*) FROM learning_records WHERE kind='research_failure'")
            .fetch_one(&pool)
            .await
            .expect("failures");
    assert_eq!(failures, 2);
    let (sender, receiver) = tokio::sync::watch::channel(false);
    drop(sender);
    tokio::time::timeout(std::time::Duration::from_secs(1), failed.run(receiver))
        .await
        .expect("closed stop channel must not spin")
        .expect("shutdown");
    println!("FULL_PYTHON_RESEARCH_SOURCE_EVENT_SELECTION_ID_PARITY=PASS");
    println!("FROZEN_RESTART_NO_BACKDATING_PREVIEW_SEPARATION=PASS");
    println!("MISSED_CUTOFF_RETAINED_NOT_FROZEN=PASS");
    println!("BENCHMARK_FAILURE_ATOMIC_ROLLBACK_NO_ORDER_AUTHORITY=PASS");
    println!("RESEARCH_FAILURE_RETRY_SPACING_AND_CLOSED_CHANNEL_SHUTDOWN=PASS");
    pool.close().await;
}
