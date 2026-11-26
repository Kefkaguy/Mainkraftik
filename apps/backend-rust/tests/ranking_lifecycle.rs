use async_trait::async_trait;
use chrono::{Duration, Utc};
use mainkraft::{Result, db, engine::SystemClock, ranking};
use serde_json::Value;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct Unavailable {
    calls: AtomicUsize,
}
#[async_trait]
impl ranking::Transport for Unavailable {
    async fn complete(&self, _: &Value) -> Result<(u16, Vec<u8>)> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok((503, b"provider secret must not be recorded".to_vec()))
    }
}

#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated paper DB"]
async fn shared_budget_survives_concurrency_failure_and_restart_without_retry() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(matches!(url.host_str(), Some("localhost" | "127.0.0.1")));
    assert_eq!(url.port(), Some(5543));
    assert!(url.path().starts_with("/intelligence_paper_test_rust_"));
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("migrate");
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/ranking-parity.json")).expect("fixture");
    let transport = Arc::new(Unavailable {
        calls: AtomicUsize::new(0),
    });
    let ranker = Arc::new(ranking::Ranker {
        pool: pool.clone(),
        model: "fixture-model".into(),
        transport: transport.clone(),
        clock: Arc::new(SystemClock),
    });
    let session = Utc::now()
        .with_timezone(&chrono_tz::America::New_York)
        .date_naive();
    let mut tasks = Vec::new();
    for _ in 0..12 {
        let ranker = ranker.clone();
        let candidates = fixture["candidates"].clone();
        let articles = fixture["articles"].clone();
        tasks.push(tokio::spawn(async move {
            ranker.rank(session, &candidates, &articles, None).await
        }));
    }
    for task in tasks {
        assert!(task.await.expect("task").is_err());
    }
    assert_eq!(transport.calls.load(Ordering::SeqCst), 3);
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_groq_calls")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(count, 3);
    let events: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_events")
        .fetch_one(&pool)
        .await
        .expect("events");
    assert_eq!(events, 0); // HTTP errors never retain raw provider messages.
    drop(ranker);
    pool.close().await;
    let restored = db::pool(&uri, true).await.expect("restart");
    let ranker = ranking::Ranker {
        pool: restored.clone(),
        model: "fixture-model".into(),
        transport: transport.clone(),
        clock: Arc::new(SystemClock),
    };
    assert!(
        ranker
            .rank(session, &fixture["candidates"], &fixture["articles"], None)
            .await
            .is_err()
    );
    assert_eq!(transport.calls.load(Ordering::SeqCst), 3);
    // A new application day isolates the monthly cap from the daily cap,
    // while SQL's original database-month boundary remains authoritative.
    sqlx::query("INSERT INTO pilot_groq_calls(session_date,payload) SELECT $1,'{}'::jsonb FROM generate_series(1,57)").bind(session).execute(&restored).await.expect("monthly fixture");
    assert!(
        ranking::reserve(
            &restored,
            session,
            Utc::now() + Duration::days(1),
            "fixture-model",
            "hash"
        )
        .await
        .is_err()
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pilot_groq_calls")
        .fetch_one(&restored)
        .await
        .expect("count");
    assert_eq!(count, 60);
    assert!(
        sqlx::query("UPDATE pilot_groq_calls SET payload='{}'")
            .execute(&restored)
            .await
            .is_err()
    );
    println!("SHARED_DAILY_MONTHLY_BUDGET_AND_RESTART=PASS");
    println!("FAILED_REQUEST_CONSUMES_RESERVATION_NO_RETRY=PASS");
    println!("IMMUTABLE_RESERVATIONS_NO_PROVIDER_ERROR_LEAK=PASS");
    restored.close().await;
}
