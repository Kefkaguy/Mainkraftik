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
    jobs::Worker,
    official_ir::{OfficialIr, Page},
    research_dispatch::NativeDispatch,
    research_providers::{Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
use uuid::Uuid;

struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
struct Gate {
    denied: bool,
    calls: Mutex<Vec<u64>>,
}
#[async_trait]
impl ResourceCheck for Gate {
    async fn check(&self, n: u64) -> Result<()> {
        self.calls.lock().expect("calls").push(n);
        if self.denied {
            Err(Error::Invalid("Fixture resource exhausted".into()))
        } else {
            Ok(())
        }
    }
}
#[derive(Default)]
struct NoNetwork(AtomicUsize);
#[async_trait]
impl Transport for NoNetwork {
    async fn send(&self, _: &Request) -> Result<Response> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Err(Error::Invalid("Unexpected research HTTP request".into()))
    }
}
#[async_trait]
impl mainkraft::official_ir::Transport for NoNetwork {
    async fn get(&self, _: &str, _: &str) -> Result<Page> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Err(Error::Invalid("Unexpected IR request".into()))
    }
}
struct NoDelay;
#[async_trait]
impl Delay for NoDelay {
    async fn wait(&self, _: u64) {
        panic!("Issuance must not perform provider delays")
    }
}
fn services(
    pool: &PgPool,
    instant: DateTime<Utc>,
    gate: Arc<Gate>,
    wire: Arc<NoNetwork>,
) -> (Arc<Forecaster>, Arc<NativeDispatch>) {
    let clock = Arc::new(FixedClock(instant));
    let calendar = Arc::new(Calendar::embedded().expect("calendar"));
    let delay = Arc::new(NoDelay);
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
        wire,
        clock.clone(),
        delay,
        "fixture@example.invalid".into(),
    ));
    let forecasts =
        Arc::new(Forecaster::new(pool.clone(), calendar, clock, "sip".into()).expect("forecaster"));
    let dispatch = Arc::new(NativeDispatch::new(
        pool.clone(),
        gate,
        ingestion,
        info,
        ir,
        forecasts.clone(),
    ));
    (forecasts, dispatch)
}
fn error_value(error: Error) -> Value {
    let (kind, message) = match error {
        Error::Invalid(m) => ("ValueError", Some(m)),
        Error::Reference { kind, .. } => (kind, None),
        Error::Database(e) => (
            match e.as_database_error().and_then(|e| e.code()).as_deref() {
                Some("P0001") => "RaiseException",
                Some("23505") => "UniqueViolation",
                _ => "DatabaseError",
            },
            None,
        ),
        Error::Json(_) => ("JSONDecodeError", None),
        other => panic!("unexpected native error {other}"),
    };
    json!({"error":{"kind":kind,"message":message}})
}
async fn identities(pool: &PgPool) -> BTreeMap<String, String> {
    sqlx::query_as::<_, (String, String)>(
        "SELECT id::text,symbol||'/'||horizon||'/'||model_id FROM forecasts",
    )
    .fetch_all(pool)
    .await
    .expect("identities")
    .into_iter()
    .collect()
}
fn normalize(value: &mut Value, ids: &BTreeMap<String, String>) {
    match value {
        Value::Array(a) => {
            for v in a {
                normalize(v, ids)
            }
        }
        Value::Object(o) => {
            for v in o.values_mut() {
                normalize(v, ids)
            }
        }
        Value::String(s) => {
            if let Some(key) = ids.get(s) {
                *s = key.clone()
            }
        }
        _ => {}
    }
}
// Only the four inference scalars use the existing numerical-parity tolerance.
// Provenance, feature values, manifests and all hashes remain exact.
fn same(a: &Value, b: &Value, key: &str) -> bool {
    if a == b {
        return true;
    }
    if matches!(
        key,
        "expected_return" | "probability_positive" | "lower_return" | "upper_return"
    ) && let (Some(a), Some(b)) = (a.as_f64(), b.as_f64())
    {
        return a.is_finite() && (a - b).abs() <= 1e-11 + b.abs() * 1e-10;
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
        return matches!((DateTime::parse_from_rfc3339(a),DateTime::parse_from_rfc3339(b)),(Ok(a),Ok(b)) if a==b);
    }
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| same(a, b, key))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(k, a)| b.get(k).is_some_and(|b| same(a, b, k)))
        }
        _ => false,
    }
}
async fn enqueue(pool: &PgPool, id: Uuid) {
    let mut tx = pool.begin().await.expect("enqueue");
    sqlx::query("INSERT INTO jobs(id,kind,payload) VALUES($1,'issue','{}')")
        .bind(id)
        .execute(&mut *tx)
        .await
        .expect("job");
    sqlx::query(
        "INSERT INTO job_events(job_id,event,detail) VALUES($1,'queued','{\"kind\":\"issue\"}')",
    )
    .bind(id)
    .execute(&mut *tx)
    .await
    .expect("event");
    tx.commit().await.expect("commit");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires original Python prospective/worker oracle in disposable schemas"]
async fn existing_frozen_forecasts_are_causal_atomic_duplicate_safe_and_worker_recoverable() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let root = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(root.host_str(), Some("localhost" | "127.0.0.1"))
            && root.port() == Some(5543)
            && root.path().starts_with("/intelligence_test_rust_")
    );
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_PROSPECTIVE_ORACLE").expect("oracle")).expect("read"),
    )
    .expect("JSON");
    assert_eq!(
        fixture["legacy_implementation_hash"],
        mainkraft::prospective::LEGACY_IMPLEMENTATION_HASH.trim()
    );
    let mut discrepancies = Vec::new();
    for case in fixture["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let mode = case["mode"].as_str().expect("mode");
        let schema = case["schema"].as_str().expect("schema");
        assert!(
            schema.starts_with("native_prospective_")
                && schema
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        );
        let mut scoped = root.clone();
        scoped
            .query_pairs_mut()
            .append_pair("options", &format!("-csearch_path={schema}"));
        let pool = db::pool(scoped.as_str(), false).await.expect("pool");
        db::migrate_research(&pool)
            .await
            .expect("idempotent migration");
        let instant = case["instant"]
            .as_str()
            .expect("time")
            .parse()
            .expect("instant");
        let gate = Arc::new(Gate {
            denied: name == "worker-resource-denied",
            calls: Mutex::new(Vec::new()),
        });
        let wire = Arc::new(NoNetwork::default());
        let (forecasts, dispatch) = services(&pool, instant, gate.clone(), wire.clone());
        let mut results = Vec::new();
        if mode == "concurrent" {
            let mut callers = Vec::new();
            for _ in 0..8 {
                let f = forecasts.clone();
                callers.push(tokio::spawn(
                    async move { f.issue_existing_enrollment().await },
                ));
            }
            for caller in callers {
                results.push(caller.await.expect("caller"));
            }
        } else {
            for (i, _) in case["results"]
                .as_array()
                .expect("results")
                .iter()
                .enumerate()
            {
                if mode == "worker" {
                    if name == "worker-terminal-recovery" && i == 1 {
                        sqlx::query("DROP TRIGGER fixture_job_failure ON jobs")
                            .execute(&pool)
                            .await
                            .expect("clear fault");
                    } else {
                        enqueue(
                            &pool,
                            case["job_ids"][i]
                                .as_str()
                                .expect("job")
                                .parse()
                                .expect("UUID"),
                        )
                        .await;
                    }
                    // Reconstructing the worker here also exercises reclaim after terminal failure.
                    results.push(
                        Worker::new(pool.clone(), dispatch.clone())
                            .run_once()
                            .await
                            .map(|v| v.expect("job")),
                    );
                } else {
                    results.push(forecasts.issue_existing_enrollment().await);
                }
            }
        }
        let ids = identities(&pool).await;
        let mut actual: Vec<Value> = results
            .into_iter()
            .map(|r| match r {
                Ok(v) => json!({"result":v}),
                Err(e) => error_value(e),
            })
            .collect();
        for v in &mut actual {
            normalize(v, &ids);
        }
        if mode == "concurrent" {
            actual.sort_by_key(|v| v["result"]["issued"].as_array().map_or(0, Vec::len));
            actual.reverse();
        }
        if !same(&json!(actual), &case["results"], "") {
            discrepancies.push(
                json!({"name":name,"part":"result","actual":actual,"expected":case["results"]}),
            );
        }
        for (part, query) in fixture["queries"].as_object().expect("queries") {
            let rows: Vec<Value> = sqlx::query_scalar(&format!(
                "SELECT to_jsonb(r) FROM ({}) r",
                query.as_str().expect("query")
            ))
            .fetch_all(&pool)
            .await
            .expect("snapshot");
            let mut native = json!(rows);
            normalize(&mut native, &ids);
            if !same(&native, &case["snapshot"][part], "") {
                discrepancies.push(json!({"name":name,"part":part,"actual":native,"expected":case["snapshot"][part]}));
            }
        }
        assert_eq!(
            json!(*gate.calls.lock().expect("calls")),
            case["projections"],
            "resource gate {name}"
        );
        assert_eq!(
            wire.0.load(Ordering::SeqCst),
            0,
            "issuance must not refetch data or call brokers"
        );
        // Closing and reconstructing the service must not change frozen records or duplicate forecasts.
        drop(dispatch);
        drop(forecasts);
        pool.close().await;
        if name == "success" {
            let reopened = db::pool(scoped.as_str(), false)
                .await
                .expect("restart pool");
            let before = identities(&reopened).await;
            let (f, _) = services(&reopened, instant, gate.clone(), wire.clone());
            let result = f.issue_existing_enrollment().await.expect("restart replay");
            assert_eq!(result["issued"], json!([]));
            assert_eq!(identities(&reopened).await, before);
            reopened.close().await;
        }
        println!("{name} evaluated");
    }
    if let Ok(path) = std::env::var("RUST_PROSPECTIVE_FAILURES") {
        std::fs::write(
            path,
            serde_json::to_vec_pretty(&discrepancies).expect("JSON"),
        )
        .expect("failure evidence");
    }
    assert!(
        discrepancies.is_empty(),
        "{} prospective discrepancies; first {}",
        discrepancies.len(),
        discrepancies.first().unwrap_or(&Value::Null)
    );
    println!(
        "{} unchanged original prospective/worker scenarios; immutable enrollment, linear/HGB inference, causal deadlines, eight concurrent callers, rollback, resource refusal and terminal-transaction restart recovery passed",
        fixture["cases"].as_array().expect("cases").len()
    );
}
