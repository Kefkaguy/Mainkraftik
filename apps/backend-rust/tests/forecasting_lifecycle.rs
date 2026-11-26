use chrono::{DateTime, Utc};
use mainkraft::{calendar::Calendar, db, engine::Clock, forecasting::Forecaster};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicI64, Ordering},
};
use uuid::Uuid;
struct TestClock(AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> DateTime<Utc> {
        DateTime::from_timestamp(self.0.load(Ordering::SeqCst), 0).expect("time")
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
#[tokio::test]
#[ignore = "requires original Python isolated workflow oracle"]
async fn original_forecast_issuance_and_resolution_are_atomic_causal_and_restart_safe() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let u = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(u.host_str(), Some("localhost" | "127.0.0.1"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool)
        .await
        .expect("idempotent migration");
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_FORECAST_ORACLE").expect("oracle")).expect("oracle"),
    )
    .expect("JSON");
    let clock = Arc::new(TestClock(AtomicI64::new(0)));
    let service = Arc::new(
        Forecaster::new(
            pool.clone(),
            Arc::new(Calendar::embedded().expect("calendar")),
            clock.clone(),
            "sip".into(),
        )
        .expect("forecaster"),
    );
    for case in fixture["cases"].as_array().expect("cases") {
        let instant = case["instant"]
            .as_str()
            .expect("instant")
            .parse::<DateTime<Utc>>()
            .expect("time");
        clock.0.store(instant.timestamp(), Ordering::SeqCst);
        let mut actual = match case["operation"].as_str().expect("operation") {
            "issue" => service.issue().await,
            "resolve" => service.resolve().await,
            "detection" => service.resolve_detections().await,
            _ => panic!("operation"),
        }
        .expect("native workflow");
        if case["operation"] == "issue" {
            for row in actual["issued"].as_array_mut().expect("issued") {
                assert!(row["id"].as_str().expect("UUID").parse::<Uuid>().is_ok());
                row.as_object_mut().expect("object").remove("id");
            }
        } else if case["operation"] == "resolve" {
            for id in actual["resolved"].as_array_mut().expect("resolved") {
                let key: String = sqlx::query_scalar(
                    "SELECT symbol||'/'||horizon||'/'||model_id FROM forecasts WHERE id=$1",
                )
                .bind(id.as_str().expect("id").parse::<Uuid>().expect("UUID"))
                .fetch_one(&pool)
                .await
                .expect("forecast key");
                *id = json!(key);
            }
        }
        assert!(
            equivalent(&actual, &case["result"]),
            "{} {}\n{actual}\n{}",
            case["operation"],
            case["instant"],
            case["result"]
        );
    }
    for (name, query) in fixture["queries"].as_object().expect("queries") {
        let rows: Vec<Value> = sqlx::query_scalar(&format!(
            "SELECT to_jsonb(r) FROM ({}) r",
            query.as_str().expect("query")
        ))
        .fetch_all(&pool)
        .await
        .expect("snapshot");
        assert!(
            equivalent(&json!(rows), &fixture["snapshot"][name]),
            "{name} state differs\n{}\n{}",
            json!(rows),
            fixture["snapshot"][name]
        );
    }
    let tiny:f64=sqlx::query_scalar("SELECT o.realized_return FROM forecast_outcomes o JOIN forecasts f ON f.id=o.forecast_id WHERE f.symbol='MSI' AND f.horizon=1").fetch_one(&pool).await.expect("tiny exact return");
    assert_eq!(
        tiny.to_bits(),
        1e-21f64.to_bits(),
        "decimal-first arithmetic cannot collapse to zero"
    );
    let corrected:i64=sqlx::query_scalar("SELECT count(*) FROM forecast_outcomes o JOIN forecasts f ON f.id=o.forecast_id WHERE f.symbol='DONE'").fetch_one(&pool).await.expect("history");
    assert_eq!(corrected, 2);
    // Eight issuers racing a fresh eligible watchlist issue one forecast pair.
    sqlx::query("INSERT INTO securities VALUES('NEW','concurrent issuance fixture',NULL,false)")
        .execute(&pool)
        .await
        .expect("fixture security");
    sqlx::query("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) SELECT 'NEW',session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash FROM daily_bars WHERE symbol='MSI'").execute(&pool).await.expect("synthetic history copy");
    sqlx::query("INSERT INTO watchlist_versions(symbols) VALUES('[\"NEW\"]')")
        .execute(&pool)
        .await
        .expect("fixture watchlist");
    clock.0.store(
        "2026-11-19T01:30:00Z"
            .parse::<DateTime<Utc>>()
            .expect("issuance")
            .timestamp(),
        Ordering::SeqCst,
    );
    let mut issuers = Vec::new();
    for _ in 0..8 {
        let s = service.clone();
        issuers.push(tokio::spawn(
            async move { s.issue().await.expect("issuer") },
        ));
    }
    let mut issued = 0;
    for issuer in issuers {
        issued += issuer.await.expect("issuer task")["issued"]
            .as_array()
            .expect("issued")
            .len();
    }
    assert_eq!(issued, 2);
    clock.0.store(
        "2026-11-25T21:15:00Z"
            .parse::<DateTime<Utc>>()
            .expect("matured")
            .timestamp(),
        Ordering::SeqCst,
    );
    assert_eq!(
        service.resolve().await.expect("new fixture outcomes")["count"],
        2
    );
    let detection = Uuid::new_v4();
    sqlx::query("INSERT INTO chart_detections SELECT (jsonb_populate_record(NULL::chart_detections,to_jsonb(d)||jsonb_build_object('id',$1::uuid)) ).* FROM chart_detections d WHERE symbol='MSI' ORDER BY created_at LIMIT 1").bind(detection).execute(&pool).await.expect("concurrent detector fixture");
    let mut detectors = Vec::new();
    for _ in 0..8 {
        let s = service.clone();
        detectors.push(tokio::spawn(async move {
            s.resolve_detections().await.expect("detection resolver")
        }));
    }
    let mut detected = 0;
    for task in detectors {
        detected += task.await.expect("detection task")["resolved"]
            .as_u64()
            .expect("resolved");
    }
    assert_eq!(detected, 2);
    // One new pending fixture is resolved once by eight concurrent resolvers.
    sqlx::query("INSERT INTO model_versions(id,kind,specification) VALUES('concurrent-fixture','fixture','{}')").execute(&pool).await.expect("fixture model");
    let pending = Uuid::new_v4();
    sqlx::query("INSERT INTO forecasts SELECT (jsonb_populate_record(NULL::forecasts,to_jsonb(f)||jsonb_build_object('id',$1::uuid,'model_id','concurrent-fixture'))).* FROM forecasts f WHERE symbol='MSI' AND horizon=1").bind(pending).execute(&pool).await.expect("pending fixture");
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let s = service.clone();
        tasks.push(tokio::spawn(async move {
            s.resolve().await.expect("concurrent resolution")
        }));
    }
    let mut total = 0;
    for task in tasks {
        total += task.await.expect("task")["count"].as_u64().expect("count");
    }
    assert_eq!(total, 1);
    assert_eq!(service.resolve().await.expect("restart replay")["count"], 0);
    // Fail on the second pending fixture: the first outcome must roll back too.
    sqlx::query("INSERT INTO model_versions(id,kind,specification) VALUES('rollback-fixture','fixture','{}')").execute(&pool).await.expect("fixture model");
    let failed = Uuid::new_v4();
    let first = Uuid::new_v4();
    sqlx::query("INSERT INTO forecasts SELECT (jsonb_populate_record(NULL::forecasts,to_jsonb(f)||jsonb_build_object('id',$1::uuid,'model_id','rollback-fixture'))).* FROM forecasts f WHERE symbol='MSI' AND horizon=1 AND model_id='historical-baseline-v1'").bind(first).execute(&pool).await.expect("first fixture");
    sqlx::query("INSERT INTO forecasts SELECT (jsonb_populate_record(NULL::forecasts,to_jsonb(f)||jsonb_build_object('id',$1::uuid,'model_id','rollback-fixture','issued_at',f.issued_at+interval '1 second'))).* FROM forecasts f WHERE symbol='MSI' AND horizon=5").bind(failed).execute(&pool).await.expect("failed fixture");
    sqlx::raw_sql("CREATE FUNCTION fail_outcome_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (SELECT horizon FROM forecasts WHERE id=NEW.forecast_id)=5 THEN RAISE EXCEPTION 'injected outcome transaction failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER outcome_fixture_fault BEFORE INSERT ON forecast_outcomes FOR EACH ROW EXECUTE FUNCTION fail_outcome_fixture();").execute(&pool).await.expect("fault injection");
    assert!(service.resolve().await.is_err());
    let written: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM forecast_outcomes WHERE forecast_id=$1 OR forecast_id=$2",
    )
    .bind(failed)
    .bind(first)
    .fetch_one(&pool)
    .await
    .expect("no partial write");
    assert_eq!(written, 0);
    sqlx::query("DROP TRIGGER outcome_fixture_fault ON forecast_outcomes")
        .execute(&pool)
        .await
        .expect("clear test fault");
    assert_eq!(service.resolve().await.expect("retry")["count"], 2);
    let immutable =
        sqlx::query("UPDATE forecast_outcomes SET realized_return=1 WHERE forecast_id=$1")
            .bind(failed)
            .execute(&pool)
            .await;
    assert!(immutable.is_err());
    sqlx::query("INSERT INTO securities VALUES('ROLL','atomic issuance fixture',NULL,false)")
        .execute(&pool)
        .await
        .expect("rollback security");
    sqlx::query("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) SELECT 'ROLL',session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash FROM daily_bars WHERE symbol='MSI'").execute(&pool).await.expect("rollback history");
    sqlx::query("INSERT INTO watchlist_versions(symbols) VALUES('[\"ROLL\"]')")
        .execute(&pool)
        .await
        .expect("rollback watchlist");
    clock.0.store(
        "2026-11-19T01:30:00Z"
            .parse::<DateTime<Utc>>()
            .expect("issuance")
            .timestamp(),
        Ordering::SeqCst,
    );
    let before: (i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM forecast_runs),(SELECT count(*) FROM dataset_manifests)",
    )
    .fetch_one(&pool)
    .await
    .expect("before transaction");
    sqlx::raw_sql("CREATE FUNCTION fail_issue_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.symbol='ROLL' AND NEW.horizon=5 THEN RAISE EXCEPTION 'injected issuance transaction failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER issue_fixture_fault BEFORE INSERT ON forecasts FOR EACH ROW EXECUTE FUNCTION fail_issue_fixture();").execute(&pool).await.expect("issue fault");
    assert!(service.issue().await.is_err());
    let after: (i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM forecast_runs),(SELECT count(*) FROM dataset_manifests)",
    )
    .fetch_one(&pool)
    .await
    .expect("after transaction");
    assert_eq!(
        after, before,
        "run and manifest must roll back with forecasts"
    );
    let written: i64 = sqlx::query_scalar("SELECT count(*) FROM forecasts WHERE symbol='ROLL'")
        .fetch_one(&pool)
        .await
        .expect("no partial forecasts");
    assert_eq!(written, 0);
    sqlx::query("DROP TRIGGER issue_fixture_fault ON forecasts")
        .execute(&pool)
        .await
        .expect("clear issue fault");
    assert_eq!(
        service.issue().await.expect("issuance retry")["issued"]
            .as_array()
            .expect("issued")
            .len(),
        2
    );
    println!("ORIGINAL_ISSUANCE_CUTOFF_BASELINE_MANIFEST_AND_RESOLUTION_PARITY=PASS");
    println!("DECIMAL_TINY_RETURNS_CORRECTION_HISTORY_REPLAY_CONCURRENCY_AND_ROLLBACK=PASS");
}
