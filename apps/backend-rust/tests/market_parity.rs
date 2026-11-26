use async_trait::async_trait;
use chrono::{DateTime, NaiveDate, TimeZone, Utc};
use mainkraft::{
    Result,
    broker::{BrokerApi, BrokerResult, OrderBody},
    calendar::Calendar,
    db,
    engine::{Clock, MarketApi},
    market::{self, Bundle, DataApi, Market},
};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicI64, AtomicUsize, Ordering},
};
use uuid::Uuid;

struct TestClock(AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> DateTime<Utc> {
        Utc.timestamp_opt(self.0.load(Ordering::SeqCst), 0)
            .single()
            .expect("time")
    }
}
struct FakeData {
    fixture: Value,
    bar_calls: AtomicUsize,
}
#[async_trait]
impl DataApi for FakeData {
    async fn bars(
        &self,
        symbol: &str,
        _: NaiveDate,
        _: DateTime<Utc>,
        _: NaiveDate,
    ) -> Result<Vec<Value>> {
        self.bar_calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.fixture["raw_bars"][symbol]
            .as_array()
            .expect("raw bars")
            .clone())
    }
    async fn latest_quote(&self, symbol: &str) -> Result<Value> {
        Ok(self.fixture["quotes"][symbol].clone())
    }
}
struct FakeAsset(Value);
#[async_trait]
impl BrokerApi for FakeAsset {
    async fn account(&self) -> BrokerResult<Value> {
        Ok(json!({}))
    }
    async fn clock(&self) -> BrokerResult<Value> {
        Ok(json!({}))
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        Ok(vec![])
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        Ok(vec![])
    }
    async fn asset(&self, _: &str) -> BrokerResult<Value> {
        Ok(self.0.clone())
    }
    async fn by_client(&self, _: &str) -> BrokerResult<Option<Value>> {
        Ok(None)
    }
    async fn submit(&self, _: &OrderBody) -> BrokerResult<Value> {
        panic!("market parity must never submit")
    }
    async fn cancel(&self, _: Uuid) -> BrokerResult<()> {
        panic!("market parity must never cancel")
    }
}

#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated paper DB"]
async fn daily_ingestion_frozen_inference_quote_and_source_identity_match_python() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_paper_test_rust_")
    );
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("schema");
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/market-parity.json")).expect("oracle");
    let numeric: Value =
        serde_json::from_str(include_str!("fixtures/python-parity.json")).expect("model oracle");
    let canonical = numeric["canonical"]
        .as_array()
        .expect("canonical")
        .last()
        .expect("frozen bundle");
    let mut artifact = canonical["value"].clone();
    artifact["bundle_id"] = canonical["expected_digest"].clone();
    let bundle = Arc::new(
        Bundle::from_json(&serde_json::to_vec(&artifact).expect("JSON")).expect("integrity"),
    );
    let instant: DateTime<Utc> = fixture["instant"]
        .as_str()
        .expect("time")
        .parse()
        .expect("timestamp");
    let clock = Arc::new(TestClock(AtomicI64::new(instant.timestamp())));
    let calendar = Arc::new(Calendar::embedded().expect("calendar"));
    let data = Arc::new(FakeData {
        fixture: fixture.clone(),
        bar_calls: AtomicUsize::new(0),
    });
    let market = Market::new(
        pool.clone(),
        data.clone(),
        clock.clone(),
        calendar.clone(),
        bundle,
        "sip".into(),
    );
    let asset = FakeAsset(fixture["asset"].clone());
    let rows = market
        .observations(&["AMD".into()], &asset, instant)
        .await
        .expect("observations");
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    let expected = &fixture["expected"][0];
    assert_eq!(
        row.source_id,
        expected["source_id"].as_str().expect("source ID")
    );
    for (actual, expected) in row.x.iter().zip(expected["x"].as_array().expect("x")) {
        assert!((actual - expected.as_f64().expect("number")).abs() < 1e-12);
    }
    assert!((row.prior_return - expected["prior_return"].as_f64().expect("prior")).abs() < 1e-12);
    assert!(
        (row.uncertainty - expected["uncertainty"].as_f64().expect("uncertainty")).abs() < 1e-12
    );
    assert_eq!(data.bar_calls.load(Ordering::SeqCst), 3);
    market
        .observations(&["AMD".into()], &asset, instant)
        .await
        .expect("cached daily inputs");
    assert_eq!(data.bar_calls.load(Ordering::SeqCst), 3);
    println!("NATIVE_DATA_FEATURE_MODEL_QUOTE_SOURCE_PARITY_AND_CACHE=PASS");
    let normalized = market::normalize_bars(
        &calendar,
        "AMD",
        fixture["raw_bars"]["AMD"].as_array().expect("bars"),
        "sip",
        instant.date_naive(),
        instant,
    )
    .expect("normalization");
    assert_eq!(
        serde_json::to_value(&normalized).expect("JSON"),
        fixture["normalized"]["AMD"]
    );
    assert!(
        normalized
            .iter()
            .all(|row| row["session_date"].as_str().expect("date") < "2026-11-19")
    );
    clock.0.fetch_add(16, Ordering::SeqCst);
    assert!(market.quote("AMD").await.is_err());
    println!("NO_UNAVAILABLE_CURRENT_CANDLE_OR_STALE_QUOTE=PASS");
    let mut duplicate = fixture["raw_bars"]["AMD"].as_array().expect("bars").clone();
    duplicate.push(duplicate[0].clone());
    assert!(
        market::normalize_bars(
            &calendar,
            "AMD",
            &duplicate,
            "sip",
            instant.date_naive(),
            instant
        )
        .is_err()
    );
    pool.close().await;
}
