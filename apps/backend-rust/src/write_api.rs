//! Existing watchlist and research-queue admission contracts; no trading controls.
use crate::{
    api::{ApiError, ApiResult, ApiState},
    jobs, research_api,
};
use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    routing::{post, put},
};
use serde_json::{Value, json};
use std::collections::BTreeSet;
fn error(kind: &str, location: Value, message: &str, input: &Value) -> Value {
    json!({"type":kind,"loc":location,"msg":message,"input":input})
}
fn invalid(errors: Vec<Value>) -> ApiError {
    ApiError {
        status: StatusCode::UNPROCESSABLE_ENTITY,
        detail: json!(errors),
    }
}
fn object(body: &Value) -> ApiResult<()> {
    if !body.is_object() {
        return Err(invalid(vec![error(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            body,
        )]));
    }
    Ok(())
}
pub fn watchlist_input(body: &Value) -> ApiResult<Vec<String>> {
    object(body)?;
    let Some(value) = body.get("symbols") else {
        return Err(invalid(vec![error(
            "missing",
            json!(["body", "symbols"]),
            "Field required",
            body,
        )]));
    };
    let Some(rows) = value.as_array() else {
        return Err(invalid(vec![error(
            "list_type",
            json!(["body", "symbols"]),
            "Input should be a valid list",
            value,
        )]));
    };
    if rows.is_empty() || rows.len() > 25 {
        let (kind, word, limit) = if rows.is_empty() {
            ("too_short", "at least", 1)
        } else {
            ("too_long", "at most", 25)
        };
        let mut e = error(
            kind,
            json!(["body", "symbols"]),
            &format!(
                "List should have {word} {limit} {} after validation, not {}",
                if limit == 1 { "item" } else { "items" },
                rows.len()
            ),
            value,
        );
        e["ctx"] = if rows.is_empty() {
            json!({"field_type":"List","min_length":1,"actual_length":0})
        } else {
            json!({"field_type":"List","max_length":25,"actual_length":rows.len()})
        };
        return Err(invalid(vec![e]));
    }
    let mut errors = Vec::new();
    let mut symbols = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        match row.as_str() {
            Some(s) => symbols.push(s.to_uppercase()),
            None => errors.push(error(
                "string_type",
                json!(["body", "symbols", index]),
                "Input should be a valid string",
                row,
            )),
        }
    }
    if !errors.is_empty() {
        return Err(invalid(errors));
    }
    let mut seen = BTreeSet::new();
    symbols.retain(|s| seen.insert(s.clone()));
    for symbol in &symbols {
        research_api::valid_symbol(symbol)?;
    }
    Ok(symbols)
}
pub struct Job {
    pub kind: String,
    pub symbol: Option<String>,
    pub model_id: Option<String>,
}
pub fn job_input(body: &Value) -> ApiResult<Job> {
    object(body)?;
    let mut errors = Vec::new();
    let mut fields = Vec::new();
    for name in ["kind", "symbol", "model_id"] {
        let value = match body.get(name) {
            None if name == "kind" => {
                errors.push(error(
                    "missing",
                    json!(["body", name]),
                    "Field required",
                    body,
                ));
                None
            }
            None | Some(Value::Null) if name != "kind" => None,
            Some(Value::String(s)) => Some(s.clone()),
            Some(value) => {
                errors.push(error(
                    "string_type",
                    json!(["body", name]),
                    "Input should be a valid string",
                    value,
                ));
                None
            }
            None => None,
        };
        fields.push(value);
    }
    if !errors.is_empty() {
        return Err(invalid(errors));
    }
    let job = Job {
        kind: fields[0].clone().unwrap_or_default(),
        symbol: fields[1].clone(),
        model_id: fields[2].clone(),
    };
    if !jobs::supported(&job.kind) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Unsupported job kind",
        ));
    }
    if matches!(job.kind.as_str(), "evaluate" | "vision")
        && job.symbol.as_deref().is_none_or(str::is_empty)
    {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "This job requires a company symbol",
        ));
    }
    Ok(job)
}
async fn watchlist(
    State(state): State<ApiState>,
    Json(body): Json<Value>,
) -> ApiResult<Json<Value>> {
    let symbols = watchlist_input(&body)?;
    let mut tx = state.pool.begin().await?;
    let known: Vec<String> =
        sqlx::query_scalar("SELECT symbol FROM securities WHERE NOT benchmark")
            .fetch_all(&mut *tx)
            .await?;
    if !symbols.iter().all(|s| known.contains(s)) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Add verified company identities through the registry CLI before changing the watchlist",
        ));
    }
    let row: Value = sqlx::query_scalar(
        "INSERT INTO watchlist_versions(symbols) VALUES($1) RETURNING to_jsonb(watchlist_versions)",
    )
    .bind(json!(symbols))
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(Json(row))
}
async fn new_job(
    State(state): State<ApiState>,
    Json(body): Json<Value>,
) -> ApiResult<(StatusCode, Json<Value>)> {
    let job = job_input(&body)?;
    let mut payload = json!({});
    let mut tx = state.pool.begin().await?;
    if let Some(symbol) = job.symbol.as_deref().filter(|s| !s.is_empty()) {
        research_api::valid_symbol(symbol)?;
        let known: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM securities WHERE symbol=$1)")
                .bind(symbol)
                .fetch_one(&mut *tx)
                .await?;
        if !known {
            return Err(ApiError::new(StatusCode::NOT_FOUND, "Unknown company"));
        }
        payload["symbol"] = json!(symbol);
    }
    if job.kind == "vision" {
        let model: Value =
            sqlx::query_scalar("SELECT to_jsonb(m) FROM vision_models m WHERE id=$1")
                .bind(job.model_id.as_deref())
                .fetch_optional(&mut *tx)
                .await?
                .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Unknown vision model"))?;
        let status = research_api::model_status(&model);
        if status["available"] != true {
            return Err(ApiError::new(
                StatusCode::CONFLICT,
                status["message"].as_str().unwrap_or("Model unavailable"),
            ));
        }
        payload["model_id"] = json!(job.model_id);
    }
    // All enqueue paths share admission ownership, but only API submissions
    // enforce the existing five-active-job limit; critical scheduled work retains its policy.
    sqlx::query("SELECT pg_advisory_xact_lock(753194022)")
        .execute(&mut *tx)
        .await?;
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM jobs WHERE status IN ('queued','running')")
            .fetch_one(&mut *tx)
            .await?;
    if count >= 5 {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "Research queue is full; wait for existing jobs",
        ));
    }
    let id=jobs::enqueue_tx(&mut tx,&job.kind,&payload).await.map_err(|_|ApiError::new(StatusCode::SERVICE_UNAVAILABLE,"The research service is unavailable. Check its local logs and database connection."))?;
    tx.commit().await?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({"id":id.to_string(),"status":"queued"})),
    ))
}
pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/v1/watchlist", put(watchlist))
        .route("/v1/jobs", post(new_job))
}
