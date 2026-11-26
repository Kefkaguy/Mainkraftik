use async_trait::async_trait;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::Request,
};
use chrono::{Duration, Utc};
use mainkraft::{
    api::{self, ApiState},
    db,
    jobs::{self, Dispatch, DispatchError},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use tokio::sync::Semaphore;
use tower::ServiceExt;
struct Complete;
#[async_trait]
impl Dispatch for Complete {
    async fn execute(&self, _: &str, _: &Value) -> std::result::Result<Value, DispatchError> {
        Ok(json!({"fixture":true}))
    }
}
async fn request(
    app: &Router,
    method: &str,
    path: &str,
    body: &Value,
    authenticated: bool,
    csrf: bool,
) -> (u16, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json");
    if authenticated {
        request = request
            .header("cookie", "intelligence_session=isolated-write-api")
            .header("origin", "https://mainkraft.invalid");
    }
    if csrf {
        request = request.header("x-csrf-token", "fixture-csrf");
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::from(body.to_string())).expect("request"))
        .await
        .expect("response");
    let status = response.status().as_u16();
    let body = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body");
    (status, serde_json::from_slice(&body).expect("JSON"))
}
#[tokio::test]
#[ignore = "requires scripts/test-rust-postgres.py isolated research DB"]
async fn watchlist_and_job_http_contracts_csrf_and_atomic_queue_admission() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool).await.expect("schema");
    sqlx::query(
        "INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES($1,'fixture-csrf',$2)",
    )
    .bind(hex::encode(Sha256::digest(b"isolated-write-api")))
    .bind(Utc::now() + Duration::hours(1))
    .execute(&pool)
    .await
    .expect("session");
    sqlx::query("INSERT INTO vision_models(id,repository,revision,weight_file) VALUES('blocked','fixture','fixture','fixture')").execute(&pool).await.expect("blocked model");
    let temp = tempfile::tempdir().expect("evidence root");
    let state = ApiState {
        chart_compression: mainkraft::charts::Compression::ZlibNg233,
        chart_workers: Arc::new(Semaphore::new(2)),
        feed: "sip".into(),
        calendar: Arc::new(mainkraft::calendar::Calendar::embedded().expect("calendar")),
        research_clock: Arc::new(mainkraft::engine::SystemClock),
        evidence_root: temp.path().to_path_buf(),
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
    let app = api::router(state);
    let worker = jobs::Worker::new(pool.clone(), Arc::new(Complete));
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/write-api-validation.json")).expect("fixture");
    for (index, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        let method = case["method"].as_str().expect("method");
        let path = case["path"].as_str().expect("path");
        assert_eq!(
            request(&app, method, path, &case["input"], false, false)
                .await
                .0,
            401
        );
        assert_eq!(
            request(&app, method, path, &case["input"], true, false)
                .await
                .0,
            403
        );
        let (status, body) = request(&app, method, path, &case["input"], true, true).await;
        assert_eq!(
            u64::from(status),
            case["status"].as_u64().expect("status"),
            "case {index}: {body}"
        );
        if status >= 400 {
            assert_eq!(body, case["response"], "case {index}");
        } else if method == "PUT" {
            assert_eq!(body["symbols"], case["response"]["symbols"]);
            assert!(body["id"].as_i64().is_some_and(|id| id > 0));
            chrono::DateTime::parse_from_rfc3339(
                body["created_at"].as_str().expect("version timestamp"),
            )
            .expect("aware timestamp");
            let stored: Value = sqlx::query_scalar(
                "SELECT to_jsonb(v) FROM watchlist_versions v ORDER BY id DESC LIMIT 1",
            )
            .fetch_one(&pool)
            .await
            .expect("latest version");
            assert_eq!(body, stored);
        } else {
            assert_eq!(body["status"], "queued");
            let id = body["id"]
                .as_str()
                .expect("ID")
                .parse::<uuid::Uuid>()
                .expect("UUID");
            assert_eq!(id.get_version_num(), 4);
            let stored: (String, Value) =
                sqlx::query_as("SELECT kind,payload FROM jobs WHERE id=$1")
                    .bind(id)
                    .fetch_one(&pool)
                    .await
                    .expect("durable job");
            assert_eq!(json!([stored.0, stored.1]), case["enqueue"]);
            let queued: i64 = sqlx::query_scalar(
                "SELECT count(*) FROM job_events WHERE job_id=$1 AND event='queued'",
            )
            .bind(id)
            .fetch_one(&pool)
            .await
            .expect("queued event");
            assert_eq!(queued, 1);
            assert_eq!(
                worker
                    .run_once()
                    .await
                    .expect("complete fixture job")
                    .expect("job")["status"],
                "completed"
            );
        }
    }
    let mut attempts = Vec::new();
    for _ in 0..12 {
        let app = app.clone();
        attempts.push(tokio::spawn(async move {
            request(
                &app,
                "POST",
                "/v1/jobs",
                &json!({"kind":"refresh"}),
                true,
                true,
            )
            .await
            .0
        }));
    }
    let mut accepted = 0;
    let mut rejected = 0;
    for task in attempts {
        match task.await.expect("request task") {
            202 => accepted += 1,
            429 => rejected += 1,
            status => panic!("unexpected admission status {status}"),
        }
    }
    assert_eq!((accepted, rejected), (5, 7));
    let active: i64 =
        sqlx::query_scalar("SELECT count(*) FROM jobs WHERE status IN ('queued','running')")
            .fetch_one(&pool)
            .await
            .expect("active");
    assert_eq!(active, 5);
    assert_eq!(
        request(
            &app,
            "POST",
            "/v1/jobs",
            &json!({"kind":"refresh","symbol":"UNKNOWN"}),
            true,
            true
        )
        .await
        .0,
        404
    );
    assert_eq!(
        request(
            &app,
            "POST",
            "/v1/jobs",
            &json!({"kind":"vision","symbol":"MSI","model_id":"blocked"}),
            true,
            true
        )
        .await
        .0,
        409
    );
    assert_eq!(
        request(
            &app,
            "POST",
            "/v1/jobs",
            &json!({"kind":"unsupported"}),
            true,
            true
        )
        .await
        .0,
        422
    );
    println!("ORIGINAL_WATCHLIST_JOB_WRITE_HTTP_VALIDATION_AND_PAYLOAD_PARITY=PASS");
    println!("AUTH_CSRF_IMMUTABLE_WATCHLIST_AND_DURABLE_QUEUED_EVENTS=PASS");
    println!("TWELVE_CONCURRENT_CLIENT_SUBMISSIONS_FIVE_ACCEPTED_SEVEN_REJECTED=PASS");
    pool.close().await;
}
