//! Existing research/workspace data contracts over the preserved PostgreSQL schema.
use crate::{
    api::{ApiError, ApiResult, ApiState},
    calendar,
    engine::stamp,
    financials, monitoring,
};
use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
async fn chart_input(
    State(state): State<ApiState>,
    Path(symbol): Path<String>,
) -> ApiResult<Response> {
    valid_symbol(&symbol)?;
    let permit = state
        .chart_workers
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "Chart renderer unavailable",
            )
        })?;
    let instant = state.research_clock.now();
    let bars = bars_at(&state, &symbol, Some(instant), Some(instant)).await?;
    let rendered = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        crate::charts::render_with(
            &state.calendar,
            &bars,
            &stamp(instant),
            state.chart_compression,
        )
        .map(|r| r.png)
    })
    .await
    .map_err(|_| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Chart renderer unavailable",
        )
    })?;
    let png = rendered.map_err(|e| match e {
        crate::Error::Invalid(message) => ApiError::new(StatusCode::CONFLICT, &message),
        other => unavailable(other),
    })?;
    Ok(([("content-type", "image/png")], png).into_response())
}
async fn detection_chart(
    State(state): State<ApiState>,
    Path(key): Path<String>,
) -> ApiResult<Response> {
    let id = key.parse::<uuid::Uuid>().map_err(|_| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Invalid detection identity",
        )
    })?;
    let permit = state
        .chart_workers
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "Chart renderer unavailable",
            )
        })?;
    let detection: Value =
        sqlx::query_scalar("SELECT to_jsonb(d) FROM chart_detections d WHERE id=$1")
            .bind(id)
            .fetch_optional(&state.pool)
            .await?
            .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Detection not found"))?;
    let ids: Vec<i64> = detection["payload"]["manifest"]["bar_ids"]
        .as_array()
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "Archived chart manifest unavailable",
            )
        })?
        .iter()
        .map(|v| {
            v.as_i64().ok_or_else(|| {
                ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Archived chart manifest unavailable",
                )
            })
        })
        .collect::<ApiResult<_>>()?;
    let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM daily_bars WHERE id=ANY($1) ORDER BY session_date) r").bind(ids).fetch_all(&state.pool).await?;
    let cutoff = detection["cutoff"]
        .as_str()
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "Archived chart cutoff unavailable",
            )
        })?
        .to_owned();
    let rendered = tokio::task::spawn_blocking(move || -> crate::Result<Option<Vec<u8>>> {
        let _permit = permit;
        // PostgreSQL preserves the instant, not the writer's timezone spelling.
        // Reproduce the immutable manifest spelling only after checking its instant.
        let Some(archived_cutoff) = detection["payload"]["manifest"]["cutoff"].as_str() else {
            return Ok(None);
        };
        let (Ok(database_time), Ok(archived_time)) = (
            DateTime::parse_from_rfc3339(&cutoff),
            DateTime::parse_from_rfc3339(archived_cutoff),
        ) else {
            return Ok(None);
        };
        if database_time != archived_time {
            return Ok(None);
        }
        let mut profile = state.chart_compression;
        let mut chart =
            crate::charts::render_with(&state.calendar, &rows, archived_cutoff, profile)?;
        if chart.input_hash != detection["input_hash"] {
            return Ok(None);
        }
        if chart.image_hash != detection["image_hash"] {
            // Old records carry the exact image hash, not a platform tag. Try
            // only the other verified encoder over the same immutable pixels.
            profile = profile.alternative();
            let png = crate::charts::encode_pixels_with(&chart.pixels, profile)?;
            if hex::encode(sha2::Sha256::digest(&png)) != detection["image_hash"] {
                return Ok(None);
            }
        }
        Ok(Some(crate::charts::annotate_with(
            &mut chart.pixels,
            &detection["payload"]["boxes"],
            profile,
        )?))
    })
    .await
    .map_err(|_| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Chart renderer unavailable",
        )
    })?
    .map_err(unavailable)?;
    let png = rendered.ok_or_else(|| {
        ApiError::new(
            StatusCode::CONFLICT,
            "Archived chart renderer or input identity mismatch",
        )
    })?;
    Ok(([("content-type", "image/png")], png).into_response())
}
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::Digest;
use sqlx::PgPool;

fn unavailable(_: crate::Error) -> ApiError {
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "The research service is unavailable. Check its local logs and database connection.",
    )
}
pub fn valid_symbol(symbol: &str) -> ApiResult<()> {
    if symbol.is_empty()
        || symbol.len() > 10
        || !symbol.as_bytes()[0].is_ascii_uppercase()
        || !symbol
            .bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'.' || c == b'-')
    {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Invalid symbol",
        ));
    }
    Ok(())
}
async fn all(pool: &PgPool, sql: &str) -> ApiResult<Vec<Value>> {
    Ok(sqlx::query_scalar(sql).fetch_all(pool).await?)
}
async fn one(pool: &PgPool, sql: &str) -> ApiResult<Option<Value>> {
    Ok(sqlx::query_scalar(sql).fetch_optional(pool).await?)
}
pub async fn bars_at(
    state: &ApiState,
    symbol: &str,
    cutoff: Option<DateTime<Utc>>,
    acquired: Option<DateTime<Utc>>,
) -> ApiResult<Vec<Value>> {
    Ok(sqlx::query_scalar("WITH basis AS (SELECT max(adjustment_asof) AS d FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND ($3::timestamptz IS NULL OR available_at<=$3) AND ($4::timestamptz IS NULL OR acquired_at<=$4)) SELECT to_jsonb(r) FROM (SELECT DISTINCT ON(session_date) * FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND ($3::timestamptz IS NULL OR available_at<=$3) AND ($4::timestamptz IS NULL OR acquired_at<=$4) AND adjustment_asof=(SELECT d FROM basis) ORDER BY session_date,acquired_at DESC,id DESC) r")
        .bind(symbol).bind(&state.feed).bind(cutoff).bind(acquired).fetch_all(&state.pool).await?)
}
pub async fn forecast_history(
    pool: &PgPool,
    symbol: Option<&str>,
    limit: i64,
) -> ApiResult<Vec<Value>> {
    Ok(sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT f.*,o.realized_return,o.spy_return,o.resolved_at FROM forecasts f LEFT JOIN LATERAL (SELECT * FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true WHERE ($1::text IS NULL OR f.symbol=$1) ORDER BY f.issued_at DESC,f.symbol,f.horizon LIMIT $2) r").bind(symbol).bind(limit).fetch_all(pool).await?)
}
pub fn model_status(model: &Value) -> Value {
    if model["license_cleared"] != true
        || model["license_review_reference"]
            .as_str()
            .is_none_or(|s| s.is_empty())
        || model["weight_sha256"].as_str().is_none_or(|s| s.is_empty())
    {
        json!({"available":false,"status":"blocked_license","message":"Model unavailable — licensing review required"})
    } else if model["runtime_verified"] != true {
        json!({"available":false,"status":"blocked_runtime","message":"Model unavailable — runtime verification required"})
    } else {
        json!({"available":true,"status":"experimental","message":"Detector confidence is not return probability"})
    }
}
async fn workspace(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    let listing = one(
        &state.pool,
        "SELECT to_jsonb(r) FROM (SELECT * FROM watchlist_versions ORDER BY id DESC LIMIT 1) r",
    )
    .await?
    .ok_or_else(|| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Watchlist unavailable"))?;
    let mut companies = Vec::new();
    if let Some(symbols) = listing["symbols"].as_array() {
        for symbol in symbols.iter().filter_map(Value::as_str) {
            let mut security: Value =
                sqlx::query_scalar("SELECT to_jsonb(s) FROM securities s WHERE symbol=$1")
                    .bind(symbol)
                    .fetch_one(&state.pool)
                    .await?;
            let bars = bars_at(&state, symbol, None, None).await?;
            let facts:Value=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT max(acquired_at) acquired_at,count(*) count FROM financial_facts WHERE symbol=$1) r").bind(symbol).fetch_one(&state.pool).await?;
            let latest = bars.last();
            let change = if bars.len() > 1 {
                Some(
                    financials::number(&bars[bars.len() - 1]["close"]).map_err(unavailable)?
                        / financials::number(&bars[bars.len() - 2]["close"])
                            .map_err(unavailable)?
                        - 1.0,
                )
            } else {
                None
            };
            let extra = json!({"latest":latest,"daily_change":change,"bar_count":bars.len(),"financial_fact_count":facts["count"],"financials_acquired_at":facts["acquired_at"]});
            security
                .as_object_mut()
                .ok_or_else(|| {
                    ApiError::new(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "Company record unavailable",
                    )
                })?
                .extend(extra.as_object().cloned().unwrap_or_default());
            companies.push(security);
        }
    }
    let universe=one(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1) r").await?;
    let jobs = all(
        &state.pool,
        "SELECT to_jsonb(r) FROM (SELECT * FROM jobs ORDER BY created_at DESC LIMIT 15) r",
    )
    .await?;
    let runs = all(
        &state.pool,
        "SELECT to_jsonb(r) FROM (SELECT * FROM forecast_runs ORDER BY started_at DESC LIMIT 5) r",
    )
    .await?;
    let mut models = all(
        &state.pool,
        "SELECT to_jsonb(r) FROM (SELECT * FROM vision_models ORDER BY id) r",
    )
    .await?;
    for model in &mut models {
        let status = model_status(model);
        if let Some(model) = model.as_object_mut() {
            model.extend(status.as_object().cloned().unwrap_or_default());
        }
    }
    let (_, waiting) = state
        .calendar
        .issuance_window(state.research_clock.now())
        .map_err(unavailable)?;
    Ok(Json(
        json!({"watchlist":listing,"companies":companies,"training_universe":universe,"jobs":jobs,"forecast_runs":runs,"vision_models":models,"feed":state.feed,"generated_at":stamp(state.research_clock.now()),"issuance_status":waiting.unwrap_or("The current prospective issuance window is open"),"execution_authority":"none","schedule":{"cutoff":"20:00 America/New_York","issuance":"20:30 America/New_York"}}),
    ))
}
#[derive(Deserialize)]
struct CompanyQuery {
    as_of: Option<String>,
}
async fn company(
    State(state): State<ApiState>,
    Path(symbol): Path<String>,
    Query(query): Query<CompanyQuery>,
) -> ApiResult<Json<Value>> {
    valid_symbol(&symbol)?;
    let instant = match query.as_of {
        Some(ref text) => DateTime::parse_from_rfc3339(text)
            .map(|t| t.with_timezone(&Utc))
            .map_err(|_| {
                ApiError::new(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "as_of must be an aware timestamp no later than now",
                )
            })?,
        None => state.research_clock.now(),
    };
    if instant > state.research_clock.now() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "as_of must be an aware timestamp no later than now",
        ));
    }
    let security: Value =
        sqlx::query_scalar("SELECT to_jsonb(s) FROM securities s WHERE symbol=$1")
            .bind(&symbol)
            .fetch_optional(&state.pool)
            .await?
            .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Company is not registered"))?;
    let bars = bars_at(&state, &symbol, Some(instant), None).await?;
    let facts: Vec<Value> =
        sqlx::query_scalar("SELECT to_jsonb(f) FROM financial_facts f WHERE symbol=$1")
            .bind(&symbol)
            .fetch_all(&state.pool)
            .await?;
    let financials = financials::calculate_metrics(&facts, instant).map_err(unavailable)?;
    let (valid, reason) = monitoring::continuity(&state.calendar, &bars).map_err(unavailable)?;
    let reports:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM evaluation_reports WHERE symbol=$1 ORDER BY created_at DESC LIMIT 5) r").bind(&symbol).fetch_all(&state.pool).await?;
    let actions:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT action_type,effective_date,payload FROM corporate_actions WHERE symbol=$1 ORDER BY effective_date DESC LIMIT 20) r").bind(&symbol).fetch_all(&state.pool).await?;
    let detections:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM chart_detections WHERE symbol=$1 ORDER BY created_at DESC LIMIT 10) r").bind(&symbol).fetch_all(&state.pool).await?;
    let sources:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT id,provider,url,content_hash,acquired_at FROM source_documents WHERE id IN (SELECT source_id FROM financial_facts WHERE symbol=$1) ORDER BY acquired_at DESC LIMIT 5) r").bind(&symbol).fetch_all(&state.pool).await?;
    let history = forecast_history(&state.pool, Some(&symbol), 20000).await?;
    let experiments:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM research_experiments WHERE symbol=$1 ORDER BY horizon,candidate_id) r").bind(&symbol).fetch_all(&state.pool).await?;
    let plans = all(
        &state.pool,
        "SELECT to_jsonb(r) FROM (SELECT * FROM research_plans ORDER BY created_at DESC) r",
    )
    .await?;
    let audit=one(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM research_audits ORDER BY created_at DESC LIMIT 1) r").await?;
    let acquired = bars
        .iter()
        .map(|r| financials::timestamp(&r["acquired_at"]))
        .collect::<crate::Result<Vec<_>>>()
        .map_err(unavailable)?
        .into_iter()
        .max();
    Ok(Json(
        json!({"security":security,"bars":&bars[bars.len().saturating_sub(300)..],"financials":financials,"coverage":{"bar_count":bars.len(),"first":bars.first().map(|r|r["session_date"].clone()),"last":bars.last().map(|r|r["session_date"].clone()),"contiguous":valid,"reason":reason,"feed":state.feed,"adjustment":"split","financial_fact_count":facts.len(),"last_acquired_at":acquired.map(stamp)},"forecasts":&history[..history.len().min(400)],"reports":reports,"corporate_actions":actions,"detections":detections,"sources":sources,"reliability":monitoring::reliability(&history).map_err(unavailable)?,"risk":monitoring::risk_summary(&state.calendar,&bars).map_err(unavailable)?,"experiments":experiments,"research_plans":plans,"audit":audit}),
    ))
}
#[derive(Deserialize)]
struct SymbolQuery {
    symbol: Option<String>,
}
async fn forecasts(
    State(state): State<ApiState>,
    Query(query): Query<SymbolQuery>,
) -> ApiResult<Json<Value>> {
    if let Some(symbol) = query.symbol.as_deref().filter(|s| !s.is_empty()) {
        valid_symbol(symbol)?;
    }
    Ok(Json(
        json!({"forecasts":forecast_history(&state.pool,query.symbol.as_deref(),200).await?}),
    ))
}
async fn performance(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(
        json!({"reports":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM evaluation_reports ORDER BY created_at DESC LIMIT 50) r").await?,"universe":one(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1) r").await?,"models":all(&state.pool,"SELECT to_jsonb(r) FROM model_versions r").await?}),
    ))
}
async fn ledger(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(
        json!({"plans":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM research_plans ORDER BY created_at DESC) r").await?,"candidates":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM research_candidates ORDER BY id) r").await?,"experiments":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM research_experiments ORDER BY symbol,horizon,candidate_id) r").await?,"audits":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM research_audits ORDER BY created_at DESC LIMIT 5) r").await?}),
    ))
}
fn identity(key: &str, message: &str) -> ApiResult<()> {
    if key.len() != 64
        || !key
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, message));
    }
    Ok(())
}
async fn predictions(
    State(state): State<ApiState>,
    Path(key): Path<String>,
) -> ApiResult<Json<Value>> {
    identity(&key, "Invalid experiment identity")?;
    let experiment: Value =
        sqlx::query_scalar("SELECT to_jsonb(r) FROM research_experiments r WHERE id=$1")
            .bind(&key)
            .fetch_optional(&state.pool)
            .await?
            .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Experiment not found"))?;
    let mut rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM research_predictions WHERE experiment_id=$1 ORDER BY origin_session) r").bind(&key).fetch_all(&state.pool).await?;
    for row in &mut rows {
        row["data_cutoff"] = json!(stamp(
            calendar::cutoff_for(financials::date(&row["origin_session"]).map_err(unavailable)?)
                .map_err(unavailable)?
        ));
        row["availability"] = json!("reconstructed_historical_not_observed_delivery");
    }
    Ok(Json(
        json!({"kind":"historical_development_not_prospective","model_version":experiment["candidate_id"],"dataset_id":experiment["report"]["dataset_id"],"predictions":rows}),
    ))
}
async fn phase3_predictions(
    State(state): State<ApiState>,
    Path(key): Path<String>,
) -> ApiResult<Json<Value>> {
    identity(&key, "Invalid result identity")?;
    let predictions: Value =
        sqlx::query_scalar("SELECT predictions FROM candidate_results WHERE id=$1")
            .bind(key)
            .fetch_optional(&state.pool)
            .await?
            .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Result not found"))?;
    Ok(Json(
        json!({"kind":"historical_development_not_prospective","predictions":predictions}),
    ))
}
pub fn legacy_progress(
    protocol: Option<&Value>,
    existing: Option<&Value>,
    rows: &[Value],
) -> crate::Result<Value> {
    let Some(protocol) = protocol else {
        return Ok(json!({"status":"not_enrolled","paired_dates":0}));
    };
    if let Some(existing) = existing {
        return Ok(json!({"status":existing["status"],"final_verdict":existing["report"]}));
    }
    let mut counts = std::collections::BTreeMap::new();
    for row in rows {
        *counts
            .entry(financials::date(&row["origin_session"])?)
            .or_insert(0usize) += 1;
    }
    let dates: Vec<_> = counts
        .into_iter()
        .filter(|(_, count)| *count == 4)
        .map(|(day, _)| day)
        .collect();
    let selected: std::collections::BTreeSet<_> = dates.iter().take(126).copied().collect();
    let mut paired = Vec::new();
    for row in rows {
        if selected.contains(&financials::date(&row["origin_session"])?) {
            paired.push(row);
        }
    }
    let matured = selected.len() == 126
        && paired
            .iter()
            .all(|r| !r["realized_return"].is_null() && !r["baseline_truth"].is_null());
    Ok(if matured {
        json!({"status":"ready_for_one_final_analysis","paired_dates":126,"required_dates":126})
    } else {
        json!({"status":"inconclusive","paired_dates":dates.len(),"required_dates":126,"start_session":protocol["start_session"],"resolved_records":paired.iter().filter(|r|!r["realized_return"].is_null()).count(),"protocol":protocol["specification"]})
    })
}
async fn progress(pool: &PgPool) -> ApiResult<Value> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(753194044)")
        .execute(&mut *tx)
        .await?;
    let protocol: Option<Value> = sqlx::query_scalar(
        "SELECT to_jsonb(p) FROM shadow_protocols p WHERE id='information-shadow-v3-126'",
    )
    .fetch_optional(&mut *tx)
    .await?;
    let mut existing = None;
    let mut rows: Vec<Value> = Vec::new();
    if let Some(protocol) = &protocol {
        existing=sqlx::query_scalar("SELECT to_jsonb(v) FROM shadow_verdicts v WHERE protocol_id='information-shadow-v3-126'").fetch_optional(&mut *tx).await?;
        if existing.is_none() {
            rows=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT f.origin_session,f.symbol,f.horizon,f.expected_return,f.probability_positive,b.expected_return AS baseline_return,b.probability_positive AS baseline_probability,o.realized_return,bo.realized_return AS baseline_truth FROM forecasts f JOIN forecasts b ON b.symbol=f.symbol AND b.origin_session=f.origin_session AND b.horizon=f.horizon AND b.model_id='historical-baseline-v1' LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=b.id ORDER BY resolved_at DESC LIMIT 1) bo ON true WHERE f.model_id='information-shadow-v3' AND f.origin_session>=$1 ORDER BY f.origin_session,f.symbol,f.horizon) r").bind(financials::date(&protocol["start_session"]).map_err(unavailable)?).fetch_all(&mut *tx).await?;
        }
    }
    let result =
        legacy_progress(protocol.as_ref(), existing.as_ref(), &rows).map_err(unavailable)?;
    tx.commit().await?;
    Ok(result)
}
async fn phase3(
    State(state): State<ApiState>,
    Query(query): Query<SymbolQuery>,
) -> ApiResult<Json<Value>> {
    if let Some(symbol) = query.symbol.as_deref().filter(|s| !s.is_empty()) {
        valid_symbol(symbol)?;
    }
    let mut sources = Vec::new();
    for provider in ["alpaca_news", "official_ir"] {
        for stock in ["MSI", "AXON"] {
            let mut row:Value=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT count(*) versions,count(DISTINCT external_id) articles,min(created_at) first_created,max(created_at) last_created,max(acquired_at) acquired_at,count(*) FILTER(WHERE updated_at>created_at) revised_or_observation_dated FROM information_versions WHERE provider=$1 AND symbols ? $2) r").bind(provider).bind(stock).fetch_one(&state.pool).await?;
            row["provider"] = json!(provider);
            row["symbol"] = json!(stock);
            sources.push(row);
        }
    }
    let filings=all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT symbol,count(*) versions,count(DISTINCT accession) filings,count(*) FILTER(WHERE form LIKE '8-K%' AND items LIKE '%2.02%') earnings_filings,min(published_at) first_available,max(published_at) last_available FROM filing_events GROUP BY symbol) r").await?;
    let mut markets = Vec::new();
    for stock in ["SPY", "XLK", "XLI"] {
        let bars = bars_at(&state, stock, None, None).await?;
        markets.push(json!({"symbol":stock,"sessions":bars.len(),"first":bars.first().map(|r|r["session_date"].clone()),"last":bars.last().map(|r|r["session_date"].clone())}));
    }
    let candidates:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT id,plan_id,family,feature_group,symbol,horizon,status,report-'artifact' AS report FROM candidate_results WHERE ($1::text IS NULL OR symbol=$1) ORDER BY symbol,horizon,feature_group,family) r").bind(query.symbol.as_deref()).fetch_all(&state.pool).await?;
    Ok(Json(
        json!({"sources":sources,"filings":filings,"markets":markets,"news_coverage":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM information_fetches ORDER BY acquired_at DESC LIMIT 5) r").await?,
        "plans":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT id,created_at,code_hash,specification FROM tuning_plans ORDER BY created_at) r").await?,"trials":all(&state.pool,"SELECT to_jsonb(r) FROM (SELECT plan_id,trial_key,family,feature_group,horizon,number,status,parameters,objective FROM tuning_trials ORDER BY feature_group,family,horizon,number) r").await?,"candidates":candidates,
        "protocol":one(&state.pool,"SELECT to_jsonb(r) FROM (SELECT * FROM shadow_protocols ORDER BY created_at DESC LIMIT 1) r").await?,"progress":progress(&state.pool).await?,
        "limitations":["Historical news is a current-vintage reconstruction censored by update time; original-version completeness is unproven","IR page edit times are unverified: available only from actual acquisition, excluded from historical training","Historical SEC timestamps use as-filed accessions; this is not a historical analyst-consensus dataset","XLK and XLI are fixed context proxies; no retrospective sector/index membership assumed","All development history is exposed. Optuna results do not establish independent predictive improvement"]}),
    ))
}
async fn monitor(State(state): State<ApiState>) -> ApiResult<Json<Value>> {
    Ok(Json(
        monitoring::snapshot(
            &state.pool,
            &state.calendar,
            &state.feed,
            state.research_clock.now(),
        )
        .await
        .map_err(unavailable)?,
    ))
}
async fn job(State(state): State<ApiState>, Path(key): Path<String>) -> ApiResult<Json<Value>> {
    let id = key
        .parse::<uuid::Uuid>()
        .map_err(|_| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, "Invalid job identity"))?;
    let row: Value = sqlx::query_scalar("SELECT to_jsonb(r) FROM jobs r WHERE id=$1")
        .bind(id)
        .fetch_optional(&state.pool)
        .await?
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Job not found"))?;
    Ok(Json(row))
}
pub fn router() -> Router<ApiState> {
    Router::new()
        .merge(crate::write_api::router())
        .route("/v1/workspace", get(workspace))
        .route("/v1/companies/{symbol}", get(company))
        .route("/v1/companies/{symbol}/chart", get(chart_input))
        .route("/v1/detections/{key}/chart", get(detection_chart))
        .route("/v1/forecasts", get(forecasts))
        .route("/v1/models/performance", get(performance))
        .route("/v1/research", get(ledger))
        .route(
            "/v1/research/experiments/{key}/predictions",
            get(predictions),
        )
        .route("/v1/phase3/predictions/{key}", get(phase3_predictions))
        .route("/v1/phase3", get(phase3))
        .route("/v1/monitor", get(monitor))
        .route("/v1/jobs/{key}", get(job))
}
