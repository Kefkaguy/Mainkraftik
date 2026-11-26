use async_trait::async_trait;
use mainkraft::{
    allocation::QuantityChange,
    broker::{BrokerApi, BrokerError, BrokerResult, OrderBody},
    db,
};
use serde_json::{Value, json};
use sqlx::{PgPool, Row};
use std::sync::{
    Mutex,
    atomic::{AtomicUsize, Ordering},
};
use uuid::Uuid;

#[derive(Clone)]
enum Response {
    Unknown,
    Rejected,
    Filled,
}
struct FakeBroker {
    calls: AtomicUsize,
    response: Mutex<Response>,
    order: Mutex<Option<Value>>,
}
impl FakeBroker {
    fn new() -> Self {
        Self {
            calls: AtomicUsize::new(0),
            response: Mutex::new(Response::Unknown),
            order: Mutex::new(None),
        }
    }
}
#[async_trait]
impl BrokerApi for FakeBroker {
    async fn account(&self) -> BrokerResult<Value> {
        Ok(json!({"id":"fixture","status":"ACTIVE"}))
    }
    async fn clock(&self) -> BrokerResult<Value> {
        Ok(json!({"is_open":true}))
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        Ok(vec![])
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        Ok(vec![])
    }
    async fn asset(&self, _: &str) -> BrokerResult<Value> {
        Ok(json!({"tradable":true}))
    }
    async fn by_client(&self, _: &str) -> BrokerResult<Option<Value>> {
        Ok(self.order.lock().expect("fixture").clone())
    }
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        match *self.response.lock().expect("fixture") {
            Response::Unknown => Err(BrokerError::Unknown),
            Response::Rejected => Err(BrokerError::Rejected(
                "fixture buying power rejected".into(),
            )),
            Response::Filled => {
                let order = json!({"id":"fixture-order","status":"filled","filled_qty":body.qty,"client_order_id":body.client_order_id});
                *self.order.lock().expect("fixture") = Some(order.clone());
                Ok(order)
            }
        }
    }
    async fn cancel(&self, _: Uuid) -> BrokerResult<()> {
        Ok(())
    }
}

async fn state(pool: &PgPool, key: &str) -> String {
    sqlx::query_scalar("SELECT state FROM learning_intents WHERE client_id=$1")
        .bind(key)
        .fetch_one(pool)
        .await
        .expect("state")
}

#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py and the isolated local PostgreSQL fixture"]
async fn full_durable_order_lifecycle_against_isolated_postgres() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("isolated fixture URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(matches!(url.host_str(), Some("127.0.0.1" | "localhost")) && url.port() == Some(5543));
    assert!(url.path().starts_with("/intelligence_paper_test_rust_"));
    let pool = db::pool(&uri, true).await.expect("pool");
    db::migrate_paper(&pool).await.expect("migration");
    sqlx::query("UPDATE learning_controls SET enabled=true")
        .execute(&pool)
        .await
        .expect("enable fixture");
    let broker = FakeBroker::new();
    let change = QuantityChange {
        side: "buy".into(),
        qty: "10.000000000".into(),
        phase: "increase".into(),
    };
    let at = "2026-11-19T14:30:00+00:00";
    let key = db::client_id("a", "p1", "AMD", "increase").expect("key");
    // Concurrent duplicate callers still perform exactly one submission.
    let (first, second) = tokio::join!(
        db::submit(&pool, "a", &broker, "p1", "AMD", &change, at),
        db::submit(&pool, "a", &broker, "p1", "AMD", &change, at)
    );
    first.expect("submit");
    second.expect("duplicate");
    assert_eq!(broker.calls.load(Ordering::SeqCst), 1);
    assert_eq!(state(&pool, &key).await, "unknown");
    assert_eq!(
        db::reconcile(&pool, "a", &broker).await.expect("unknown"),
        1
    );
    // New pool = restarted worker; the durable intent remains, no resubmission.
    let restarted = db::pool(&uri, true).await.expect("restarted pool");
    *broker.order.lock().expect("fixture") =
        Some(json!({"status":"partially_filled","filled_qty":"3"}));
    assert_eq!(
        db::reconcile(&restarted, "a", &broker)
            .await
            .expect("partial"),
        1
    );
    *broker.order.lock().expect("fixture") =
        Some(json!({"status":"partially_filled","filled_qty":"2"}));
    assert!(db::reconcile(&restarted, "a", &broker).await.is_err());
    let payload: Value =
        sqlx::query_scalar("SELECT order_payload FROM learning_intents WHERE client_id=$1")
            .bind(&key)
            .fetch_one(&pool)
            .await
            .expect("payload");
    assert_eq!(payload["filled_qty"], "3");
    *broker.order.lock().expect("fixture") = Some(json!({"status":"filled","filled_qty":"10"}));
    assert_eq!(
        db::reconcile(&restarted, "a", &broker)
            .await
            .expect("filled"),
        0
    );
    db::submit(&restarted, "a", &broker, "p1", "AMD", &change, at)
        .await
        .expect("restart duplicate");
    assert_eq!(broker.calls.load(Ordering::SeqCst), 1);
    println!("UNKNOWN_PARTIAL_RESTART_DUPLICATE=PASS");
    *broker.response.lock().expect("fixture") = Response::Rejected;
    db::submit(&pool, "a", &broker, "p2", "AMD", &change, at)
        .await
        .expect("rejection");
    let rejected = db::client_id("a", "p2", "AMD", "increase").expect("key");
    assert_eq!(state(&pool, &rejected).await, "rejected");
    assert_eq!(
        db::reconcile(&pool, "a", &broker)
            .await
            .expect("no pending"),
        0
    );
    println!("KNOWN_REJECTION=PASS");
    let other = db::claim(&pool, "b", "p1", "AMD", &change)
        .await
        .expect("other account")
        .expect("claim");
    assert_ne!(other.client_order_id, key);
    assert!(
        db::observe_order(
            &pool,
            "a",
            &other.client_order_id,
            &json!({"status":"filled","filled_qty":"10"})
        )
        .await
        .is_err()
    );
    println!("ACCOUNT_ISOLATION=PASS");
    let evidence = json!({"symbol":"AMD","forecast":0.001,"unicode":"λ"});
    let id = db::record(&pool, "a", "prediction", &evidence)
        .await
        .expect("record");
    assert_eq!(
        id,
        db::record(&pool, "a", "prediction", &evidence)
            .await
            .expect("dedup")
    );
    assert!(
        sqlx::query("UPDATE learning_records SET kind='tampered' WHERE id=$1")
            .bind(&id)
            .execute(&pool)
            .await
            .is_err()
    );
    assert!(
        sqlx::query("DELETE FROM learning_records WHERE id=$1")
            .bind(&id)
            .execute(&pool)
            .await
            .is_err()
    );
    println!("IMMUTABLE_EVIDENCE=PASS");
    assert!(
        db::claim_cycle(&pool, "a", "paper-learning-v1", "123", at)
            .await
            .expect("cycle")
    );
    assert!(
        !db::claim_cycle(&restarted, "a", "paper-learning-v1", "123", at)
            .await
            .expect("restart cycle")
    );
    let lease = db::CycleLease::acquire(&pool)
        .await
        .expect("lease")
        .expect("acquired");
    assert!(
        db::CycleLease::acquire(&restarted)
            .await
            .expect("competing lease")
            .is_none()
    );
    lease.finish().await.expect("release");
    db::CycleLease::acquire(&restarted)
        .await
        .expect("lease")
        .expect("reacquired")
        .finish()
        .await
        .expect("release");
    println!("DURABLE_CYCLE_AND_SHARED_PYTHON_LEASE=PASS");
    // Inject a DB failure AFTER a broker fill: transaction rolls back; the
    // submitting intent can subsequently be reconciled without another POST.
    sqlx::raw_sql("CREATE FUNCTION test_fail_order_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.kind='order_version' THEN RAISE EXCEPTION 'injected fixture failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER test_order_failure BEFORE INSERT ON learning_records FOR EACH ROW EXECUTE FUNCTION test_fail_order_evidence();").execute(&pool).await.expect("fault injection");
    *broker.response.lock().expect("fixture") = Response::Filled;
    let fault = db::client_id("a", "fault", "MSFT", "increase").expect("key");
    assert!(
        db::submit(&pool, "a", &broker, "fault", "MSFT", &change, at)
            .await
            .is_err()
    );
    assert_eq!(state(&pool, &fault).await, "submitting");
    let calls = broker.calls.load(Ordering::SeqCst);
    sqlx::raw_sql("DROP TRIGGER test_order_failure ON learning_records; DROP FUNCTION test_fail_order_evidence();").execute(&pool).await.expect("remove fault");
    assert_eq!(
        db::reconcile(&restarted, "a", &broker)
            .await
            .expect("recover fill"),
        0
    );
    assert_eq!(state(&pool, &fault).await, "filled");
    assert_eq!(broker.calls.load(Ordering::SeqCst), calls);
    println!("DB_FAILURE_AFTER_FILL_RECOVERY=PASS");
    sqlx::query("UPDATE learning_controls SET enabled=false")
        .execute(&pool)
        .await
        .expect("pause");
    assert!(
        db::claim(&pool, "a", "paused", "AMD", &change)
            .await
            .expect("pause")
            .is_none()
    );
    println!("PAUSE_BEFORE_INTENT=PASS");
    // Exercise the real dashboard status query with existing-format records.
    sqlx::query("INSERT INTO learning_accounts(account_id,label,account_number,baseline_equity) VALUES('a','Original account · fixed model','TEST-A',1000),('b','Second account · online learning','TEST-B',1000000)")
        .execute(&pool).await.expect("fixture accounts");
    db::record(&pool,"a","snapshot",&json!({"at":at,"market_open":false,"enabled":false,"positions":[{"symbol":"UBS","qty":"1","market_value":"45.00","unrealized_pl":"-0.50"}],"account":{"cash":"954.50","equity":"999.50","buying_power":"1999.00"}})).await.expect("snapshot");
    db::record(
        &pool,
        "a",
        "outcome",
        &json!({"prediction_id":"outcome-fixture","actual_return":-0.001,"forecast_return":0.002}),
    )
    .await
    .expect("outcome metric");
    let native = mainkraft::paper_api::status(&pool)
        .await
        .expect("native status query");
    assert_eq!(native["accounts"].as_array().expect("accounts").len(), 2);
    if let Ok(path) = std::env::var("RUST_TEST_STATUS_OUTPUT") {
        std::fs::write(path, serde_json::to_vec(&native).expect("JSON"))
            .expect("status oracle output");
    }
    let before: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_records")
        .fetch_one(&pool)
        .await
        .expect("count");
    db::migrate_paper(&pool).await.expect("repeat migration");
    let after: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_records")
        .fetch_one(&pool)
        .await
        .expect("count");
    assert_eq!(before, after);
    let enabled: bool = sqlx::query_scalar("SELECT enabled FROM learning_controls WHERE id=1")
        .fetch_one(&pool)
        .await
        .expect("state");
    assert!(!enabled);
    println!("DASHBOARD_STATUS_AND_SCHEMA_PRESERVATION=PASS");
    // No fake broker method ever reaches HTTP; no deployment credentials exist.
    let rows = sqlx::query(
        "SELECT account_id,kind,count(*) n FROM learning_records GROUP BY account_id,kind",
    )
    .fetch_all(&pool)
    .await
    .expect("evidence");
    assert!(
        rows.iter()
            .any(|r| r.get::<String, _>("kind") == "order_version")
    );
    restarted.close().await;
    pool.close().await;
}
