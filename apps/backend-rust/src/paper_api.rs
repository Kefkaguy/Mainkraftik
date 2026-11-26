//! Private paper API, preserving the existing learning tables and wire contract.
use crate::{
    Result,
    api::{ApiError, ApiResult},
    broker::BrokerApi,
};
use axum::{
    Json, Router,
    extract::{Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response, Sse, sse::Event},
    routing::{get, post},
};
use futures::stream;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::{convert::Infallible, sync::Arc, time::Duration};
use subtle::ConstantTimeEq;

pub const VERSION: &str = "paper-learning-v1";
#[derive(Clone)]
pub struct PaperState {
    pub pool: PgPool,
    pub token: String,
    pub brokers: Vec<Arc<dyn BrokerApi>>,
}

pub async fn status(pool: &PgPool) -> Result<Value> {
    // Bounded per-account reads preserve the original Python projection and
    // avoid pathological planning of the large correlated aggregate in production.
    let mut tx = pool.begin().await?;
    sqlx::query("SET LOCAL statement_timeout='5s'")
        .execute(&mut *tx)
        .await?;
    sqlx::query("SET LOCAL jit=off").execute(&mut *tx).await?;
    let rows:Vec<Value>=sqlx::query_scalar("SELECT jsonb_build_object('id',account_id,'label',label,'account_number',account_number,'baseline_equity',baseline_equity) FROM learning_accounts ORDER BY created_at").fetch_all(&mut *tx).await?;
    let mut accounts = Vec::new();
    for row in rows {
        let id = row["id"]
            .as_str()
            .ok_or_else(|| crate::invalid("Paper account identity missing"))?;
        let snapshot:Option<Value>=sqlx::query_scalar("SELECT payload FROM learning_records WHERE account_id=$1 AND kind='snapshot' ORDER BY recorded_at DESC LIMIT 1").bind(id).fetch_optional(&mut *tx).await?;
        let counts:Value=sqlx::query_scalar("SELECT COALESCE(jsonb_object_agg(kind,n),'{}'::jsonb) FROM (SELECT kind,count(*) n FROM learning_records WHERE account_id=$1 GROUP BY kind)c").bind(id).fetch_one(&mut *tx).await?;
        let recent:Value=sqlx::query_scalar("SELECT COALESCE(jsonb_agg(to_jsonb(r)),'[]'::jsonb) FROM (SELECT kind,payload FROM learning_records WHERE account_id=$1 ORDER BY recorded_at DESC LIMIT 30)r").bind(id).fetch_one(&mut *tx).await?;
        let orders:Value=sqlx::query_scalar("SELECT COALESCE(jsonb_agg(to_jsonb(o)),'[]'::jsonb) FROM (SELECT symbol,state,body,order_payload,created_at FROM learning_intents WHERE account_id=$1 ORDER BY created_at DESC LIMIT 50)o").bind(id).fetch_one(&mut *tx).await?;
        let metrics:Value=sqlx::query_scalar("SELECT jsonb_build_object('n',count(*),'mae',avg(abs((payload->>'actual_return')::float-(payload->>'forecast_return')::float)),'directional_accuracy',avg(CASE WHEN ((payload->>'actual_return')::float>0)=((payload->>'forecast_return')::float>0) THEN 1.0 ELSE 0.0 END)) FROM learning_records WHERE account_id=$1 AND kind='outcome'").bind(id).fetch_one(&mut *tx).await?;
        accounts.push(json!({"label":row["label"],"account_number":row["account_number"],"baseline_equity":row["baseline_equity"],"snapshot":snapshot,"counts":counts,"recent":recent,"orders":orders,"forecast_metrics":metrics}));
    }
    let enabled: bool = sqlx::query_scalar("SELECT enabled FROM learning_controls WHERE id=1")
        .fetch_one(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(
        json!({"experimental":true,"paper_only":true,"policy_version":VERSION,"enabled":enabled,"accounts":accounts,
        "limits":"Broker buying power and asset permissions; no app capital/count/confidence/daily-loss limits",
        "learning":"Original account: fixed prior. Second: reproducible exploration and online residual learning from matured, pre-recorded forecasts.",
        "profitability":"Unvalidated experiment. Paper fills do not establish a live trading advantage."}),
    )
}
async fn authorization(
    State(state): State<PaperState>,
    request: Request,
    next: Next,
) -> ApiResult<Response> {
    let token = request
        .headers()
        .get("x-pilot-token")
        .and_then(|h| h.to_str().ok())
        .unwrap_or("");
    if state.token.is_empty() || !bool::from(token.as_bytes().ct_eq(state.token.as_bytes())) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Private pilot bridge authorization required",
        ));
    }
    Ok(next.run(request).await)
}
async fn health() -> Json<Value> {
    Json(json!({"status":"ok","execution":"paper_only"}))
}
async fn get_status(State(state): State<PaperState>) -> ApiResult<Json<Value>> {
    Ok(Json(status(&state.pool).await.map_err(|_| {
        ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Paper status unavailable")
    })?))
}
async fn control(
    State(state): State<PaperState>,
    Json(body): Json<Value>,
) -> ApiResult<Json<Value>> {
    let enabled = crate::api::control_enabled(&body)?;
    if enabled {
        if state.brokers.len() != 2 {
            return Err(ApiError::new(
                StatusCode::CONFLICT,
                "Distinct pinned paper accounts required",
            ));
        }
        let mut ids = std::collections::BTreeSet::new();
        for broker in &state.brokers {
            let a = broker.account().await.map_err(|_| {
                ApiError::new(
                    StatusCode::CONFLICT,
                    "Active unblocked paper accounts required",
                )
            })?;
            if a["status"] != "ACTIVE"
                || a["account_blocked"].as_bool() == Some(true)
                || a["trading_blocked"].as_bool() == Some(true)
            {
                return Err(ApiError::new(
                    StatusCode::CONFLICT,
                    "Active unblocked paper accounts required",
                ));
            }
            let id = a["id"]
                .as_str()
                .filter(|id| !id.is_empty())
                .ok_or_else(|| {
                    ApiError::new(
                        StatusCode::CONFLICT,
                        "Distinct pinned paper accounts required",
                    )
                })?;
            if !ids.insert(id.to_owned()) {
                return Err(ApiError::new(
                    StatusCode::CONFLICT,
                    "Distinct pinned paper accounts required",
                ));
            }
        }
    }
    sqlx::query("UPDATE learning_controls SET enabled=$1 WHERE id=1")
        .bind(enabled)
        .execute(&state.pool)
        .await?;
    Ok(Json(
        json!({"enabled":enabled,"mode":VERSION,"positions":"Remain owned and visible; no automatic liquidation on operator pause"}),
    ))
}
async fn traces(State(state): State<PaperState>) -> ApiResult<Json<Value>> {
    let signals:Value=sqlx::query_scalar("SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (SELECT id,symbol,strategy,role,action,issued_at,payload->'decision_trace' AS trace FROM pilot_strategy_signals ORDER BY issued_at DESC,id LIMIT 100) s").fetch_one(&state.pool).await?;
    let events:Value=sqlx::query_scalar("SELECT COALESCE(jsonb_agg(to_jsonb(e)),'[]'::jsonb) FROM (SELECT created_at,kind,payload FROM pilot_events WHERE kind IN ('decision_trace','strategy_entry_gate') ORDER BY id DESC LIMIT 100) e").fetch_one(&state.pool).await?;
    Ok(Json(
        json!({"signals":signals,"events":events,"historical_policy":"Original records preserved; absent traces mean NOT_RECORDED, not passed gates"}),
    ))
}
async fn events(
    State(state): State<PaperState>,
) -> Sse<impl futures::Stream<Item = std::result::Result<Event, Infallible>>> {
    Sse::new(stream::unfold((state, 0u8), |(state, index)| async move {
        if index >= 20 {
            return None;
        }
        if index > 0 {
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
        let event = match status(&state.pool).await {
            Ok(value) => Event::default().data(value.to_string()),
            // Preserve the last valid dashboard data. An ordinary message
            // containing only an error would overwrite its trading mode.
            Err(_) => Event::default()
                .event("unavailable")
                .data("{\"detail\":\"Paper status unavailable\"}"),
        };
        Some((Ok(event), (state, index + 1)))
    }))
}
pub fn router(state: PaperState) -> Router {
    Router::new()
        .route("/status", get(get_status))
        .route("/control", post(control))
        .route("/decision-traces", get(traces))
        .route("/events", get(events))
        .route_layer(middleware::from_fn_with_state(state.clone(), authorization))
        .route("/health", get(health))
        .with_state(state)
        .fallback(|| async {
            (StatusCode::NOT_FOUND, Json(json!({"detail":"Not Found"}))).into_response()
        })
}
