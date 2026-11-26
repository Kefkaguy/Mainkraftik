use async_trait::async_trait;
use mainkraft::{
    Result,
    calendar::Calendar,
    canonical, db,
    jobs::{self, Dispatch, DispatchError, Worker},
};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tokio::sync::Semaphore;
use uuid::Uuid;
struct FixtureDispatch {
    pool: PgPool,
    fixture: Value,
    calls: AtomicUsize,
    gate: Option<Arc<Semaphore>>,
}
#[async_trait]
impl Dispatch for FixtureDispatch {
    async fn execute(
        &self,
        kind: &str,
        payload: &Value,
    ) -> std::result::Result<Value, DispatchError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if let Some(gate) = &self.gate {
            gate.acquire()
                .await
                .map_err(|_| DispatchError::Unexpected("GateClosed"))?
                .forget();
        }
        let id = canonical::digest(&json!([kind, payload]))
            .map_err(|_| DispatchError::Unexpected("IdentityError"))?;
        sqlx::query("INSERT INTO research_audits(id,report) VALUES($1,$2) ON CONFLICT DO NOTHING")
            .bind(id)
            .bind(json!({"fixture":true,"payload":payload}))
            .execute(&self.pool)
            .await
            .map_err(|_| DispatchError::Unexpected("DatabaseError"))?;
        if let Some(index) = payload["case"].as_u64() {
            let case = &self.fixture["completions"][index as usize];
            return match case["failure"].as_str() {
                Some("value") => Err(DispatchError::Value(format!(
                    "Safe validation {}",
                    "界".repeat(550)
                ))),
                Some("unexpected") => Err(DispatchError::Unexpected("RuntimeError")),
                _ => Ok(case["dispatch_result"].clone()),
            };
        }
        Ok(json!({"kind":kind,"fixture":true}))
    }
}
async fn events(pool: &PgPool, id: Uuid) -> Vec<(String, Value)> {
    sqlx::query_as("SELECT event,detail FROM job_events WHERE job_id=$1 ORDER BY id")
        .bind(id)
        .fetch_all(pool)
        .await
        .expect("events")
}
async fn wait_calls(dispatch: &FixtureDispatch, count: usize) {
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        while dispatch.calls.load(Ordering::SeqCst) < count {
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("dispatch started");
}
#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated research DB"]
async fn durable_jobs_preserve_completion_priority_ownership_recovery_and_scheduler_marks()
-> Result<()> {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await?;
    db::migrate_research(&pool).await?;
    let fixture: Value = serde_json::from_str(include_str!("fixtures/job-parity.json"))?;
    let dispatch = Arc::new(FixtureDispatch {
        pool: pool.clone(),
        fixture: fixture.clone(),
        calls: AtomicUsize::new(0),
        gate: None,
    });
    let worker = Worker::new(pool.clone(), dispatch.clone());
    for (index, case) in fixture["completions"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        let id = jobs::enqueue(
            &pool,
            case["kind"].as_str().expect("kind"),
            &json!({"case":index}),
        )
        .await?;
        let mut result = worker.run_once().await?.expect("job");
        result["id"] = case["expected"]["id"].clone();
        assert_eq!(result, case["expected"]);
        let actual: Vec<_> = events(&pool, id)
            .await
            .into_iter()
            .filter(|(kind, _)| kind != "queued")
            .map(|(event, detail)| json!({"event":event,"detail":detail}))
            .collect();
        assert_eq!(json!(actual), case["events"]);
    }
    let old = jobs::enqueue(&pool, "research", &json!({"priority":"old"})).await?;
    let resolve = jobs::enqueue(&pool, "resolve", &json!({"priority":"resolve"})).await?;
    let refresh = jobs::enqueue(&pool, "refresh", &json!({"priority":"refresh"})).await?;
    let issue = jobs::enqueue(&pool, "issue", &json!({"priority":"issue"})).await?;
    for expected in [issue, refresh, resolve, old] {
        assert_eq!(
            worker.run_once().await?.expect("priority job")["id"],
            expected.to_string()
        );
    }
    assert!(worker.run_once().await?.is_none());
    // Cancellation of the HTTP/requesting task must not release ownership of ongoing work.
    let gate = Arc::new(Semaphore::new(0));
    let blocked = Arc::new(FixtureDispatch {
        pool: pool.clone(),
        fixture: fixture.clone(),
        calls: AtomicUsize::new(0),
        gate: Some(gate.clone()),
    });
    let owner = Arc::new(Worker::new(pool.clone(), blocked.clone()));
    let id = jobs::enqueue(&pool, "research", &json!({"ownership":"cancelled_caller"})).await?;
    let caller = owner.clone();
    let task = tokio::spawn(async move { caller.run_once().await });
    wait_calls(&blocked, 1).await;
    task.abort();
    match task.await {
        Err(error) => assert!(error.is_cancelled()),
        Ok(_) => panic!("caller must be cancelled"),
    }
    let other = Worker::new(pool.clone(), dispatch.clone());
    assert!(other.run_once().await?.is_none());
    assert_eq!(blocked.calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        events(&pool, id)
            .await
            .iter()
            .map(|(e, _)| e.as_str())
            .collect::<Vec<_>>(),
        vec!["queued", "started"]
    );
    gate.add_permits(1);
    assert_eq!(
        owner.drain().await?.expect("drained job")["status"],
        "completed"
    );
    assert_eq!(
        events(&pool, id)
            .await
            .iter()
            .map(|(e, _)| e.as_str())
            .collect::<Vec<_>>(),
        vec!["queued", "started", "completed"]
    );
    // An exited process leaves a durable running row but no live lease owner.
    let id = jobs::enqueue(&pool, "research", &json!({"recovery":"exited_process"})).await?;
    sqlx::query("UPDATE jobs SET status='running',attempts=1 WHERE id=$1")
        .bind(id)
        .execute(&pool)
        .await?;
    sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,'started','{}')")
        .bind(id)
        .execute(&pool)
        .await?;
    assert_eq!(
        other.run_once().await?.expect("recovered")["id"],
        id.to_string()
    );
    let attempts: i32 = sqlx::query_scalar("SELECT attempts FROM jobs WHERE id=$1")
        .bind(id)
        .fetch_one(&pool)
        .await?;
    assert_eq!(attempts, 2);
    assert_eq!(
        events(&pool, id)
            .await
            .iter()
            .map(|(e, _)| e.as_str())
            .collect::<Vec<_>>(),
        vec!["queued", "started", "recovered", "started", "completed"]
    );
    // Persistence failure after an idempotent workflow side effect cannot erase evidence.
    let payload = json!({"recovery":"completion_transaction_failure"});
    let id = jobs::enqueue(&pool, "research", &payload).await?;
    sqlx::raw_sql("CREATE FUNCTION fixture_fail_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='completed' THEN RAISE EXCEPTION 'fixture completion failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER fixture_fail_completion BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION fixture_fail_completion();").execute(&pool).await?;
    assert!(worker.run_once().await.is_err());
    let status: String = sqlx::query_scalar("SELECT status FROM jobs WHERE id=$1")
        .bind(id)
        .fetch_one(&pool)
        .await?;
    assert_eq!(status, "running");
    sqlx::query("DROP TRIGGER fixture_fail_completion ON jobs")
        .execute(&pool)
        .await?;
    let recovered = tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            if let Some(result) = other.run_once().await? {
                break Ok::<_, mainkraft::Error>(result);
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("lease release and recovery")?;
    assert_eq!(recovered["id"], id.to_string());
    let audit = canonical::digest(&json!(["research", payload]))?;
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM research_audits WHERE id=$1")
        .bind(audit)
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 1);
    assert_eq!(
        events(&pool, id)
            .await
            .iter()
            .map(|(e, _)| e.as_str())
            .collect::<Vec<_>>(),
        vec!["queued", "started", "recovered", "started", "completed"]
    );
    let calendar = Calendar::embedded()?;
    // All pure due-task windows match the captured unmodified Python scheduler.
    for case in fixture["schedule"].as_array().expect("schedule") {
        let instant = case["instant"]
            .as_str()
            .expect("instant")
            .parse()
            .expect("time");
        let due: Vec<_> = jobs::due_tasks(&calendar, instant)
            .into_iter()
            .filter(|(_, kind)| !(case["pending_refresh"] == true && *kind == "refresh"))
            .collect();
        assert_eq!(json!(due), case["tasks"]);
    }
    let at = "2026-11-18T19:00:00-05:00".parse().expect("time");
    let mut schedulers = Vec::new();
    for _ in 0..8 {
        let pool = pool.clone();
        let calendar = calendar.clone();
        schedulers.push(tokio::spawn(async move {
            jobs::schedule(&pool, &calendar, at).await
        }));
    }
    for task in schedulers {
        task.await.expect("scheduler")?;
    }
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM scheduler_marks")
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 1);
    jobs::schedule(
        &pool,
        &calendar,
        "2026-11-18T19:30:00-05:00".parse().expect("time"),
    )
    .await?;
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM scheduler_marks")
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 1); // Slow pending refresh prevents duplication.
    worker.run_once().await?.expect("scheduled refresh");
    jobs::schedule(
        &pool,
        &calendar,
        "2026-11-18T19:30:00-05:00".parse().expect("time"),
    )
    .await?;
    jobs::schedule(
        &pool,
        &calendar,
        "2026-11-18T20:35:00-05:00".parse().expect("time"),
    )
    .await?;
    jobs::schedule(
        &pool,
        &calendar,
        "2026-11-18T20:35:00-05:00".parse().expect("time"),
    )
    .await?;
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM scheduler_marks")
        .fetch_one(&pool)
        .await?;
    assert_eq!(count, 4);
    assert!(
        sqlx::query("UPDATE job_events SET detail='{}'")
            .execute(&pool)
            .await
            .is_err()
    );
    let (sender, receiver) = tokio::sync::watch::channel(true);
    worker
        .run(&calendar, &mainkraft::engine::SystemClock, receiver)
        .await?;
    drop(sender);
    let before: i64 = sqlx::query_scalar("SELECT count(*) FROM jobs WHERE status='queued'")
        .fetch_one(&pool)
        .await?;
    let (sender, receiver) = tokio::sync::watch::channel(false);
    drop(sender);
    tokio::time::timeout(
        std::time::Duration::from_secs(1),
        worker.run(&calendar, &mainkraft::engine::SystemClock, receiver),
    )
    .await
    .expect("closed supervisor channel stops without polling")?;
    let after: i64 = sqlx::query_scalar("SELECT count(*) FROM jobs WHERE status='queued'")
        .fetch_one(&pool)
        .await?;
    assert_eq!(before, after);
    // Graceful shutdown drains its already-owned job but claims no further jobs.
    struct WeekendClock;
    impl mainkraft::engine::Clock for WeekendClock {
        fn now(&self) -> chrono::DateTime<chrono::Utc> {
            "2026-11-21T12:00:00+00:00".parse().expect("weekend")
        }
    }
    let stop_gate = Arc::new(Semaphore::new(0));
    let stopping_dispatch = Arc::new(FixtureDispatch {
        pool: pool.clone(),
        fixture: fixture.clone(),
        calls: AtomicUsize::new(0),
        gate: Some(stop_gate.clone()),
    });
    let stopping = Arc::new(Worker::new(pool.clone(), stopping_dispatch.clone()));
    let (sender, receiver) = tokio::sync::watch::channel(false);
    let owned = stopping.clone();
    let owned_calendar = calendar.clone();
    let shutdown =
        tokio::spawn(async move { owned.run(&owned_calendar, &WeekendClock, receiver).await });
    wait_calls(&stopping_dispatch, 1).await;
    sender.send(true).expect("shutdown request");
    assert!(!shutdown.is_finished());
    assert!(other.run_once().await?.is_none());
    stop_gate.add_permits(1);
    tokio::time::timeout(std::time::Duration::from_secs(2), shutdown)
        .await
        .expect("drained shutdown")
        .expect("worker task")?;
    assert_eq!(stopping_dispatch.calls.load(Ordering::SeqCst), 1);
    let after: i64 = sqlx::query_scalar("SELECT count(*) FROM jobs WHERE status='queued'")
        .fetch_one(&pool)
        .await?;
    assert_eq!(after, before - 1);
    println!("ORIGINAL_COMPLETION_FAILURE_EVENT_AND_PRIORITY_PARITY=PASS");
    println!("CANCELLED_CALLER_RETAINS_ACTIVE_WORKER_LEASE=PASS");
    println!("EXITED_WORKER_AND_POST_SIDE_EFFECT_DB_FAILURE_RECOVERY=PASS");
    println!("SCHEDULER_RESTART_DEDUP_AND_PENDING_REFRESH_BACKPRESSURE=PASS");
    println!("GRACEFUL_DRAIN_AND_CLOSED_SUPERVISOR_SHUTDOWN=PASS");
    pool.close().await;
    Ok(())
}
