use async_trait::async_trait;
use chrono::{DateTime, Utc};
use mainkraft::{
    Error, Result, db,
    engine::Clock,
    official_ir::{OfficialIr, Page, Transport},
    research_providers::Delay,
};
use mainkraft::{
    calendar::Calendar,
    information::Information,
    ingestion::{Ingester, ResourceCheck},
    research_providers as p,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicI64, Ordering},
};
struct TestClock(AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> DateTime<Utc> {
        DateTime::from_timestamp(self.0.load(Ordering::SeqCst), 0).expect("time")
    }
}
#[derive(Default)]
struct FakeDelay(Mutex<Vec<u64>>);
#[async_trait]
impl Delay for FakeDelay {
    async fn wait(&self, n: u64) {
        self.0.lock().expect("delays").push(n);
    }
}
#[derive(Default)]
struct Wire {
    routes: Mutex<Value>,
    calls: Mutex<Vec<Value>>,
}
#[derive(Default)]
struct Gate(Mutex<Vec<u64>>);
#[async_trait]
impl ResourceCheck for Gate {
    async fn check(&self, n: u64) -> Result<()> {
        self.0.lock().expect("projections").push(n);
        Ok(())
    }
}
#[derive(Default)]
struct DataWire(Mutex<Vec<Value>>);
#[async_trait]
impl p::Transport for DataWire {
    async fn send(&self, r: &p::Request) -> Result<p::Response> {
        self.0.lock().expect("data calls").push(
            json!({"url":r.url,"headers":r.headers,"query":r.query,"timeout":r.timeout_seconds}),
        );
        let (status, payload) = if r.url.ends_with("/news") {
            (
                200,
                json!({"news":[{"id":"refresh-fixture","created_at":"2026-11-18T12:00:00Z","updated_at":"2026-11-18T13:00:00Z","symbols":["MSI"],"headline":"Company reports results","content":"NOT_STORED_NEWS_BODY"}]}),
            )
        } else if r.url.ends_with("/bars") {
            (
                200,
                json!({"bars":[{"t":"2026-11-18T05:00:00Z","o":100,"h":102,"l":99,"c":101,"v":1000000}]}),
            )
        } else {
            (403, json!({}))
        };
        Ok(p::Response { status, payload })
    }
}
#[async_trait]
impl Transport for Wire {
    async fn get(&self, url: &str, agent: &str) -> Result<Page> {
        self.calls
            .lock()
            .expect("calls")
            .push(json!({"url":url,"agent":agent}));
        let value = self
            .routes
            .lock()
            .expect("routes")
            .get(url)
            .cloned()
            .unwrap_or_else(|| panic!("unexpected IR request {url}"));
        let content = if let Some(n) = value["body_size"].as_u64() {
            vec![b'x'; n as usize]
        } else if let Some(s) = value["hex"].as_str() {
            hex::decode(s).expect("hex")
        } else {
            value["body"].as_str().unwrap_or("").as_bytes().to_vec()
        };
        Ok(Page {
            url: url::Url::parse(url).expect("URL").to_string(),
            status: value["status"].as_u64().expect("status") as u16,
            location: value["location"].as_str().map(str::to_owned),
            charset: value["charset"].as_str().map(str::to_owned),
            content,
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
            a.len() == b.len() && a.iter().all(|(k, a)| b.get(k).is_some_and(|b| same(a, b)))
        }
        _ => false,
    }
}
#[tokio::test]
#[ignore = "requires isolated Python official IR workflow oracle"]
async fn official_pages_and_enrichment_match_original_without_backdating_or_rewriting() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let u = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(u.host_str(), Some("localhost" | "127.0.0.1"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool).await.expect("migrate");
    let f: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_IR_ORACLE").expect("oracle")).expect("oracle"),
    )
    .expect("JSON");
    let clock = Arc::new(TestClock(AtomicI64::new(0)));
    let wire = Arc::new(Wire::default());
    let delay = Arc::new(FakeDelay::default());
    let data = Arc::new(DataWire::default());
    let gate = Arc::new(Gate::default());
    let providers = Arc::new(
        p::Providers::new(
            p::Credentials {
                key: "fixture-key".into(),
                secret: "fixture-secret".into(),
                feed: "sip".into(),
                sec_user_agent: "fixture@example.invalid".into(),
            },
            data.clone(),
            delay.clone(),
        )
        .expect("providers"),
    );
    let info = Information::new(
        pool.clone(),
        providers.clone(),
        clock.clone(),
        gate.clone(),
        delay.clone(),
    );
    let markets = Ingester::new(
        pool.clone(),
        providers,
        clock.clone(),
        Arc::new(Calendar::embedded().expect("calendar")),
        gate.clone(),
    );
    let ir = OfficialIr::new(
        pool.clone(),
        wire.clone(),
        clock.clone(),
        delay.clone(),
        "fixture@example.invalid".into(),
    );
    for case in f["cases"].as_array().expect("cases") {
        *wire.routes.lock().expect("routes") = case["routes"].clone();
        wire.calls.lock().expect("calls").clear();
        delay.0.lock().expect("delays").clear();
        data.0.lock().expect("data calls").clear();
        gate.0.lock().expect("gate").clear();
        if let Some(time) = case["instant"].as_str() {
            clock.0.store(
                time.parse::<DateTime<Utc>>().expect("time").timestamp(),
                Ordering::SeqCst,
            );
        }
        let actual=match case["operation"].as_str().expect("operation"){
            "ingest"=>ir.ingest().await,"enrich"=>ir.enrich().await,"refresh"|"backfill"=>info.refresh(&ir,&markets,case["operation"]=="backfill").await,"get"=>ir.get(case["url"].as_str().expect("url"),false).await.map(|p|json!({"url":p.url,"status":p.status,"text":if case["result"]["text"].is_null(){None}else{Some(p.text())},"sha256":hex::encode(Sha256::digest(&p.content))})),_=>panic!("operation")};
        if let Some(error) = case.get("reference_error") {
            match actual {
                Err(Error::Reference { kind, message }) => {
                    assert_eq!(kind, error["kind"].as_str().expect("kind"));
                    assert_eq!(message, error["message"]);
                }
                other => panic!(
                    "{} {}: expected uncaught reference error {error}, got {other:?}",
                    case["operation"], case["phase"]
                ),
            }
        } else if let Some(error) = case.get("error") {
            assert!(
                matches!(actual,Err(Error::Invalid(reason)) if reason==error.as_str().expect("error")),
                "{case}"
            );
        } else {
            assert_eq!(
                actual.unwrap_or_else(|e| panic!("{} {}: {e}", case["operation"], case["phase"])),
                case["result"],
                "{} {} {}",
                case["operation"],
                case["phase"],
                case["label"]
            );
        }
        assert_eq!(
            json!(*wire.calls.lock().expect("calls")),
            case["requests"],
            "requests {} {}",
            case["operation"],
            case["phase"]
        );
        assert_eq!(json!(*delay.0.lock().expect("delays")), case["delays"]);
        assert_eq!(
            json!(*data.0.lock().expect("data calls")),
            case["data_requests"]
        );
        assert_eq!(
            json!(*gate.0.lock().expect("projections")),
            case["projections"]
        );
    }
    for (name, sql) in f["queries"].as_object().expect("queries") {
        let rows: Vec<Value> = sqlx::query_scalar(&format!(
            "SELECT to_jsonb(r) FROM ({}) r",
            sql.as_str().expect("SQL")
        ))
        .fetch_all(&pool)
        .await
        .expect("snapshot");
        assert!(
            same(&json!(rows), &f["snapshot"][name]),
            "{name} evidence differs"
        );
    }
    let body: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM source_documents WHERE payload::text LIKE '%PARA_STORAGE_MARKER%'",
    )
    .fetch_one(&pool)
    .await
    .expect("privacy");
    assert_eq!(body, 0);
    let news_body: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM source_documents WHERE payload::text LIKE '%NOT_STORED_NEWS_BODY%'",
    )
    .fetch_one(&pool)
    .await
    .expect("news privacy");
    assert_eq!(news_body, 0);
    assert!(
        sqlx::query("UPDATE information_versions SET acquired_at=now()")
            .execute(&pool)
            .await
            .is_err()
    );
    let mut routes = f["cases"][0]["routes"].clone();
    routes["https://investor.axon.com/2026-11-18-story-0"]["body"] =
        json!("<h1>Rollback-only headline</h1>");
    *wire.routes.lock().expect("routes") = routes;
    clock.0.fetch_add(3600, Ordering::SeqCst);
    let before:(i64,i64)=sqlx::query_as("SELECT (SELECT count(*) FROM source_documents),(SELECT count(*) FROM information_versions)").fetch_one(&pool).await.expect("before listing fault");
    sqlx::raw_sql("CREATE FUNCTION fail_ir_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.external_id LIKE '%story-5' THEN RAISE EXCEPTION 'injected listing transaction failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER ir_fixture_fault BEFORE INSERT ON information_versions FOR EACH ROW EXECUTE FUNCTION fail_ir_fixture();").execute(&pool).await.expect("listing fault");
    assert!(matches!(ir.ingest().await, Err(Error::Database(_))));
    let after:(i64,i64)=sqlx::query_as("SELECT (SELECT count(*) FROM source_documents),(SELECT count(*) FROM information_versions)").fetch_one(&pool).await.expect("after listing fault");
    assert_eq!(after, before);
    sqlx::query("DROP TRIGGER ir_fixture_fault ON information_versions")
        .execute(&pool)
        .await
        .expect("clear listing fault");
    ir.ingest().await.expect("clean listing retry");
    let written:i64=sqlx::query_scalar("SELECT count(*) FROM information_versions WHERE payload->>'headline'='Rollback-only headline'").fetch_one(&pool).await.expect("one recovered version");
    assert_eq!(written, 1);
    println!("ORIGINAL_OFFICIAL_IR_HTTP_ROBOTS_LISTING_ARTICLE_AND_ENRICHMENT_PARITY=PASS");
    println!("IMMUTABLE_PUBLICATION_IDENTITIES_AVAILABILITY_SOURCE_HASHES_AND_PRIVACY=PASS");
    println!("INFORMATION_REFRESH_BACKFILL_MARKET_CONTEXT_AND_WHOLE_LISTING_ROLLBACK=PASS");
}
