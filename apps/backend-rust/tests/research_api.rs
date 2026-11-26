use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use chrono::{DateTime, Duration, Utc};
use mainkraft::{
    api::{self, ApiState},
    calendar::Calendar,
    db,
    engine::Clock,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{path::PathBuf, sync::Arc};
use tokio::sync::Semaphore;
use tower::ServiceExt;
struct FixedClock(DateTime<Utc>);
impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}
fn differences(path: &str, a: &Value, b: &Value) -> Vec<String> {
    if a == b {
        return vec![];
    }
    if let (Some(a), Some(b)) = (a.as_f64(), b.as_f64())
        && (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs()))
    {
        return vec![];
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str())
        && let (Ok(a), Ok(b)) = (
            DateTime::parse_from_rfc3339(a),
            DateTime::parse_from_rfc3339(b),
        )
        && a == b
    {
        return vec![];
    }
    if let (Some(a), Some(b)) = (a.as_object(), b.as_object()) {
        let mut out = Vec::new();
        for key in a
            .keys()
            .chain(b.keys())
            .collect::<std::collections::BTreeSet<_>>()
        {
            match (a.get(key), b.get(key)) {
                (Some(a), Some(b)) => out.extend(differences(&format!("{path}/{key}"), a, b)),
                _ => out.push(format!("{path}/{key}: key presence differs")),
            }
        }
        return out;
    }
    if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
        if a.len() != b.len() {
            return vec![format!("{path}: array length {} != {}", a.len(), b.len())];
        }
        return a
            .iter()
            .zip(b)
            .enumerate()
            .flat_map(|(i, (a, b))| differences(&format!("{path}/{i}"), a, b))
            .collect();
    }
    vec![format!("{path}: {a} != {b}")]
}
#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py original seeded Python API oracle"]
async fn existing_company_workspace_forecast_research_and_monitoring_contracts_match_python() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let target = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(target.host_str(), Some("localhost" | "127.0.0.1"))
            && target.port() == Some(5543)
            && target.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool)
        .await
        .expect("idempotent native schema");
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_RESEARCH_API_ORACLE").expect("oracle path"))
            .expect("oracle"),
    )
    .expect("JSON");
    let at = fixture["instant"]
        .as_str()
        .expect("instant")
        .parse()
        .expect("time");
    let token = "isolated-research-api-session";
    let hash = hex::encode(Sha256::digest(token.as_bytes()));
    sqlx::query(
        "INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES($1,'fixture-csrf',$2)",
    )
    .bind(hash)
    .bind(Utc::now() + Duration::hours(1))
    .execute(&pool)
    .await
    .expect("session");
    let temporary = tempfile::tempdir().expect("no production evidence");
    let state = ApiState {
        chart_compression: fixture["png_compression"]
            .as_str()
            .expect("original PNG profile")
            .parse()
            .expect("verified PNG profile"),
        chart_workers: Arc::new(Semaphore::new(2)),
        feed: "sip".into(),
        calendar: Arc::new(Calendar::embedded().expect("calendar")),
        research_clock: Arc::new(FixedClock(at)),
        evidence_root: PathBuf::from(temporary.path()),
        pool: pool.clone(),
        origin: "https://mainkraft.invalid".into(),
        username: "fixture".into(),
        password_hash: "unavailable".into(),
        production: true,
        paper_url: "https://untrusted.invalid".into(),
        pilot_token: "fixture-only".into(),
        client: reqwest::Client::new(),
        password_workers: Arc::new(Semaphore::new(2)),
    };
    let app = api::router(state.clone());
    let mut failures = Vec::new();
    for case in fixture["cases"].as_array().expect("cases") {
        let path = case["path"].as_str().expect("path");
        let unauthenticated = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(path)
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        assert_eq!(
            unauthenticated.status().as_u16(),
            401,
            "{path} must require session"
        );
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(path)
                    .header("cookie", format!("intelligence_session={token}"))
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        if u64::from(response.status().as_u16()) != case["status"].as_u64().expect("status") {
            failures.push(format!(
                "{path}: status {} != {}",
                response.status(),
                case["status"]
            ));
        }
        let body = to_bytes(response.into_body(), 4 * 1024 * 1024)
            .await
            .expect("body");
        if let Some(png) = case.get("png") {
            use base64::Engine;
            let expected = base64::engine::general_purpose::STANDARD
                .decode(png.as_str().expect("PNG"))
                .expect("base64");
            if body.as_ref() != expected {
                failures.push(format!(
                    "{path}: PNG SHA {} != {} (body prefix {})",
                    hex::encode(Sha256::digest(&body)),
                    hex::encode(Sha256::digest(&expected)),
                    String::from_utf8_lossy(&body[..body.len().min(90)])
                ));
                if path.starts_with("/v1/detections/") {
                    let id = path
                        .split('/')
                        .nth(3)
                        .expect("ID")
                        .parse::<uuid::Uuid>()
                        .expect("UUID");
                    let d: Value = sqlx::query_scalar(
                        "SELECT to_jsonb(d) FROM chart_detections d WHERE id=$1",
                    )
                    .bind(id)
                    .fetch_one(&pool)
                    .await
                    .expect("detection");
                    let ids: Vec<i64> = d["payload"]["manifest"]["bar_ids"]
                        .as_array()
                        .expect("IDs")
                        .iter()
                        .map(|v| v.as_i64().expect("ID"))
                        .collect();
                    let bars:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM daily_bars WHERE id=ANY($1) ORDER BY session_date) r").bind(ids).fetch_all(&pool).await.expect("bars");
                    let chart = mainkraft::charts::render(
                        &state.calendar,
                        &bars,
                        d["cutoff"].as_str().expect("cutoff"),
                    )
                    .expect("render");
                    failures.push(format!(
                        "manifest cutoff {} != {}",
                        chart.manifest["cutoff"], d["payload"]["manifest"]["cutoff"]
                    ));
                    failures.extend(differences(
                        "archived-manifest",
                        &chart.manifest,
                        &d["payload"]["manifest"],
                    ));
                    failures.push(format!(
                        "image identity {} == {} ; input identity {} == {}",
                        chart.image_hash, d["image_hash"], chart.input_hash, d["input_hash"]
                    ));
                }
            }
        } else {
            let body: Value = serde_json::from_slice(&body).expect("JSON");
            failures.extend(differences(path, &body, &case["body"]));
        }
    }
    assert!(
        failures.is_empty(),
        "{}",
        failures
            .iter()
            .take(12)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
    // Equivalent timestamp representations are allowed; a changed absolute cutoff is not.
    let valid_chart = fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .find(|c| {
            c["path"]
                .as_str()
                .is_some_and(|p| p.starts_with("/v1/detections/"))
                && c["status"] == 200
        })
        .expect("valid archived chart")["path"]
        .as_str()
        .expect("path");
    let id = valid_chart
        .split('/')
        .nth(3)
        .expect("ID")
        .parse::<uuid::Uuid>()
        .expect("UUID");
    // A historical encoder may differ from the configured encoder. Accept
    // only exact stored base-image/input hashes, and preserve annotation bytes.
    let mut other_state = state.clone();
    other_state.chart_compression = state.chart_compression.alternative();
    let other_app = api::router(other_state);
    for case in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter(|c| {
            c["path"]
                .as_str()
                .is_some_and(|p| p.starts_with("/v1/detections/"))
        })
    {
        let response = other_app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(case["path"].as_str().unwrap())
                    .header("cookie", format!("intelligence_session={token}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            response.status().as_u16(),
            case["status"].as_u64().unwrap() as u16,
            "cross-profile archive guard"
        );
        if let Some(expected) = case["png"].as_str() {
            use base64::Engine;
            let expected = base64::engine::general_purpose::STANDARD
                .decode(expected)
                .unwrap();
            assert_eq!(
                to_bytes(response.into_body(), 4 * 1024 * 1024)
                    .await
                    .unwrap()
                    .as_ref(),
                expected,
                "exact archived encoder/annotation bytes"
            );
        }
    }
    let mismatched_id = uuid::Uuid::new_v4();
    sqlx::query("INSERT INTO chart_detections SELECT (jsonb_populate_record(NULL::chart_detections,to_jsonb(d)||jsonb_build_object('id',$2::uuid,'cutoff',d.cutoff+interval '1 second'))).* FROM chart_detections d WHERE id=$1")
        .bind(id)
        .bind(mismatched_id)
        .execute(&pool)
        .await
        .expect("append isolated inconsistent-cutoff fixture");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/v1/detections/{mismatched_id}/chart"))
                .header("cookie", format!("intelligence_session={token}"))
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(
        response.status().as_u16(),
        409,
        "changed absolute cutoff must be rejected"
    );
    // Availability-only history retains a later acquired source vintage;
    // observed delivery cuts must exclude it. This distinction matches Python.
    let all = mainkraft::research_api::bars_at(&state, "MSI", Some(at), None)
        .await
        .unwrap_or_else(|_| panic!("history unavailable"));
    let observed = mainkraft::research_api::bars_at(&state, "MSI", Some(at), Some(at))
        .await
        .unwrap_or_else(|_| panic!("observed history unavailable"));
    assert_eq!(all.last().expect("bar")["close"].as_f64(), Some(119.0));
    assert_ne!(
        all.last().expect("bar")["close"],
        observed.last().expect("bar")["close"]
    );
    let forecasts = mainkraft::research_api::forecast_history(&pool, Some("MSI"), 200)
        .await
        .unwrap_or_else(|_| panic!("forecast history"));
    assert_eq!(
        forecasts
            .iter()
            .find(|r| r["model_id"] == "fixture-model-0")
            .expect("revised outcome")["realized_return"]
            .as_f64(),
        Some(0.025)
    );
    let outcome_count: i64 = sqlx::query_scalar("SELECT count(*) FROM forecast_outcomes")
        .fetch_one(&pool)
        .await
        .expect("immutable outcomes");
    assert_eq!(outcome_count, 5);
    println!("TWELVE_RESEARCH_API_ROUTE_FAMILIES_ORIGINAL_PYTHON_HTTP_PARITY=PASS");
    println!("AUTHENTICATION_VALIDATION_HISTORICAL_LABELS_AND_LATEST_OUTCOMES=PASS");
    println!("AVAILABILITY_AND_OBSERVED_ACQUISITION_CUTS_REMAIN_DISTINCT=PASS");
    println!(
        "{}",
        json!({"responses_checked":fixture["cases"].as_array().expect("cases").len()})
    );
    pool.close().await;
}
