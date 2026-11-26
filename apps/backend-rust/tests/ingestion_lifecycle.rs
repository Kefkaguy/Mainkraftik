use async_trait::async_trait;
use chrono::{DateTime, Utc};
use mainkraft::{
    Error, Result,
    calendar::Calendar,
    db,
    engine::Clock,
    ingestion::{self, Disk, Ingester, ResourceCheck, ResourceGate},
    research_providers::{Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};
use uuid::Uuid;
struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
struct CompletionClock(std::sync::atomic::AtomicI64);
impl Clock for CompletionClock {
    fn now(&self) -> DateTime<Utc> {
        DateTime::from_timestamp(self.0.load(Ordering::SeqCst), 0).expect("time")
    }
}
struct CompleteFetch {
    inner: Arc<FixtureTransport>,
    clock: Arc<CompletionClock>,
    completed: i64,
}
#[async_trait]
impl Transport for CompleteFetch {
    async fn send(&self, request: &Request) -> Result<Response> {
        let response = self.inner.send(request).await?;
        self.clock.0.store(self.completed, Ordering::SeqCst);
        Ok(response)
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
struct NoDelay;
#[async_trait]
impl Delay for NoDelay {
    async fn wait(&self, _: u64) {}
}
struct FixtureTransport {
    fixture: Value,
    calls: Mutex<usize>,
    fault: AtomicBool,
    block: AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait]
impl Transport for FixtureTransport {
    async fn send(&self, r: &Request) -> Result<Response> {
        *self.calls.lock().expect("calls") += 1;
        if self.block.load(Ordering::SeqCst) && r.url.ends_with("corporate-actions") {
            self.entered.notify_one();
            self.release.notified().await;
        }
        let (status, payload) = if r.url.ends_with("/bars") {
            (200, json!({"bars":self.fixture["raw"]}))
        } else if r.url.ends_with("corporate-actions") {
            if r.query["symbols"] == "AXON" {
                (403, Value::Null)
            } else {
                (200, self.fixture["actions"].clone())
            }
        } else if r.url.contains("/submissions/") || r.url.contains("CIK0001069183") {
            (200, json!({}))
        } else {
            let mut facts = self.fixture["facts"].clone();
            if self.fault.load(Ordering::SeqCst) {
                facts["entityName"] = json!("transaction failure fixture");
            }
            (200, facts)
        };
        Ok(Response { status, payload })
    }
}
fn equivalent(a: &Value, b: &Value) -> bool {
    if a == b {
        return true;
    }
    if let (Some(a), Some(b)) = (a.as_f64(), b.as_f64()) {
        return (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs()));
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
        return matches!((DateTime::parse_from_rfc3339(a),DateTime::parse_from_rfc3339(b)),(Ok(a),Ok(b)) if a==b);
    }
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equivalent(a, b))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(k, a)| b.get(k).is_some_and(|b| equivalent(a, b)))
        }
        _ => false,
    }
}
async fn snapshot(pool: &sqlx::PgPool) -> Value {
    let mut out = json!({});
    for (name, sql) in [
        (
            "documents",
            "SELECT provider,url,content_hash,acquired_at,payload FROM source_documents ORDER BY provider,url,content_hash",
        ),
        (
            "bars",
            "SELECT b.symbol,b.session_date,b.feed,b.adjustment,b.adjustment_asof,b.open::text,b.high::text,b.low::text,b.close::text,b.volume::text,b.available_at,b.acquired_at,b.content_hash,s.content_hash source_hash FROM daily_bars b JOIN source_documents s ON s.id=b.source_id ORDER BY b.symbol,b.session_date",
        ),
        (
            "actions",
            "SELECT a.symbol,a.action_type,a.effective_date,a.acquired_at,a.content_hash,a.payload,s.content_hash source_hash FROM corporate_actions a JOIN source_documents s ON s.id=a.source_id ORDER BY a.symbol,a.action_type,a.content_hash",
        ),
        (
            "facts",
            "SELECT (to_jsonb(f)-'id'-'source_id')||jsonb_build_object('source_hash',s.content_hash,'value',f.value::text) AS record FROM financial_facts f JOIN source_documents s ON s.id=f.source_id ORDER BY f.symbol,f.metric,f.tag,f.content_hash",
        ),
        (
            "metrics",
            "SELECT symbol,as_of,calculation_version,inputs_hash,payload FROM calculated_metrics ORDER BY symbol,inputs_hash",
        ),
        (
            "runs",
            "SELECT kind,symbols,status,result,error FROM ingest_runs ORDER BY started_at",
        ),
    ] {
        let rows: Vec<Value> = sqlx::query_scalar(&format!("SELECT to_jsonb(r) FROM ({sql}) r"))
            .fetch_all(pool)
            .await
            .expect("snapshot");
        out[name] = json!(rows);
    }
    out
}
struct FixedDisk(u64);
impl Disk for FixedDisk {
    fn free_bytes(&self) -> Result<u64> {
        Ok(self.0)
    }
}
#[tokio::test]
#[ignore = "requires isolated original Python ingestion oracle"]
async fn original_ingestion_storage_transactions_and_restart_recovery() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let target = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(target.host_str(), Some("localhost" | "127.0.0.1"))
            && target.port() == Some(5543)
            && target.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool).await.expect("native schema");
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_INGESTION_ORACLE").expect("oracle")).expect("oracle"),
    )
    .expect("JSON");
    let transport = Arc::new(FixtureTransport {
        fixture: fixture.clone(),
        calls: Mutex::new(0),
        fault: AtomicBool::new(false),
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
            transport.clone(),
            Arc::new(NoDelay),
        )
        .expect("provider"),
    );
    let gate = Arc::new(Gate::default());
    let ingester = Arc::new(Ingester::new(
        pool.clone(),
        providers,
        Arc::new(FixedClock(
            fixture["instant"]
                .as_str()
                .expect("instant")
                .parse()
                .expect("time"),
        )),
        Arc::new(Calendar::embedded().expect("calendar")),
        gate.clone(),
    ));
    let mut saved = None;
    for case in fixture["cases"].as_array().expect("cases") {
        gate.0.lock().expect("gate").clear();
        let actual = match case["operation"].as_str().expect("operation") {
            "prices" => ingester.prices(case["arg"].as_str().expect("symbol")).await,
            "financials" => {
                ingester
                    .financials(case["arg"].as_str().expect("symbol"))
                    .await
            }
            "all" => {
                let symbols: Vec<String> =
                    serde_json::from_value(case["arg"].clone()).expect("symbols");
                let result = ingester.all(&symbols).await;
                if !symbols.is_empty()
                    && let Ok(value) = &result
                {
                    saved = Some((
                        value["run_id"]
                            .as_str()
                            .expect("id")
                            .parse::<Uuid>()
                            .expect("UUID"),
                        symbols,
                        value.clone(),
                    ));
                }
                result
            }
            _ => panic!("unknown operation"),
        };
        if let Some(error) = case.get("error") {
            match actual {
                Err(Error::Invalid(reason)) => assert_eq!(reason, *error.as_str().expect("error")),
                _ => panic!("expected original refusal"),
            }
        } else {
            let mut actual =
                actual.unwrap_or_else(|e| panic!("{} {}: {e}", case["operation"], case["arg"]));
            actual.as_object_mut().expect("object").remove("run_id");
            assert!(
                equivalent(&actual, &case["result"]),
                "result {} {}\n{actual}\n{}",
                case["operation"],
                case["arg"],
                case["result"]
            );
        }
        assert_eq!(json!(*gate.0.lock().expect("gate")), case["gates"]);
    }
    let actual = snapshot(&pool).await;
    for (table, expected) in fixture["snapshot"].as_object().expect("snapshot") {
        assert!(
            equivalent(&actual[table], expected),
            "{table} persisted state differs\n{}\n{expected}",
            actual[table]
        );
    }
    let (run, symbols, result) = saved.expect("saved run");
    let calls = *transport.calls.lock().expect("calls");
    assert_eq!(
        ingester
            .resume(run, &symbols)
            .await
            .expect("completed replay"),
        result
    );
    assert_eq!(
        *transport.calls.lock().expect("calls"),
        calls,
        "finished replay must not refetch"
    );
    assert!(
        ingester.resume(run, &["AXON".into()]).await.is_err(),
        "immutable run input identity"
    );
    let restarted = Uuid::new_v4();
    sqlx::query(
        "INSERT INTO ingest_runs(id,kind,symbols,status) VALUES($1,'refresh',$2,'running')",
    )
    .bind(restarted)
    .bind(json!(symbols))
    .execute(&pool)
    .await
    .expect("interrupted run");
    let resumed = ingester
        .resume(restarted, &symbols)
        .await
        .expect("restart recovery");
    assert_eq!(resumed["results"], result["results"]);
    let recovered = snapshot(&pool).await;
    for table in ["documents", "bars", "facts", "metrics", "actions"] {
        assert!(
            equivalent(&recovered[table], &actual[table]),
            "{table} duplicated or changed after recovery"
        );
    }
    // Fail after source and revenue writes, then prove the entire financial
    // transaction rolled back and a clean retry retains exact original evidence.
    sqlx::query("INSERT INTO securities VALUES('FAIL','failure fixture','0000068505',false)")
        .execute(&pool)
        .await
        .expect("fixture security");
    sqlx::raw_sql("CREATE FUNCTION fail_native_fact() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.symbol='FAIL' AND NEW.metric='net_income' THEN RAISE EXCEPTION 'injected transaction failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER native_fact_fault BEFORE INSERT ON financial_facts FOR EACH ROW EXECUTE FUNCTION fail_native_fact();").execute(&pool).await.expect("failure injection");
    transport.fault.store(true, Ordering::SeqCst);
    assert!(matches!(
        ingester.financials("FAIL").await,
        Err(Error::Database(_))
    ));
    let failed = snapshot(&pool).await;
    assert_eq!(failed["documents"], recovered["documents"]);
    assert_eq!(failed["facts"], recovered["facts"]);
    sqlx::query("DROP TRIGGER native_fact_fault ON financial_facts")
        .execute(&pool)
        .await
        .expect("clear fixture injection");
    ingester
        .financials("FAIL")
        .await
        .expect("clean transaction retry");
    transport.fault.store(false, Ordering::SeqCst);
    // Exclusive run ownership spans provider work. Cancellation closes the lease
    // connection; resume succeeds only after that session is actually gone.
    sqlx::query("INSERT INTO securities VALUES('CRASH','interrupted transaction fixture','0000068505',false)").execute(&pool).await.expect("crash fixture");
    transport.block.store(true, Ordering::SeqCst);
    let interrupted = Uuid::new_v4();
    let owner = ingester.clone();
    let task = tokio::spawn(async move { owner.resume(interrupted, &["CRASH".into()]).await });
    tokio::time::timeout(
        std::time::Duration::from_secs(3),
        transport.entered.notified(),
    )
    .await
    .expect("provider entered");
    let committed: i64 = sqlx::query_scalar("SELECT count(*) FROM daily_bars WHERE symbol='CRASH'")
        .fetch_one(&pool)
        .await
        .expect("committed before cancellation");
    assert_eq!(
        committed, 2,
        "bars must already be committed before blocked action fetch"
    );
    assert!(
        matches!(ingester.resume(Uuid::new_v4(),&[]).await,Err(Error::Invalid(reason)) if reason=="Another research ingestion run is active")
    );
    task.abort();
    assert!(task.await.expect_err("cancelled").is_cancelled());
    transport.block.store(false, Ordering::SeqCst);
    tokio::time::timeout(std::time::Duration::from_secs(4), async {
        loop {
            match ingester.resume(interrupted, &["CRASH".into()]).await {
                Ok(value) => break value,
                Err(Error::Invalid(reason))
                    if reason == "Another research ingestion run is active" =>
                {
                    tokio::time::sleep(std::time::Duration::from_millis(10)).await
                }
                Err(e) => panic!("recovery: {e}"),
            }
        }
    })
    .await
    .expect("session released after cancellation");
    let recovered_bars: i64 =
        sqlx::query_scalar("SELECT count(*) FROM daily_bars WHERE symbol='CRASH'")
            .fetch_one(&pool)
            .await
            .expect("recovered bars");
    assert_eq!(
        recovered_bars, committed,
        "retry after partial commit cannot duplicate bars"
    );
    for case in fixture["resource_cases"].as_array().expect("resources") {
        let result = ingestion::check_limits(
            case["free"].as_u64().expect("free"),
            case["projected"].as_u64().expect("projection"),
            case["reserve"].as_f64().expect("reserve"),
            case["size"].as_u64(),
        );
        assert_eq!(
            result.err().map(|e| match e {
                Error::Invalid(reason) => reason,
                _ => panic!("gate"),
            }),
            case["error"].as_str().map(str::to_owned)
        );
    }
    ResourceGate::new(
        pool.clone(),
        Arc::new(FixedDisk(11 * 1024 * 1024 * 1024)),
        10.0,
    )
    .expect("gate")
    .check(ingestion::PRICE_PROJECTION)
    .await
    .expect("real DB size probe");
    let blocked = ResourceGate::new(pool.clone(), Arc::new(FixedDisk(0)), 10.0).expect("gate");
    assert!(blocked.check(ingestion::PRICE_PROJECTION).await.is_err());
    // Fetch starts before the close+15-minute boundary and ends exactly on it.
    // A clock sampled before completion would incorrectly omit today's bar.
    sqlx::query("INSERT INTO securities VALUES('TIME','completion-time fixture',NULL,false)")
        .execute(&pool)
        .await
        .expect("time fixture");
    let started = "2026-11-18T21:14:59Z"
        .parse::<DateTime<Utc>>()
        .expect("started");
    let completed = "2026-11-18T21:15:00Z"
        .parse::<DateTime<Utc>>()
        .expect("completed");
    let clock = Arc::new(CompletionClock(std::sync::atomic::AtomicI64::new(
        started.timestamp(),
    )));
    let completion = Arc::new(
        Providers::new(
            Credentials {
                key: "fixture-key".into(),
                secret: "fixture-secret".into(),
                feed: "sip".into(),
                sec_user_agent: "fixture@example.invalid".into(),
            },
            Arc::new(CompleteFetch {
                inner: transport.clone(),
                clock: clock.clone(),
                completed: completed.timestamp(),
            }),
            Arc::new(NoDelay),
        )
        .expect("provider"),
    );
    let timed = Ingester::new(
        pool.clone(),
        completion,
        clock,
        Arc::new(Calendar::embedded().expect("calendar")),
        gate,
    );
    assert_eq!(
        timed.prices("TIME").await.expect("completed fetch")["count"],
        2
    );
    let stored: DateTime<Utc> = sqlx::query_scalar(
        "SELECT acquired_at FROM daily_bars WHERE symbol='TIME' AND session_date='2026-11-18'",
    )
    .fetch_one(&pool)
    .await
    .expect("post-fetch bar");
    assert_eq!(stored, completed);
    let mut writers = Vec::new();
    for _ in 0..12 {
        let pool = pool.clone();
        writers.push(tokio::spawn(async move {
            let mut tx = pool.begin().await.expect("writer transaction");
            let id = ingestion::store_document(
                &mut tx,
                "concurrent_fixture",
                "https://example.invalid/fixture",
                &json!({"concurrent":true}),
                completed,
            )
            .await
            .expect("shared source");
            tx.commit().await.expect("commit");
            id
        }));
    }
    let mut ids = std::collections::BTreeSet::new();
    for writer in writers {
        ids.insert(writer.await.expect("writer"));
    }
    assert_eq!(
        ids.len(),
        1,
        "concurrent writers must return the one committed identity"
    );
    let id = *ids.first().expect("identity");
    assert!(
        sqlx::query("UPDATE source_documents SET acquired_at=now() WHERE id=$1")
            .bind(id)
            .execute(&pool)
            .await
            .is_err(),
        "original immutable-evidence trigger must remain active"
    );
    let directory = tempfile::tempdir().expect("local storage");
    let disk = ingestion::NativeDisk::new(directory.path(), Path::new(".local/data"))
        .expect("contained path");
    assert!(disk.free_bytes().expect("real disk probe") > 0);
    assert!(
        ingestion::NativeDisk::new(directory.path(), Path::new(".local/../../outside")).is_err()
    );
    println!("ORIGINAL_INGESTION_RESULTS_AND_SIX_TABLE_FAMILIES=PASS");
    println!("EXACT_DECIMALS_IMMUTABLE_SOURCE_REPLAY_ROLLBACK_AND_RESTART=PASS");
    println!("RESOURCE_GATES_AND_CANCELLATION_OWNERSHIP=PASS");
    println!("POST_FETCH_AVAILABILITY_AND_CONCURRENT_SOURCE_IDENTITY=PASS");
}
