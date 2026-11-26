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
    prospective,
    research_dispatch::NativeDispatch,
    research_providers::{Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
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
        Err(Error::Invalid("Unexpected HTTP".into()))
    }
}
#[async_trait]
impl mainkraft::official_ir::Transport for NoNetwork {
    async fn get(&self, _: &str, _: &str) -> Result<Page> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Err(Error::Invalid("Unexpected IR".into()))
    }
}
struct NoDelay;
#[async_trait]
impl Delay for NoDelay {
    async fn wait(&self, _: u64) {
        panic!("Monitoring must not fetch provider data")
    }
}
fn dispatcher(
    pool: &PgPool,
    at: DateTime<Utc>,
    gate: Arc<Gate>,
    wire: Arc<NoNetwork>,
) -> Arc<NativeDispatch> {
    let clock = Arc::new(FixedClock(at));
    let calendar = Arc::new(Calendar::embedded().expect("calendar"));
    let delay = Arc::new(NoDelay);
    let providers = Arc::new(
        Providers::new(
            Credentials {
                key: "fixture".into(),
                secret: "fixture".into(),
                feed: "sip".into(),
                sec_user_agent: "fixture@example.invalid".into(),
            },
            wire.clone(),
            delay.clone(),
        )
        .expect("providers"),
    );
    let ingest = Arc::new(Ingester::new(
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
    let forecast =
        Arc::new(Forecaster::new(pool.clone(), calendar, clock, "sip".into()).expect("forecaster"));
    Arc::new(NativeDispatch::new(
        pool.clone(),
        gate,
        ingest,
        info,
        ir,
        forecast,
    ))
}
fn equivalent(a: &Value, b: &Value, descriptive: bool) -> bool {
    if a == b {
        return true;
    }
    if descriptive && let (Some(a), Some(b)) = (a.as_f64(), b.as_f64()) {
        return (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs()));
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
        return matches!((DateTime::parse_from_rfc3339(a),DateTime::parse_from_rfc3339(b)),(Ok(a),Ok(b)) if a==b);
    }
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equivalent(a, b, descriptive))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(k, a)| {
                    b.get(k).is_some_and(|b| {
                        equivalent(a, b, descriptive || k == "risk" || k == "reliability")
                    })
                })
        }
        _ => false,
    }
}
fn outcome(result: Result<Value>) -> Value {
    match result {
        Ok(v) => json!({"result":v}),
        Err(Error::Invalid(m)) => json!({"error":{"kind":"ValueError","message":m}}),
        Err(Error::Database(e))
            if e.as_database_error().and_then(|e| e.code()).as_deref() == Some("P0001") =>
        {
            json!({"error":{"kind":"RaiseException","message":null}})
        }
        Err(e) => panic!("unexpected native outcome {e}"),
    }
}
async fn enqueue(pool: &PgPool, id: Uuid) {
    let mut tx = pool.begin().await.expect("transaction");
    sqlx::query("INSERT INTO jobs(id,kind,payload) VALUES($1,'monitor','{}')")
        .bind(id)
        .execute(&mut *tx)
        .await
        .expect("job");
    sqlx::query(
        "INSERT INTO job_events(job_id,event,detail) VALUES($1,'queued','{\"kind\":\"monitor\"}')",
    )
    .bind(id)
    .execute(&mut *tx)
    .await
    .expect("event");
    tx.commit().await.expect("commit");
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires original Python verdict/monitor oracle in disposable local schemas"]
async fn original_monitor_and_once_only_verdict_preserve_maturity_integrity_bootstrap_and_recovery()
{
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let root = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(root.host_str(), Some("localhost" | "127.0.0.1"))
            && root.port() == Some(5543)
            && root.path().starts_with("/intelligence_test_rust_")
    );
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_VERDICT_ORACLE").expect("oracle")).expect("read"),
    )
    .expect("JSON");
    let mut failures = Vec::new();
    for case in fixture["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let mode = case["mode"].as_str().expect("mode");
        let schema = case["schema"].as_str().expect("schema");
        assert!(
            schema.starts_with("native_verdict_")
                && schema
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        );
        let mut url = root.clone();
        url.query_pairs_mut()
            .append_pair("options", &format!("-csearch_path={schema}"));
        let pool = db::pool(url.as_str(), false).await.expect("pool");
        let zone: String = sqlx::query_scalar("SHOW TimeZone")
            .fetch_one(&pool)
            .await
            .expect("time zone");
        println!(
            "{name} native database TimeZone={zone}; original={}",
            case["python_database_timezone"]
        );
        db::migrate_research(&pool).await.expect("migrate");
        let gate = Arc::new(Gate {
            denied: name == "worker-resource-denied",
            calls: Mutex::new(Vec::new()),
        });
        let wire = Arc::new(NoNetwork::default());
        let dispatch = dispatcher(
            &pool,
            case["instant"]
                .as_str()
                .expect("time")
                .parse()
                .expect("time"),
            gate.clone(),
            wire.clone(),
        );
        let mut results = Vec::new();
        let finalize = case["finalize"].as_bool().expect("finalize");
        if mode == "concurrent" {
            let mut callers = Vec::new();
            for _ in 0..8 {
                let p = pool.clone();
                callers.push(tokio::spawn(async move {
                    prospective::progress_and_verdict(&p, true).await
                }));
            }
            for caller in callers {
                let mut v = outcome(caller.await.expect("caller"));
                v["verdict_count"] = json!(
                    sqlx::query_scalar::<_, i64>("SELECT count(*) FROM shadow_verdicts")
                        .fetch_one(&pool)
                        .await
                        .expect("count")
                );
                results.push(v);
            }
        } else {
            for (i, _) in case["results"]
                .as_array()
                .expect("results")
                .iter()
                .enumerate()
            {
                if name == "verdict-failure" && i == 1 {
                    sqlx::query("DROP TRIGGER fixture_verdict_failure ON shadow_verdicts")
                        .execute(&pool)
                        .await
                        .expect("remove fault");
                }
                let result = if mode == "worker" {
                    if name == "worker-terminal-recovery" && i == 1 {
                        sqlx::query("DROP TRIGGER fixture_job_failure ON jobs")
                            .execute(&pool)
                            .await
                            .expect("remove fault");
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
                    Worker::new(pool.clone(), dispatch.clone())
                        .run_once()
                        .await
                        .map(|v| v.expect("job"))
                } else {
                    prospective::progress_and_verdict(&pool, finalize).await
                };
                let mut result = outcome(result);
                result["verdict_count"] = json!(
                    sqlx::query_scalar::<_, i64>("SELECT count(*) FROM shadow_verdicts")
                        .fetch_one(&pool)
                        .await
                        .expect("count")
                );
                results.push(result);
            }
        }
        if !equivalent(&json!(results), &case["results"], false) {
            failures.push(
                json!({"name":name,"part":"result","actual":results,"expected":case["results"]}),
            );
        }
        for (key, query) in fixture["queries"].as_object().expect("queries") {
            let rows: Vec<Value> = sqlx::query_scalar(&format!(
                "SELECT to_jsonb(r) FROM ({}) r",
                query.as_str().expect("query")
            ))
            .fetch_all(&pool)
            .await
            .expect("snapshot");
            if !equivalent(&json!(rows), &case["snapshot"][key], false) {
                failures.push(
                    json!({"name":name,"part":key,"actual":rows,"expected":case["snapshot"][key]}),
                );
            }
        }
        assert_eq!(
            json!(*gate.calls.lock().expect("calls")),
            case["projections"]
        );
        assert_eq!(wire.0.load(Ordering::SeqCst), 0);
        // New pool/service reconstruction returns the same persisted final verdict.
        let stored:Option<Value>=sqlx::query_scalar("SELECT jsonb_build_object('status',status,'final_verdict',report) FROM shadow_verdicts WHERE protocol_id=$1").bind(prospective::PROTOCOL_ID).fetch_optional(&pool).await.expect("stored");
        drop(dispatch);
        pool.close().await;
        if let Some(expected) = stored {
            let reopened = db::pool(url.as_str(), false).await.expect("restart");
            assert_eq!(
                prospective::progress_and_verdict(&reopened, true)
                    .await
                    .expect("stored replay"),
                expected
            );
            reopened.close().await;
        }
        println!("{name} evaluated");
    }
    if let Ok(path) = std::env::var("RUST_VERDICT_FAILURES") {
        std::fs::write(path, serde_json::to_vec_pretty(&failures).expect("JSON"))
            .expect("evidence");
    }
    assert!(
        failures.is_empty(),
        "{} monitor/verdict mismatches; first {}",
        failures.len(),
        failures.first().unwrap_or(&Value::Null)
    );
    println!(
        "{} original monitor/verdict scenarios, exact final bootstrap reports, original maturity/integrity rules, immutable data, once-only verdict, eight concurrent callers and terminal recovery passed",
        fixture["cases"].as_array().expect("cases").len()
    );
}
