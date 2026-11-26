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
#[ignore = "requires isolated PostgreSQL and original evaluation oracle"]
async fn evaluation_persistence_native_worker_and_restart_preserve_frozen_reports() {
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
        &std::fs::read(std::env::var("RUST_EVALUATION_ORACLE").unwrap()).unwrap(),
    )
    .unwrap();
    let calendar = Arc::new(Calendar::embedded().unwrap());
    let evaluator = mainkraft::research_evaluation_store::Evaluator::new(
        pool.clone(),
        calendar.clone(),
        "sip".into(),
    );
    // An access-log failure must roll back publication while preserving the independently committed manifest.
    sqlx::raw_sql("CREATE FUNCTION reject_evaluation_log() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture log failure'; END $$; CREATE TRIGGER reject_log BEFORE INSERT ON evaluation_access_log FOR EACH ROW EXECUTE FUNCTION reject_evaluation_log();").execute(&pool).await.unwrap();
    assert!(evaluator.run("MSI").await.is_err());
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM evaluation_reports")
            .fetch_one(&pool)
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM dataset_manifests")
            .fetch_one(&pool)
            .await
            .unwrap(),
        1
    );
    sqlx::query("DROP TRIGGER reject_log ON evaluation_access_log")
        .execute(&pool)
        .await
        .unwrap();
    let fresh = evaluator.run("MSI").await.unwrap();
    let runtime = &fresh["provenance"]["native_runtime"];
    assert_eq!(runtime["implementation"], "rust");
    assert_eq!(runtime["original_python_runtime_used"], false);
    assert_eq!(runtime["source_sha256"].as_object().unwrap().len(), 7);
    for hash in runtime["source_sha256"].as_object().unwrap().values() {
        assert_eq!(hash.as_str().unwrap().len(), 64);
    }
    let mut actual = fresh.clone();
    actual.as_object_mut().unwrap().remove("id");
    actual["provenance"]
        .as_object_mut()
        .unwrap()
        .remove("native_runtime");
    let mut expected = oracle["fresh"].clone();
    expected.as_object_mut().unwrap().remove("id");
    compare("fresh", &actual, &expected);
    let manifest: Value = sqlx::query_scalar(
        "SELECT jsonb_build_object('id',id,'payload',payload) FROM dataset_manifests",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(manifest, oracle["manifest"]);
    let stored: Value = sqlx::query_scalar("SELECT report FROM evaluation_reports")
        .fetch_one(&pool)
        .await
        .unwrap();
    for _ in 0..2 {
        let reused = evaluator.run("MSI").await.unwrap();
        assert_eq!(reused["id"], fresh["id"]);
        assert_eq!(reused["reused_frozen_report"], true);
        let mut expected = fresh.clone();
        expected["reused_frozen_report"] = json!(true);
        assert_eq!(reused, expected);
    }
    let (a, b, c, d) = tokio::join!(
        evaluator.run("MSI"),
        evaluator.run("MSI"),
        evaluator.run("MSI"),
        evaluator.run("MSI")
    );
    for report in [a, b, c, d] {
        assert_eq!(report.unwrap()["id"], fresh["id"]);
    }
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
    enqueue(&pool, id, "evaluate", &json!({"symbol":"MSI"})).await;
    // Crash after report reuse but before job completion commit; restart must reuse, never refit or duplicate.
    sqlx::raw_sql("CREATE FUNCTION reject_terminal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event='completed' THEN RAISE EXCEPTION 'fixture completion failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER reject_terminal BEFORE INSERT ON job_events FOR EACH ROW EXECUTE FUNCTION reject_terminal();").execute(&pool).await.unwrap();
    let worker = Worker::new(pool.clone(), dispatch.clone());
    assert!(worker.run_once().await.is_err());
    drop(worker);
    sqlx::query("DROP TRIGGER reject_terminal ON job_events")
        .execute(&pool)
        .await
        .unwrap();
    let worker = Worker::new(pool.clone(), dispatch);
    let done = worker.run_once().await.unwrap().unwrap();
    assert_eq!(done["status"], "completed");
    assert_eq!(done["result"]["id"], fresh["id"]);
    assert_eq!(
        sqlx::query_scalar::<_, i32>("SELECT attempts FROM jobs WHERE id=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM job_events WHERE event='recovered'")
            .fetch_one(&pool)
            .await
            .unwrap(),
        1
    );
    assert_eq!(gate.calls.lock().unwrap().len(), 2);
    // A corrected synthetic archive arrives without mutating any original bar/report.
    sqlx::query("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) SELECT symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at+interval '1 second',source_id,repeat('d',64) FROM daily_bars WHERE symbol='MSI' ORDER BY session_date DESC LIMIT 1").execute(&pool).await.unwrap();
    // Deterministically pause after the initial unsealed check, before CPU training.
    let mut barrier = pool.acquire().await.unwrap();
    sqlx::query("SELECT pg_advisory_lock(753194099)")
        .execute(&mut *barrier)
        .await
        .unwrap();
    sqlx::raw_sql("CREATE FUNCTION pause_manifest() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(753194099); RETURN NEW; END $$; CREATE TRIGGER pause_manifest AFTER INSERT ON dataset_manifests FOR EACH ROW EXECUTE FUNCTION pause_manifest();").execute(&pool).await.unwrap();
    let race_pool = pool.clone();
    let pending = tokio::spawn(async move {
        mainkraft::research_evaluation_store::Evaluator::new(
            race_pool,
            Arc::new(Calendar::embedded().unwrap()),
            "sip".into(),
        )
        .run("MSI")
        .await
    });
    tokio::time::timeout(std::time::Duration::from_secs(10),async {
        loop {
            let waiting:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=753194099 AND NOT granted)").fetch_one(&pool).await.unwrap();
            if waiting {break;} tokio::task::yield_now().await;
        }
    }).await.expect("evaluation paused at manifest barrier");
    sqlx::query("INSERT INTO research_plans(id,dataset_id,protected_start,specification,code_hash) VALUES('sealed',$1,'2020-01-01','{}',$2)").bind(fresh["provenance"]["dataset_id"].as_str().unwrap()).bind("a".repeat(64)).execute(&pool).await.unwrap();
    sqlx::query("SELECT pg_advisory_unlock(753194099)")
        .execute(&mut *barrier)
        .await
        .unwrap();
    drop(barrier);
    match pending.await.unwrap().unwrap_err() {
        Error::Invalid(message) => assert_eq!(
            message,
            "Original holdout became sealed; native result was not published"
        ),
        other => panic!("unexpected {other:?}"),
    }
    sqlx::query("DROP TRIGGER pause_manifest ON dataset_manifests")
        .execute(&pool)
        .await
        .unwrap();
    let sealed = evaluator.run("MSI").await.unwrap();
    let mut wanted = fresh.clone();
    wanted["reused_frozen_report"] = json!(true);
    wanted["holdout_sealed"] = json!(true);
    assert_eq!(sealed, wanted);
    match evaluator.run("AXON").await.unwrap_err() {
        Error::Invalid(message) => assert_eq!(message, oracle["missing"]),
        other => panic!("unexpected {other:?}"),
    }
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM dataset_manifests")
            .fetch_one(&pool)
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM evaluation_reports")
            .fetch_one(&pool)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        sqlx::query_scalar::<_, Value>("SELECT report FROM evaluation_reports")
            .fetch_one(&pool)
            .await
            .unwrap(),
        stored
    );
    pool.close().await;
}
