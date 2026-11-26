mod common;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderMap, Request},
};
use mainkraft::{
    api::{self, ApiState},
    db,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use tokio::sync::Semaphore;
use tower::ServiceExt;

async fn call(
    app: &Router,
    method: &str,
    path: &str,
    body: Value,
    cookie: Option<&str>,
    csrf: Option<&str>,
    origin: Option<&str>,
) -> (u16, HeaderMap, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json");
    if let Some(cookie) = cookie {
        request = request.header("cookie", cookie);
    }
    if let Some(csrf) = csrf {
        request = request.header("x-csrf-token", csrf);
    }
    if let Some(origin) = origin {
        request = request.header("origin", origin);
    }
    let response = app
        .clone()
        .oneshot(
            request
                .body(if method == "GET" {
                    Body::empty()
                } else {
                    Body::from(body.to_string())
                })
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status().as_u16();
    let headers = response.headers().clone();
    let bytes = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("body");
    (
        status,
        headers,
        serde_json::from_slice(&bytes).expect("JSON response"),
    )
}

#[tokio::test]
#[ignore = "requires isolated research DB from scripts/test-rust-postgres.py"]
async fn authentication_preserves_python_password_sessions_csrf_and_throttle() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("isolated URI");
    let url = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.port() == Some(5543)
            && url.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool).await.expect("schema");
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/auth-python.json")).expect("Python fixture");
    let origin = "https://mainkraft.invalid";
    let evidence = tempfile::tempdir().expect("isolated evidence");
    let report_fixture = common::seed_reports(evidence.path()).await;
    let state = ApiState {
        chart_compression: mainkraft::charts::Compression::ZlibNg233,
        chart_workers: Arc::new(Semaphore::new(2)),
        feed: "sip".into(),
        calendar: Arc::new(mainkraft::calendar::Calendar::embedded().expect("calendar")),
        research_clock: Arc::new(mainkraft::engine::SystemClock),
        evidence_root: evidence.path().to_path_buf(),
        pool: pool.clone(),
        origin: origin.into(),
        username: "admin".into(),
        password_hash: fixture["password_hash"].as_str().expect("hash").into(),
        production: true,
        paper_url: "https://untrusted.invalid".into(),
        pilot_token: "test-only".into(),
        client: reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .expect("client"),
        password_workers: Arc::new(Semaphore::new(2)),
    };
    let app = api::router(state);
    for path in [
        "/v1/chart-intelligence/phase7",
        "/v1/chart-intelligence/status",
        "/v1/research-lab/results",
    ] {
        assert_eq!(
            call(&app, "GET", path, Value::Null, None, None, None)
                .await
                .0,
            401
        );
    }
    let (status, headers, _) = call(&app, "GET", "/health", Value::Null, None, None, None).await;
    assert_eq!(status, 200);
    assert_eq!(headers["cache-control"], "no-store");
    assert_eq!(
        call(
            &app,
            "GET",
            "/v1/auth/session",
            Value::Null,
            None,
            None,
            None
        )
        .await
        .0,
        401
    );
    let credentials = json!({"username":"admin","password":fixture["password"]});
    assert_eq!(
        call(
            &app,
            "POST",
            "/v1/auth/login",
            credentials.clone(),
            None,
            None,
            Some("https://evil.invalid")
        )
        .await
        .0,
        403
    );
    let (status, headers, body) = call(
        &app,
        "POST",
        "/v1/auth/login",
        credentials,
        None,
        None,
        Some(origin),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["authenticated"], true);
    let cookie_header = headers["set-cookie"].to_str().expect("cookie");
    assert!(
        cookie_header.contains("Path=/intelligence")
            && cookie_header.contains("HttpOnly")
            && cookie_header.contains("Secure")
            && cookie_header.contains("SameSite=strict")
    );
    let cookie = cookie_header.split(';').next().expect("cookie pair");
    let csrf = body["csrf_token"].as_str().expect("csrf");
    let active = call(
        &app,
        "GET",
        "/v1/auth/session",
        Value::Null,
        Some(cookie),
        None,
        None,
    )
    .await;
    assert_eq!(active.0, 200);
    assert_eq!(active.2["csrf_token"], csrf);
    for (path, key) in [
        ("/v1/chart-intelligence/phase7", "phase7"),
        ("/v1/chart-intelligence/status", "chart"),
        ("/v1/research-lab/results", "lab"),
    ] {
        let (status, headers, body) =
            call(&app, "GET", path, Value::Null, Some(cookie), None, None).await;
        assert_eq!(status, 200);
        assert_eq!(headers["cache-control"], "no-store");
        assert_eq!(body, report_fixture["expected"][key]);
    }
    assert_eq!(
        call(
            &app,
            "POST",
            "/v1/auth/logout",
            json!({}),
            Some(cookie),
            None,
            Some(origin)
        )
        .await
        .0,
        403
    );
    assert_eq!(
        call(
            &app,
            "POST",
            "/v1/auth/logout",
            json!({}),
            Some(cookie),
            Some(csrf),
            Some("https://evil.invalid")
        )
        .await
        .0,
        403
    );
    assert_eq!(
        call(
            &app,
            "GET",
            "/v1/paper/status",
            Value::Null,
            Some(cookie),
            None,
            None
        )
        .await
        .0,
        503
    ); // host rejected before HTTP
    assert_eq!(
        call(
            &app,
            "POST",
            "/v1/auth/logout",
            json!({}),
            Some(cookie),
            Some(csrf),
            Some(origin)
        )
        .await
        .0,
        200
    );
    assert_eq!(
        call(
            &app,
            "GET",
            "/v1/auth/session",
            Value::Null,
            Some(cookie),
            None,
            None
        )
        .await
        .0,
        401
    );
    println!("PYTHON_ARGON2_SESSION_COOKIE_CSRF_LOGOUT=PASS");
    // Accept a previously created Python session without rotating credentials.
    sqlx::query("INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES($1,'python-csrf',now()+interval '1 hour')")
        .bind(fixture["existing_token_hash"].as_str().expect("Python hash")).execute(&pool).await.expect("existing Python session");
    let previous = format!(
        "intelligence_session={}",
        fixture["existing_token"].as_str().expect("token")
    );
    assert_eq!(
        call(
            &app,
            "GET",
            "/v1/auth/session",
            Value::Null,
            Some(&previous),
            None,
            None
        )
        .await
        .2["csrf_token"],
        "python-csrf"
    );
    println!("EXISTING_PYTHON_SESSION=PASS");
    for _ in 0..8 {
        assert_eq!(
            call(
                &app,
                "POST",
                "/v1/auth/login",
                json!({"username":"admin","password":"wrong"}),
                None,
                None,
                Some(origin)
            )
            .await
            .0,
            401
        );
    }
    assert_eq!(
        call(
            &app,
            "POST",
            "/v1/auth/login",
            json!({"username":"admin","password":"wrong"}),
            None,
            None,
            Some(origin)
        )
        .await
        .0,
        429
    );
    let failures: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM login_attempts WHERE address_hash=$1 AND NOT succeeded",
    )
    .bind(hex::encode(Sha256::digest(b"single-admin")))
    .fetch_one(&pool)
    .await
    .expect("failed attempts persist");
    assert_eq!(failures, 8);
    println!("FAILED_ATTEMPTS_PERSIST_GLOBAL_THROTTLE=PASS");
    // Startup migrations never revoke the existing user's session.
    db::migrate_research(&pool).await.expect("repeat migrate");
    assert_eq!(
        call(
            &app,
            "GET",
            "/v1/auth/session",
            Value::Null,
            Some(&previous),
            None,
            None
        )
        .await
        .0,
        200
    );
    println!("IDEMPOTENT_SCHEMA_PRESERVES_SESSION=PASS");
    pool.close().await;
}
