use async_trait::async_trait;
use chrono::{DateTime, Utc};
use mainkraft::{
    Error, Result, db,
    engine::Clock,
    information::{self, Information},
    ingestion::ResourceCheck,
    research_providers::{Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
#[derive(Default)]
struct Gate(Mutex<Vec<u64>>);
#[async_trait]
impl ResourceCheck for Gate {
    async fn check(&self, n: u64) -> Result<()> {
        self.0.lock().expect("gate").push(n);
        Ok(())
    }
}
#[derive(Default)]
struct FakeDelay(Mutex<Vec<u64>>);
#[async_trait]
impl Delay for FakeDelay {
    async fn wait(&self, n: u64) {
        self.0.lock().expect("delay").push(n);
    }
}
#[derive(Default)]
struct Wire {
    responses: Mutex<VecDeque<Value>>,
    requests: Mutex<Vec<Value>>,
    pause_at: std::sync::atomic::AtomicUsize,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait]
impl Transport for Wire {
    async fn send(&self, r: &Request) -> Result<Response> {
        assert_eq!(r.timeout_seconds, 40);
        let ordinal = {
            let mut calls = self.requests.lock().expect("requests");
            calls.push(json!({"url":r.url,"query":r.query,"headers":r.headers}));
            calls.len()
        };
        if self.pause_at.load(std::sync::atomic::Ordering::SeqCst) == ordinal {
            self.entered.notify_one();
            self.release.notified().await;
        }
        let response = self
            .responses
            .lock()
            .expect("responses")
            .pop_front()
            .expect("no extra request");
        if response.is_string() {
            return Err(Error::Invalid(
                "Provider connection failed (ConnectError); no data substituted".into(),
            ));
        }
        Ok(Response {
            status: response["status"].as_u64().expect("status") as u16,
            payload: response
                .get("payload")
                .cloned()
                .unwrap_or_else(|| json!({})),
        })
    }
}
fn same(a: &Value, b: &Value) -> bool {
    if a == b {
        return true;
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
        return matches!((DateTime::parse_from_rfc3339(a),DateTime::parse_from_rfc3339(b)),(Ok(a),Ok(b)) if a==b);
    }
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| same(a, b))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(key, a)| b.get(key).is_some_and(|b| same(a, b)))
        }
        _ => false,
    }
}
#[tokio::test]
#[ignore = "requires original Python information oracle in isolated DB schemas"]
async fn private_news_filings_and_information_identities_match_python() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let u = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(u.host_str(), Some("localhost" | "127.0.0.1"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool).await.expect("migrate");
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_INFORMATION_ORACLE").expect("oracle")).expect("oracle"),
    )
    .expect("JSON");
    let wire = Arc::new(Wire::default());
    let delay = Arc::new(FakeDelay::default());
    let gate = Arc::new(Gate::default());
    let providers = Arc::new(
        Providers::new(
            Credentials {
                key: "fixture-key".into(),
                secret: "fixture-secret".into(),
                feed: "sip".into(),
                sec_user_agent: "fixture@example.invalid".into(),
            },
            wire.clone(),
            delay.clone(),
        )
        .expect("provider"),
    );
    let info = Arc::new(Information::new(
        pool.clone(),
        providers,
        Arc::new(FixedClock(
            fixture["instant"]
                .as_str()
                .expect("time")
                .parse()
                .expect("time"),
        )),
        gate.clone(),
        delay.clone(),
    ));
    for case in fixture["cases"].as_array().expect("cases") {
        *wire.responses.lock().expect("responses") = case["responses"]
            .as_array()
            .expect("responses")
            .iter()
            .cloned()
            .collect();
        wire.requests.lock().expect("requests").clear();
        delay.0.lock().expect("delay").clear();
        gate.0.lock().expect("gate").clear();
        let parse = |v: &Value| {
            v.as_str()
                .map(|s| s.parse::<DateTime<Utc>>().expect("time"))
        };
        let actual = info.news(parse(&case["start"]), parse(&case["end"])).await;
        if let Some(error) = case.get("reference_error") {
            match actual {
                Err(Error::Reference { kind, message }) => {
                    assert_eq!(kind, error["kind"].as_str().expect("kind"));
                    assert_eq!(message, error["message"]);
                }
                other => panic!(
                    "{}: expected uncaught reference error {error}, got {other:?}",
                    case["name"]
                ),
            }
        } else if let Some(error) = case.get("error") {
            assert!(
                matches!(actual,Err(Error::Invalid(reason)) if reason==error.as_str().expect("error"))
            );
        } else {
            assert_eq!(
                actual.unwrap_or_else(|e| panic!("{}: {e}", case["name"])),
                case["result"],
                "{}",
                case["name"]
            );
        }
        assert_eq!(
            json!(*wire.requests.lock().expect("requests")),
            case["requests"],
            "{}",
            case["name"]
        );
        assert_eq!(json!(*delay.0.lock().expect("delay")), case["delays"]);
        assert_eq!(
            gate.0.lock().expect("gate").len(),
            case["gate_calls"].as_u64().expect("calls") as usize
        );
        assert!(
            gate.0
                .lock()
                .expect("gate")
                .iter()
                .all(|n| *n == 64 * 1024 * 1024)
        );
        assert!(wire.responses.lock().expect("responses").is_empty());
    }
    let mut tx = pool.begin().await.expect("official metadata transaction");
    for case in fixture["official"].as_array().expect("official") {
        let identity = information::persist_information(
            &mut tx,
            "official_ir",
            &case["metadata"],
            fixture["official_source"]
                .as_str()
                .expect("source")
                .parse()
                .expect("UUID"),
            fixture["instant"]
                .as_str()
                .expect("time")
                .parse()
                .expect("time"),
        )
        .await
        .expect("persist");
        assert_eq!(identity, case["identity"]);
    }
    tx.commit().await.expect("commit");
    for expected in fixture["filings"].as_array().expect("filings") {
        assert_eq!(info.filings().await.expect("filings"), *expected);
    }
    for (name, query) in fixture["queries"].as_object().expect("queries") {
        let rows: Vec<Value> = sqlx::query_scalar(&format!(
            "SELECT to_jsonb(r) FROM ({}) r",
            query.as_str().expect("SQL")
        ))
        .fetch_all(&pool)
        .await
        .expect("snapshot");
        assert!(
            same(&json!(rows), &fixture["snapshot"][name]),
            "{name} persisted state differs"
        );
    }
    let leaked:i64=sqlx::query_scalar("SELECT count(*) FROM source_documents WHERE payload::text LIKE '%private article body must not persist%' OR payload::text LIKE '%private summary must not persist%'").fetch_one(&pool).await.expect("privacy");
    assert_eq!(leaked, 0);
    assert!(
        sqlx::query("UPDATE information_versions SET acquired_at=now()")
            .execute(&pool)
            .await
            .is_err(),
        "original immutable trigger"
    );
    // Inject a page failure after source/first-article work: transaction atomicity
    // must preserve the original committed pages without recording false coverage.
    sqlx::raw_sql("CREATE FUNCTION fail_information_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.external_id='FAIL-SECOND' THEN RAISE EXCEPTION 'injected private metadata transaction failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER information_fixture_fault BEFORE INSERT ON information_versions FOR EACH ROW EXECUTE FUNCTION fail_information_fixture();").execute(&pool).await.expect("fault injection");
    let mut first = fixture["cases"][0]["responses"][0]["payload"]["news"][0].clone();
    first["id"] = json!("FAIL-FIRST");
    let mut second = first.clone();
    second["id"] = json!("FAIL-SECOND");
    let before:(i64,i64,i64)=sqlx::query_as("SELECT (SELECT count(*) FROM source_documents),(SELECT count(*) FROM information_versions),(SELECT count(*) FROM information_fetches)").fetch_one(&pool).await.expect("before");
    wire.responses
        .lock()
        .expect("responses")
        .push_back(json!({"status":200,"payload":{"news":[first.clone(),second.clone()]}}));
    assert!(matches!(
        info.news(None, None).await,
        Err(Error::Database(_))
    ));
    let after:(i64,i64,i64)=sqlx::query_as("SELECT (SELECT count(*) FROM source_documents),(SELECT count(*) FROM information_versions),(SELECT count(*) FROM information_fetches)").fetch_one(&pool).await.expect("after");
    assert_eq!(after, before);
    sqlx::query("DROP TRIGGER information_fixture_fault ON information_versions")
        .execute(&pool)
        .await
        .expect("clear fault");
    wire.responses
        .lock()
        .expect("responses")
        .push_back(json!({"status":200,"payload":{"news":[first,second]}}));
    assert_eq!(
        info.news(None, None).await.expect("retry")["articles_returned"],
        2
    );
    let mut first = fixture["cases"][0]["responses"][0]["payload"]["news"][0].clone();
    first["id"] = json!("CANCEL-FIRST");
    let mut second = first.clone();
    second["id"] = json!("CANCEL-SECOND");
    let responses = vec![
        json!({"status":200,"payload":{"news":[first],"next_page_token":"after-commit"}}),
        json!({"status":200,"payload":{"news":[second]}}),
    ];
    wire.requests.lock().expect("calls").clear();
    *wire.responses.lock().expect("responses") = responses.iter().cloned().collect();
    wire.pause_at.store(2, std::sync::atomic::Ordering::SeqCst);
    let fetches: i64 = sqlx::query_scalar("SELECT count(*) FROM information_fetches")
        .fetch_one(&pool)
        .await
        .expect("coverage before cancellation");
    let owner = info.clone();
    let task = tokio::spawn(async move { owner.news(None, None).await });
    tokio::time::timeout(std::time::Duration::from_secs(4), wire.entered.notified())
        .await
        .expect("second page entered");
    let committed: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM information_versions WHERE external_id='CANCEL-FIRST'",
    )
    .fetch_one(&pool)
    .await
    .expect("committed first page");
    assert_eq!(committed, 1);
    task.abort();
    assert!(task.await.expect_err("cancelled").is_cancelled());
    let still: i64 = sqlx::query_scalar("SELECT count(*) FROM information_fetches")
        .fetch_one(&pool)
        .await
        .expect("no false coverage");
    assert_eq!(still, fetches);
    wire.pause_at.store(0, std::sync::atomic::Ordering::SeqCst);
    wire.requests.lock().expect("calls").clear();
    *wire.responses.lock().expect("responses") = responses.into_iter().collect();
    assert_eq!(
        info.news(None, None).await.expect("restart")["status"],
        "complete"
    );
    let unique:i64=sqlx::query_scalar("SELECT count(*) FROM information_versions WHERE external_id IN ('CANCEL-FIRST','CANCEL-SECOND')").fetch_one(&pool).await.expect("deduplicated resumed pages");
    assert_eq!(unique, 2);
    println!("PRIVATE_NEWS_REQUEST_STATUS_PAGINATION_HASH_AND_PERSISTENCE_PARITY=PASS");
    println!("FILING_REVISIONS_OFFICIAL_METADATA_IDENTITIES_PRIVACY_AND_ROLLBACK=PASS");
    println!("CANCELLED_PAGE_FETCH_CANNOT_CLAIM_COMPLETE_COVERAGE_AND_RESTART_DEDUPLICATES=PASS");
}
