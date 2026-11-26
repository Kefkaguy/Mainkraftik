use async_trait::async_trait;
use chrono::{DateTime, Utc};
use mainkraft::{
    Error, Result,
    calendar::Calendar,
    db,
    engine::Clock,
    forecasting::Forecaster,
    information::Information,
    ingestion::{Ingester, ResourceCheck},
    jobs::{Dispatch, DispatchError, Worker},
    official_ir::{OfficialIr, Page},
    research_dispatch::NativeDispatch,
    research_providers::{Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use uuid::Uuid;
struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
#[derive(Default)]
struct Gate {
    calls: Mutex<Vec<u64>>,
    fail: AtomicBool,
}
#[async_trait]
impl ResourceCheck for Gate {
    async fn check(&self, n: u64) -> Result<()> {
        self.calls.lock().expect("calls").push(n);
        if self.fail.load(Ordering::SeqCst) {
            return Err(Error::Invalid("Fixture resource exhausted".into()));
        }
        Ok(())
    }
}
#[derive(Default)]
struct Delays(Mutex<Vec<u64>>);
#[async_trait]
impl Delay for Delays {
    async fn wait(&self, n: u64) {
        self.0.lock().expect("delays").push(n);
    }
}
struct Wire {
    fixture: Value,
    phase: Mutex<String>,
    calls: Mutex<Vec<Value>>,
    block: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait]
impl Transport for Wire {
    async fn send(&self, r: &Request) -> Result<Response> {
        self.calls
            .lock()
            .expect("calls")
            .push(json!({"url":r.url,"query":r.query,"headers":r.headers,"agent":null}));
        if r.url.ends_with("/bars") && self.block.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        let phase = self.phase.lock().expect("phase").clone();
        let (status, payload) = if r.url.ends_with("/news") {
            if phase == "denied" {
                (403, json!({}))
            } else {
                (
                    200,
                    json!({"news":[{"id":"dispatch-news","created_at":if phase=="overflow"{"0001-01-01T00+01"}else{"2026-11-18T12Z"},"updated_at":"2026-11-18T13Z","symbols":["MSI"],"headline":"Contract awarded","content":"PRIVATE_BODY_NOT_PERSISTED"}]}),
                )
            }
        } else if r.url.ends_with("/bars") {
            (200, json!({"bars":self.fixture["raw"]}))
        } else if r.url.ends_with("corporate-actions") {
            (200, json!({"corporate_actions":{}}))
        } else if r.url.contains("/submissions/") || r.url.contains("CIK0001069183") {
            (200, json!({}))
        } else {
            (200, self.fixture["facts"].clone())
        };
        Ok(Response { status, payload })
    }
}
#[async_trait]
impl mainkraft::official_ir::Transport for Wire {
    async fn get(&self, url: &str, agent: &str) -> Result<Page> {
        self.calls
            .lock()
            .expect("calls")
            .push(json!({"url":url,"query":{},"headers":{},"agent":agent}));
        let body = if url.ends_with("/robots.txt") {
            "User-agent: *\nAllow: /"
        } else if url.ends_with("/press-releases/") {
            "<a href=\"/2026-11-18-story\">fixture</a>"
        } else if url.ends_with("/newsroom.html") {
            "<a href=\"/newsroom/press-releases/fixture.html\">fixture</a>"
        } else {
            "<h1>Raises guidance</h1><meta name=\"press_release_publish_date\" content=\"2026W473T120000Z\">"
        };
        Ok(Page {
            url: url.into(),
            status: 200,
            location: None,
            content: body.as_bytes().to_vec(),
            charset: None,
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
                    .all(|(key, value)| b.get(key).is_some_and(|other| same(value, other)))
        }
        _ => false,
    }
}
async fn enqueue(pool: &sqlx::PgPool, id: Uuid, kind: &str, payload: &Value) {
    let mut tx = pool.begin().await.expect("enqueue");
    sqlx::query("INSERT INTO jobs(id,kind,payload) VALUES($1,$2,$3)")
        .bind(id)
        .bind(kind)
        .bind(payload)
        .execute(&mut *tx)
        .await
        .expect("job");
    sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,'queued',$2)")
        .bind(id)
        .bind(json!({"kind":kind}))
        .execute(&mut *tx)
        .await
        .expect("event");
    tx.commit().await.expect("commit");
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires original Python durable dispatch oracle in disposable local schemas"]
async fn native_jobs_use_actual_services_match_python_and_recover_committed_refresh() {
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
        &std::fs::read(std::env::var("RUST_DISPATCH_ORACLE").expect("oracle")).expect("file"),
    )
    .expect("JSON");
    let clock = Arc::new(FixedClock(
        fixture["instant"]
            .as_str()
            .expect("instant")
            .parse()
            .expect("time"),
    ));
    let gate = Arc::new(Gate::default());
    let delay = Arc::new(Delays::default());
    let calendar = Arc::new(Calendar::embedded().expect("calendar"));
    let wire = Arc::new(Wire {
        fixture: fixture.clone(),
        phase: Mutex::new("normal".into()),
        calls: Mutex::new(Vec::new()),
        block: AtomicBool::new(false),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
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
        .expect("providers"),
    );
    let ingestion = Arc::new(Ingester::new(
        pool.clone(),
        providers.clone(),
        clock.clone(),
        calendar.clone(),
        gate.clone(),
    ));
    let info = Arc::new(Information::new(
        pool.clone(),
        providers,
        clock.clone(),
        gate.clone(),
        delay.clone(),
    ));
    let ir = Arc::new(OfficialIr::new(
        pool.clone(),
        wire.clone(),
        clock.clone(),
        delay.clone(),
        "fixture@example.invalid".into(),
    ));
    let forecasts =
        Arc::new(Forecaster::new(pool.clone(), calendar, clock, "sip".into()).expect("forecasts"));
    let dispatcher = Arc::new(NativeDispatch::new(
        pool.clone(),
        gate.clone(),
        ingestion,
        info,
        ir,
        forecasts,
    ));
    let worker = Arc::new(Worker::new(pool.clone(), dispatcher.clone()));
    for case in fixture["cases"].as_array().expect("cases") {
        *wire.phase.lock().expect("phase") = case["phase"].as_str().expect("phase").into();
        wire.calls.lock().expect("calls").clear();
        gate.calls.lock().expect("calls").clear();
        delay.0.lock().expect("delay").clear();
        gate.fail
            .store(case["phase"] == "resource-denied", Ordering::SeqCst);
        let id = case["id"].as_str().expect("id").parse().expect("UUID");
        enqueue(
            &pool,
            id,
            case["kind"].as_str().expect("kind"),
            &case["payload"],
        )
        .await;
        let actual = worker.run_once().await.expect("run").expect("job");
        assert!(
            same(&actual, &case["result"]),
            "{} {}: actual={actual}, expected={}",
            case["kind"],
            case["phase"],
            case["result"]
        );
        assert_eq!(
            json!(*wire.calls.lock().expect("calls")),
            case["requests"],
            "requests for {} {}",
            case["kind"],
            case["phase"]
        );
        assert_eq!(
            json!(*gate.calls.lock().expect("gate")),
            case["projections"],
            "resource gates"
        );
        assert_eq!(
            json!(*delay.0.lock().expect("delays")),
            case["delays"],
            "delays"
        );
    }
    for (name, query) in fixture["queries"].as_object().expect("queries") {
        let sql = format!(
            "SELECT to_jsonb(row) FROM ({}) row",
            query.as_str().expect("SQL")
        );
        let rows: Vec<Value> = sqlx::query_scalar(&sql)
            .fetch_all(&pool)
            .await
            .expect("snapshot");
        assert!(
            same(&json!(rows), &fixture["snapshot"][name]),
            "{name} persisted state differs"
        );
    }
    let leaks:i64=sqlx::query_scalar("SELECT count(*) FROM source_documents WHERE payload::text LIKE '%PRIVATE_BODY_NOT_PERSISTED%'").fetch_one(&pool).await.expect("privacy");
    assert_eq!(leaks, 0);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM forecast_outcomes")
            .fetch_one(&pool)
            .await
            .expect("outcomes"),
        1
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM detection_outcomes")
            .fetch_one(&pool)
            .await
            .expect("outcomes"),
        1
    );
    gate.fail.store(false, Ordering::SeqCst);
    *wire.phase.lock().expect("phase") = "normal".into();
    wire.calls.lock().expect("calls").clear();
    for kind in mainkraft::jobs::KINDS {
        if !NativeDispatch::implemented(kind) {
            assert!(matches!(
                dispatcher.execute(kind, &json!({})).await,
                Err(DispatchError::Unexpected("NativeWorkflowUnavailable"))
            ));
        }
    }
    assert!(
        wire.calls.lock().expect("calls").is_empty(),
        "unmigrated jobs perform no external work"
    );
    // Real workflow + failed terminal transaction: next worker reclaims durable
    // state and reuses the completed ingestion run instead of refetching it.
    let id = Uuid::new_v4();
    enqueue(&pool, id, "refresh", &json!({"symbols":["SPY"]})).await;
    sqlx::query("CREATE FUNCTION fail_dispatch_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status IN ('completed','partial') THEN RAISE EXCEPTION 'terminal transaction fixture'; END IF; RETURN NEW; END $$").execute(&pool).await.expect("function");
    sqlx::query("CREATE TRIGGER fail_dispatch_completion BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION fail_dispatch_completion()").execute(&pool).await.expect("trigger");
    assert!(matches!(worker.run_once().await, Err(Error::Database(_))));
    sqlx::query("DROP TRIGGER fail_dispatch_completion ON jobs")
        .execute(&pool)
        .await
        .expect("drop trigger");
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT status FROM jobs WHERE id=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .expect("running"),
        "running"
    );
    let before = wire
        .calls
        .lock()
        .expect("calls")
        .iter()
        .filter(|r| r["url"].as_str().is_some_and(|u| u.ends_with("/SPY/bars")))
        .count();
    assert_eq!(before, 1);
    let recovered = Worker::new(pool.clone(), dispatcher.clone());
    let result = recovered.run_once().await.expect("recovered").expect("job");
    assert_ne!(result["status"], "failed");
    let after = wire
        .calls
        .lock()
        .expect("calls")
        .iter()
        .filter(|r| r["url"].as_str().is_some_and(|u| u.ends_with("/SPY/bars")))
        .count();
    assert_eq!(
        before, after,
        "committed SPY ingestion must not refetch on recovery"
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM ingest_runs WHERE id=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .expect("one run"),
        1
    );
    assert_eq!(
        sqlx::query_scalar::<_, i32>("SELECT attempts FROM jobs WHERE id=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .expect("attempts"),
        2
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM job_events WHERE job_id=$1 AND event='recovered'"
        )
        .bind(id)
        .fetch_one(&pool)
        .await
        .expect("event"),
        1
    );
    // Cancelling an HTTP caller cannot detach the actual service work from its
    // exclusive worker lease or allow a competing worker to reclaim it.
    let id = Uuid::new_v4();
    enqueue(&pool, id, "refresh", &json!({"symbols":["SPY"]})).await;
    wire.block.store(true, Ordering::SeqCst);
    let cloned = worker.clone();
    let caller = tokio::spawn(async move { cloned.run_once().await });
    tokio::time::timeout(std::time::Duration::from_secs(10), wire.entered.notified())
        .await
        .expect("native request must reach barrier");
    caller.abort();
    assert!(caller.await.expect_err("cancelled").is_cancelled());
    assert!(
        Worker::new(pool.clone(), dispatcher)
            .run_once()
            .await
            .expect("competing worker")
            .is_none()
    );
    wire.release.notify_one();
    let completed = tokio::time::timeout(std::time::Duration::from_secs(30), worker.drain())
        .await
        .expect("native work must drain after release")
        .expect("drain")
        .expect("job");
    assert_eq!(completed["id"], id.to_string());
    assert_ne!(completed["status"], "failed");
    println!(
        "NATIVE_ACTUAL_RESEARCH_DISPATCH_PYTHON_WORKER_PARITY_RESOURCE_GATES_AND_RESTART_RECOVERY=PASS"
    );
}
