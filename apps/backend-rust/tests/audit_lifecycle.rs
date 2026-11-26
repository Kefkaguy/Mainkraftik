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
struct Wire;
#[async_trait]
impl Transport for Wire {
    async fn send(&self, _: &Request) -> Result<Response> {
        panic!("evaluation must never call providers")
    }
}
#[async_trait]
impl mainkraft::official_ir::Transport for Wire {
    async fn get(&self, _: &str, _: &str) -> Result<Page> {
        panic!("evaluation must never fetch IR")
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
fn compare(path: &str, a: &Value, b: &Value) {
    if a == b {
        return;
    }
    if a.is_i64() && b.is_i64() {
        assert_eq!(a, b, "{path}");
        return;
    }
    if let (Some(a), Some(b)) = (a.as_f64(), b.as_f64()) {
        assert!((a - b).abs() <= 5e-9 * (1. + b.abs()), "{path}: {a} != {b}");
        return;
    }
    if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (i, (a, b)) in a.iter().zip(b).enumerate() {
            compare(&format!("{path}/{i}"), a, b);
        }
        return;
    }
    if let (Some(a), Some(b)) = (a.as_object(), b.as_object()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (k, b) in b {
            compare(
                &format!("{path}/{k}"),
                a.get(k).unwrap_or_else(|| panic!("{path}/{k} missing")),
                b,
            );
        }
        return;
    }
    assert_eq!(a, b, "{path}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated original Python saved-head audit oracle"]
async fn immutable_saved_head_audit_reconciles_and_survives_restart() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").unwrap();
    let url = url::Url::parse(&uri).unwrap();
    assert!(
        matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.unwrap();
    db::migrate_research(&pool).await.unwrap();
    let oracle: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_AUDIT_ORACLE").unwrap()).unwrap(),
    )
    .unwrap();
    let calendar = Arc::new(Calendar::embedded().unwrap());
    let audit = mainkraft::research_audit::Auditor::new(pool.clone(), calendar.clone());
    let mut bad = oracle["snapshots"].clone();
    bad[0]["report"]["horizons"][0]["candidate"]["mae"] = json!(1.0);
    match mainkraft::research_audit::reconstruct(&calendar, 2, bad.as_array().unwrap()).unwrap_err()
    {
        Error::Invalid(s) => assert_eq!(s, "Original prediction reconstruction does not reconcile"),
        e => panic!("unexpected {e:?}"),
    }
    // Make one archived row temporarily unavailable without changing any stored candle.
    sqlx::raw_sql("ALTER TABLE daily_bars RENAME TO audit_saved_bars; CREATE VIEW daily_bars AS SELECT * FROM audit_saved_bars WHERE id<>1;").execute(&pool).await.unwrap();
    match audit.run().await.unwrap_err() {
        Error::Invalid(s) => assert_eq!(s, "Frozen audit dataset identity mismatch"),
        e => panic!("unexpected {e:?}"),
    }
    sqlx::raw_sql("DROP VIEW daily_bars; ALTER TABLE audit_saved_bars RENAME TO daily_bars;")
        .execute(&pool)
        .await
        .unwrap();
    sqlx::raw_sql("CREATE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture audit publication failure'; END $$; CREATE TRIGGER fail_audit BEFORE INSERT ON research_audits FOR EACH ROW EXECUTE FUNCTION fail_audit();").execute(&pool).await.unwrap();
    assert!(audit.run().await.is_err());
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM research_audits")
            .fetch_one(&pool)
            .await
            .unwrap(),
        2
    );
    sqlx::query("DROP TRIGGER fail_audit ON research_audits")
        .execute(&pool)
        .await
        .unwrap();
    let fresh = audit.run().await.unwrap();
    let mut report = fresh.clone();
    let id = report.as_object_mut().unwrap().remove("id").unwrap();
    assert_eq!(id, mainkraft::canonical::digest(&report).unwrap());
    let runtime = report
        .as_object_mut()
        .unwrap()
        .remove("native_runtime")
        .unwrap();
    assert_eq!(runtime["implementation"], "rust");
    assert_eq!(runtime["new_model_fitting"], false);
    assert_eq!(runtime["original_python_runtime_used"], false);
    assert_eq!(runtime["source_sha256"].as_object().unwrap().len(), 7);
    let mut expected = oracle["expected"].clone();
    let old = expected.as_object_mut().unwrap().remove("id").unwrap();
    assert_ne!(old, id);
    compare("audit", &report, &expected);
    assert_eq!(audit.run().await.unwrap(), fresh);
    let a = mainkraft::research_audit::Auditor::new(pool.clone(), calendar.clone());
    let b = mainkraft::research_audit::Auditor::new(pool.clone(), calendar.clone());
    let (first, second) = tokio::join!(a.run(), b.run());
    assert_eq!(first.unwrap(), fresh);
    assert_eq!(second.unwrap(), fresh);
    let clock = Arc::new(FixedClock("2026-11-20T22:00:00Z".parse().unwrap()));
    let gate = Arc::new(Gate::default());
    let delay = Arc::new(Delays::default());
    let wire = Arc::new(Wire);
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
        .unwrap(),
    );
    let ingestion = Arc::new(Ingester::new(
        pool.clone(),
        providers.clone(),
        clock.clone(),
        calendar.clone(),
        gate.clone(),
    ));
    let information = Arc::new(Information::new(
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
    let forecasts = Arc::new(Forecaster::new(pool.clone(), calendar, clock, "sip".into()).unwrap());
    let dispatch = Arc::new(NativeDispatch::new(
        pool.clone(),
        gate.clone(),
        ingestion,
        information,
        ir,
        forecasts,
    ));

    let id = Uuid::new_v4();
    enqueue(&pool, id, "audit", &json!(null)).await;
    sqlx::raw_sql("CREATE FUNCTION fail_audit_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event='completed' THEN RAISE EXCEPTION 'fixture completion failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER fail_audit_job BEFORE INSERT ON job_events FOR EACH ROW EXECUTE FUNCTION fail_audit_job();").execute(&pool).await.unwrap();
    let worker = Worker::new(pool.clone(), dispatch.clone());
    assert!(worker.run_once().await.is_err());
    drop(worker);
    sqlx::query("DROP TRIGGER fail_audit_job ON job_events")
        .execute(&pool)
        .await
        .unwrap();
    let worker = Worker::new(pool.clone(), dispatch);
    let done = worker.run_once().await.unwrap().unwrap();
    assert_eq!(done["status"], "completed");
    assert_eq!(done["result"], fresh);
    assert_eq!(
        sqlx::query_scalar::<_, i32>("SELECT attempts FROM jobs WHERE id=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap(),
        2
    );
    assert_eq!(gate.calls.lock().unwrap().len(), 2);
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM research_audits")
            .fetch_one(&pool)
            .await
            .unwrap(),
        3
    );
    for row in oracle["original_audits"].as_array().unwrap() {
        let stored: Value = sqlx::query_scalar("SELECT report FROM research_audits WHERE id=$1")
            .bind(row["id"].as_str().unwrap())
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(stored, row["report"]);
    }
    for (key, query) in oracle["queries"].as_object().unwrap() {
        let current: String = sqlx::query_scalar(query.as_str().unwrap())
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(current, oracle["before"][key], "frozen {key} changed");
    }
    pool.close().await;
}
