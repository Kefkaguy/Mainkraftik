use async_trait::async_trait;
use chrono::{DateTime, NaiveDate, Utc};
use mainkraft::{Result, db, discovery, engine::Clock, market::DataApi};
use serde_json::{Value, json};
use std::sync::{
    Mutex,
    atomic::{AtomicUsize, Ordering},
};

struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
struct Data {
    fixture: Value,
    calls: AtomicUsize,
    queries: Mutex<Vec<Value>>,
    repeat: bool,
}
#[async_trait]
impl DataApi for Data {
    async fn bars(
        &self,
        _: &str,
        _: NaiveDate,
        _: DateTime<Utc>,
        _: NaiveDate,
    ) -> Result<Vec<Value>> {
        panic!("not used")
    }
    async fn latest_quote(&self, _: &str) -> Result<Value> {
        panic!("no quote/order path")
    }
    async fn news_page(
        &self,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        token: Option<&str>,
    ) -> Result<Value> {
        let mut query = json!({"start":start.to_rfc3339(),"end":end.to_rfc3339(),"sort":"desc","limit":50,"include_content":"false"});
        if let Some(token) = token {
            query["page_token"] = json!(token);
        }
        self.queries.lock().expect("queries").push(query);
        let index = self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.fixture["pages"][if self.repeat { 0 } else { index % 2 }].clone())
    }
}

#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated paper DB"]
async fn acquired_news_matches_python_and_keeps_truncation_and_provenance_explicit() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/discovery-parity.json")).expect("fixture");
    assert_eq!(
        discovery::eligible_catalog(fixture["catalog"].as_array().expect("catalog")),
        fixture["eligible"]
    );
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(matches!(url.host_str(), Some("localhost" | "127.0.0.1")));
    assert_eq!(url.port(), Some(5543));
    assert!(url.path().starts_with("/intelligence_paper_test_rust_"));
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("migrate");
    let instant = fixture["started"]
        .as_str()
        .expect("instant")
        .parse::<DateTime<Utc>>()
        .expect("time");
    let clock = FixedClock(instant);
    let data = Data {
        fixture: fixture.clone(),
        calls: AtomicUsize::new(0),
        queries: Mutex::new(vec![]),
        repeat: false,
    };
    let acquired = discovery::acquire(&pool, &data, &clock, instant)
        .await
        .expect("acquire");
    assert_eq!(acquired.payload, fixture["expected"]["payload"]);
    assert_eq!(acquired.source_id, fixture["expected"]["id"]);
    assert_eq!(
        json!(*data.queries.lock().expect("queries")),
        fixture["queries"]
    );
    assert_eq!(
        json!(
            acquired.payload["articles"]
                .as_object()
                .expect("articles")
                .keys()
                .collect::<Vec<_>>()
        ),
        fixture["expected_article_order"]
    );
    assert!(!acquired.payload.to_string().contains("PRIVATE"));
    assert_eq!(
        discovery::acquire(&pool, &data, &clock, instant)
            .await
            .expect("replay")
            .source_id,
        acquired.source_id
    );
    let rows: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_sources")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(rows, 1);
    assert!(
        sqlx::query("UPDATE pilot_sources SET payload='{}'")
            .execute(&pool)
            .await
            .is_err()
    );
    let repeated = Data {
        fixture: fixture.clone(),
        calls: AtomicUsize::new(0),
        queries: Mutex::new(vec![]),
        repeat: true,
    };
    let result = discovery::acquire(&pool, &repeated, &clock, instant)
        .await
        .expect("bounded acquisition");
    assert_eq!(repeated.calls.load(Ordering::SeqCst), 20);
    assert_eq!(result.payload["truncated"], true);
    assert_eq!(result.payload["pages"], 20);
    assert!(discovery::liquidity(&vec![json!({"close":5.0,"volume":4_000_000.0}); 19]).is_err());
    assert_eq!(
        discovery::liquidity(&vec![json!({"close":5.0,"volume":4_000_000.0}); 20])
            .expect("exact boundary"),
        (5.0, 20_000_000.0)
    );
    assert!(discovery::liquidity(&vec![json!({"close":4.99,"volume":100_000_000.0}); 20]).is_err());
    println!("CATALOG_AND_ORIGINAL_PYTHON_NEWS_SOURCE_ID_PARITY=PASS");
    println!("FUTURE_INVALID_AND_OMITTED_SOURCE_FIELDS_EXCLUDED=PASS");
    println!("UPDATED_NEWS_INPUT_ORDER_REPLAY_IMMUTABILITY=PASS");
    println!("BOUNDED_PAGINATION_WITH_EXPLICIT_TRUNCATION=PASS");
    pool.close().await;
}
