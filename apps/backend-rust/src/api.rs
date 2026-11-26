//! Native API/authentication contracts. Not runnable/deployable until all routes
//! in the migration map have passed their contract tests.
use argon2::{Argon2, PasswordHash, PasswordVerifier};
use axum::{
    Json, Router,
    body::Body,
    extract::{Request, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{SecondsFormat, Utc};
use rand::RngCore;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Row};
use std::{sync::Arc, time::Duration};
use subtle::ConstantTimeEq;
use tokio::sync::Semaphore;

pub const COOKIE: &str = "intelligence_session";
pub const COOKIE_PATH: &str = "/intelligence";

#[derive(Clone)]
pub struct ApiState {
    pub chart_compression: crate::charts::Compression,
    pub feed: String,
    pub calendar: Arc<crate::calendar::Calendar>,
    pub research_clock: Arc<dyn crate::engine::Clock>,
    pub evidence_root: std::path::PathBuf,
    pub pool: PgPool,
    pub origin: String,
    pub username: String,
    pub password_hash: String,
    pub production: bool,
    pub paper_url: String,
    pub pilot_token: String,
    pub client: reqwest::Client,
    pub password_workers: Arc<Semaphore>,
    pub chart_workers: Arc<Semaphore>,
}
pub struct ApiError {
    pub status: StatusCode,
    pub detail: Value,
}
impl ApiError {
    pub fn new(status: StatusCode, message: &str) -> Self {
        Self {
            status,
            detail: json!(message),
        }
    }
}
impl From<sqlx::Error> for ApiError {
    fn from(_: sqlx::Error) -> Self {
        Self::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "The research service is unavailable. Check its local logs and database connection.",
        )
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"detail":self.detail}))).into_response()
    }
}
pub type ApiResult<T> = std::result::Result<T, ApiError>;

pub(crate) fn control_enabled(body: &Value) -> ApiResult<bool> {
    let error = |kind: &str, location: Value, message: &str, input: &Value| ApiError {
        status: StatusCode::UNPROCESSABLE_ENTITY,
        detail: json!([{"type":kind,"loc":location,"msg":message,"input":input}]),
    };
    if body.is_null() {
        return Err(error("missing", json!(["body"]), "Field required", body));
    }
    if !body.is_object() {
        return Err(error(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            body,
        ));
    }
    let Some(value) = body.get("enabled") else {
        return Err(error(
            "missing",
            json!(["body", "enabled"]),
            "Field required",
            body,
        ));
    };
    let accepted = match value {
        Value::Bool(b) => Some(*b),
        Value::Number(n) => match n.as_f64() {
            Some(0.) => Some(false),
            Some(1.) => Some(true),
            _ => None,
        },
        Value::String(s) => match s.to_ascii_lowercase().as_str() {
            "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
            "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
            _ => None,
        },
        _ => None,
    };
    accepted.ok_or_else(|| {
        let parsing = value.is_string() || value.as_f64().is_some_and(|v| v.fract() == 0.);
        error(
            if parsing { "bool_parsing" } else { "bool_type" },
            json!(["body", "enabled"]),
            if parsing {
                "Input should be a valid boolean, unable to interpret input"
            } else {
                "Input should be a valid boolean"
            },
            value,
        )
    })
}

fn token_hash(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}
fn equal(a: &str, b: &str) -> bool {
    bool::from(a.as_bytes().ct_eq(b.as_bytes()))
}
fn origin(state: &ApiState, headers: &HeaderMap) -> ApiResult<()> {
    if headers.get("origin").and_then(|v| v.to_str().ok()) != Some(&state.origin) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Request origin is not permitted",
        ));
    }
    Ok(())
}
fn cookie_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get("cookie")?
        .to_str()
        .ok()?
        .split(';')
        .filter_map(|entry| entry.trim().split_once('='))
        .filter(|(name, _)| *name == COOKIE)
        .map(|(_, value)| value.trim_matches('"'))
        .next_back()
        .filter(|v| !v.is_empty())
}
fn timestamp(t: chrono::DateTime<Utc>) -> String {
    t.to_rfc3339_opts(
        if t.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    )
}

async fn headers(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    response
        .headers_mut()
        .insert("x-frame-options", HeaderValue::from_static("DENY"));
    response
}

async fn require_session(
    State(state): State<ApiState>,
    request: Request,
    next: Next,
) -> ApiResult<Response> {
    let token = cookie_token(request.headers())
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Sign in to continue"))?;
    let row=sqlx::query("SELECT csrf_token FROM sessions WHERE token_hash=$1 AND revoked_at IS NULL AND expires_at>now()")
        .bind(token_hash(token)).fetch_optional(&state.pool).await?.ok_or_else(||ApiError::new(StatusCode::UNAUTHORIZED,"Your session has expired. Sign in again."))?;
    if !matches!(
        *request.method(),
        Method::GET | Method::HEAD | Method::OPTIONS
    ) {
        origin(&state, request.headers())?;
        let csrf: String = row.try_get("csrf_token")?;
        if !equal(
            request
                .headers()
                .get("x-csrf-token")
                .and_then(|h| h.to_str().ok())
                .unwrap_or(""),
            &csrf,
        ) {
            return Err(ApiError::new(
                StatusCode::FORBIDDEN,
                "Refresh the page before retrying this action",
            ));
        }
    }
    Ok(next.run(request).await)
}

fn field(body: &Value, name: &str, max: usize) -> ApiResult<String> {
    let value=body.get(name).and_then(Value::as_str).ok_or_else(||ApiError{status:StatusCode::UNPROCESSABLE_ENTITY,detail:json!([{"type":if body.get(name).is_none(){"missing"}else{"string_type"},"loc":["body",name],"msg":if body.get(name).is_none(){"Field required"}else{"Input should be a valid string"},"input":body.get(name).unwrap_or(body)}])})?;
    if value.chars().count() > max {
        return Err(ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            detail: json!([{"type":"string_too_long","loc":["body",name],"msg":format!("String should have at most {max} characters"),"input":value,"ctx":{"max_length":max}}]),
        });
    }
    Ok(value.into())
}

async fn login(
    State(state): State<ApiState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> ApiResult<Response> {
    let username = field(&body, "username", 100)?;
    let password = field(&body, "password", 200)?;
    origin(&state, &headers)?;
    if state.password_hash.is_empty() {
        return Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Administrator password has not been configured",
        ));
    }
    let address = token_hash("single-admin");
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(&address)
        .execute(&mut *tx)
        .await?;
    let recent:i64=sqlx::query_scalar("SELECT count(*) FROM login_attempts WHERE address_hash=$1 AND attempted_at>now()-interval '15 minutes' AND NOT succeeded").bind(&address).fetch_one(&mut *tx).await?;
    if recent >= 8 {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "Too many attempts. Try again in 15 minutes.",
        ));
    }
    let permit = state
        .password_workers
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "Authentication unavailable",
            )
        })?;
    let hash = state.password_hash.clone();
    let valid = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        PasswordHash::new(&hash).is_ok_and(|parsed| {
            Argon2::default()
                .verify_password(password.as_bytes(), &parsed)
                .is_ok()
        })
    })
    .await
    .map_err(|_| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Authentication unavailable",
        )
    })? && equal(&username, &state.username);
    sqlx::query("INSERT INTO login_attempts(address_hash,succeeded) VALUES($1,$2)")
        .bind(address)
        .bind(valid)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    if !valid {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "Username or password is incorrect",
        ));
    }
    let mut token = [0u8; 48];
    let mut csrf = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut token);
    rand::rngs::OsRng.fill_bytes(&mut csrf);
    let token = URL_SAFE_NO_PAD.encode(token);
    let csrf = URL_SAFE_NO_PAD.encode(csrf);
    let expires = Utc::now() + chrono::Duration::days(7);
    sqlx::query("INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES($1,$2,$3)")
        .bind(token_hash(&token))
        .bind(&csrf)
        .bind(expires)
        .execute(&state.pool)
        .await?;
    let mut response =
        Json(json!({"authenticated":true,"csrf_token":csrf,"expires_at":timestamp(expires)}))
            .into_response();
    let cookie = format!(
        "{COOKIE}={token}; HttpOnly; Max-Age=604800; Path={COOKIE_PATH}; SameSite=strict{}",
        if state.production { "; Secure" } else { "" }
    );
    response.headers_mut().insert(
        "set-cookie",
        HeaderValue::from_str(&cookie)
            .map_err(|_| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Session unavailable"))?,
    );
    Ok(response)
}

async fn session(State(state): State<ApiState>, headers: HeaderMap) -> ApiResult<Json<Value>> {
    let token = cookie_token(&headers)
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Sign in to continue"))?;
    let csrf:String=sqlx::query_scalar("SELECT csrf_token FROM sessions WHERE token_hash=$1 AND revoked_at IS NULL AND expires_at>now()")
        .bind(token_hash(token)).fetch_one(&state.pool).await?;
    Ok(Json(
        json!({"authenticated":true,"username":state.username,"csrf_token":csrf}),
    ))
}
async fn logout(State(state): State<ApiState>, headers: HeaderMap) -> ApiResult<Response> {
    let token = cookie_token(&headers)
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Sign in to continue"))?;
    sqlx::query("UPDATE sessions SET revoked_at=now() WHERE token_hash=$1")
        .bind(token_hash(token))
        .execute(&state.pool)
        .await?;
    let mut response = Json(json!({"authenticated":false})).into_response();
    response.headers_mut().insert(
        "set-cookie",
        HeaderValue::from_static(
            "intelligence_session=\"\"; Max-Age=0; Path=/intelligence; SameSite=lax",
        ),
    );
    Ok(response)
}
async fn health(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    sqlx::query("SELECT 1").execute(&state.pool).await?;
    Ok(Json(
        json!({"status":"ok","application":"keftrade-intelligence","execution_authority":"none"}),
    ))
}

async fn bridge(state: &ApiState, path: &str, body: Option<Value>) -> ApiResult<Response> {
    if !matches!(
        state.paper_url.as_str(),
        "http://127.0.0.1:8200" | "http://paper:8200"
    ) {
        return Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Private paper service address is invalid",
        ));
    }
    let mut request = state
        .client
        .request(
            if body.is_some() {
                Method::POST
            } else {
                Method::GET
            },
            format!("{}{path}", state.paper_url),
        )
        .header("X-Pilot-Token", &state.pilot_token)
        .timeout(Duration::from_secs(if path == "/events" { 70 } else { 45 }));
    if let Some(body) = body {
        request = request.json(&body);
    }
    let response = request.send().await.map_err(|_| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Isolated paper pilot unavailable; trading state is not assumed safe",
        )
    })?;
    let status = response.status();
    let content = response.headers().get("content-type").cloned();
    if path != "/control" && !status.is_success() {
        return Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Isolated paper pilot unavailable; trading state is not assumed safe",
        ));
    }
    let mut outgoing = Response::builder()
        .status(status)
        .header("cache-control", "no-store");
    if let Some(content) = content {
        outgoing = outgoing.header("content-type", content);
    }
    if path == "/events" {
        outgoing = outgoing.header("x-accel-buffering", "no");
    }
    outgoing
        .body(Body::from_stream(response.bytes_stream()))
        .map_err(|_| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Paper bridge unavailable"))
}
async fn paper_status(State(state): State<ApiState>) -> ApiResult<Response> {
    bridge(&state, "/status", None).await
}
async fn paper_events(State(state): State<ApiState>) -> ApiResult<Response> {
    bridge(&state, "/events", None).await
}
async fn paper_traces(State(state): State<ApiState>) -> ApiResult<Response> {
    bridge(&state, "/decision-traces", None).await
}
async fn paper_control(
    State(state): State<ApiState>,
    Json(body): Json<Value>,
) -> ApiResult<Response> {
    let enabled = control_enabled(&body)?;
    bridge(&state, "/control", Some(json!({"enabled":enabled}))).await
}
async fn chart_phase7(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(crate::report_api::phase7(&state.evidence_root).await?))
}
async fn chart_status(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(
        crate::report_api::chart_status(&state.evidence_root).await?,
    ))
}
async fn lab_results(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(
        crate::report_api::lab_results(&state.evidence_root).await?,
    ))
}

pub fn router(state: ApiState) -> Router {
    let authenticated = Router::new()
        .merge(crate::research_api::router())
        .route("/v1/auth/session", get(session))
        .route("/v1/auth/logout", post(logout))
        .route("/v1/paper/status", get(paper_status))
        .route("/v1/paper/events", get(paper_events))
        .route("/v1/paper/decision-traces", get(paper_traces))
        .route("/v1/paper/control", post(paper_control))
        .route("/v1/chart-intelligence/phase7", get(chart_phase7))
        .route("/v1/chart-intelligence/status", get(chart_status))
        .route("/v1/research-lab/results", get(lab_results))
        .route_layer(middleware::from_fn_with_state(
            state.clone(),
            require_session,
        ));
    Router::new()
        .merge(authenticated)
        .route("/health", get(health))
        .route("/v1/auth/login", post(login))
        .fallback(|| async { (StatusCode::NOT_FOUND, Json(json!({"detail":"Not Found"}))) })
        .layer(middleware::from_fn(headers))
        .with_state(state)
}

#[cfg(test)]
mod validation_tests {
    use super::*;
    #[test]
    fn control_wire_validation_matches_fastapi() {
        let cases: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/control-http-validation.json"
        ))
        .expect("HTTP oracle");
        for case in cases.as_array().expect("cases") {
            let actual = match control_enabled(&case["body"]) {
                Ok(enabled) => json!({"enabled":enabled}),
                Err(e) => json!({"detail":e.detail}),
            };
            assert_eq!(actual, case["expected"], "input {}", case["body"]);
        }
    }
}
