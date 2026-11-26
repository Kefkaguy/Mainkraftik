//! Issuance from the existing immutable prospective enrollment only.
//! Legacy schema labels are retained; no enrollment, training, promotion or orders.
use crate::{
    Error, Result,
    calendar::{Calendar, cutoff_for},
    canonical,
    engine::stamp,
    financials::date,
    invalid,
    models::NumericModel,
    point_features::{self, Context, Row},
};
use chrono::{DateTime, Duration, Utc};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::{Arc, LazyLock};
use tokio::sync::Semaphore;
use uuid::Uuid;

pub const PROTOCOL_ID: &str = "information-shadow-v3-126";
pub const MODEL_ID: &str = "information-shadow-v3";
/// Identity of the preserved Python semantics, not a hash of the Rust source.
/// The source preservation/parity gates must pass before any production cutover.
pub const LEGACY_IMPLEMENTATION_HASH: &str =
    include_str!("../assets/legacy-information-implementation.sha256");
static FEATURE_WORKERS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| Arc::new(Semaphore::new(2)));

async fn latest(
    calendar: Arc<Calendar>,
    context: Arc<Context>,
    symbol: &'static str,
) -> Result<Vec<Row>> {
    let permit = FEATURE_WORKERS
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| invalid("Research feature workers unavailable"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        point_features::build_features(&calendar, &context, symbol, true, true)
    })
    .await
    .map_err(|_| invalid("Research feature worker failed"))?
}

fn required<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    value.get(key).ok_or_else(|| Error::Reference {
        kind: "KeyError",
        message: format!("Missing frozen artifact field {key}"),
    })
}

pub async fn issue(
    pool: &PgPool,
    calendar: Arc<Calendar>,
    instant: DateTime<Utc>,
    feed: &str,
) -> Result<Value> {
    let (origin, blocked) = calendar.issuance_window(instant)?;
    if let Some(reason) = blocked {
        return Ok(json!({"status":"skipped","reason":reason,"issued":[]}));
    }
    let origin = origin.ok_or_else(|| invalid("Missing prospective origin"))?;
    let cutoff = cutoff_for(origin)?;
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(753194043)")
        .execute(&mut *tx)
        .await?;
    let protocol: Option<Value> =
        sqlx::query_scalar("SELECT to_jsonb(r) FROM shadow_protocols r WHERE id=$1")
            .bind(PROTOCOL_ID)
            .fetch_optional(&mut *tx)
            .await?;
    let Some(protocol) = protocol.filter(|p| date(&p["start_session"]).is_ok_and(|d| origin >= d))
    else {
        tx.commit().await?;
        return Ok(
            json!({"status":"skipped","reason":"Shadow not enrolled or start session not reached","issued":[]}),
        );
    };
    let specification = required(&protocol, "specification")?;
    if required(specification, "code_hash")? != LEGACY_IMPLEMENTATION_HASH.trim() {
        return Err(invalid(
            "Shadow feature/model code differs from frozen protocol; reference remains available",
        ));
    }
    let artifacts:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT symbol,horizon,artifact FROM shadow_artifacts WHERE protocol_id=$1 ORDER BY symbol,horizon) r").bind(PROTOCOL_ID).fetch_all(&mut *tx).await?;
    if canonical::digest(&json!(artifacts))? != protocol["artifact_hash"] {
        return Err(invalid("Frozen artifact identity mismatch"));
    }
    let context = Arc::new(point_features::load_context(&mut tx, feed, Some(cutoff), true).await?);
    let mut targets =
        calendar.sessions_between(origin + Duration::days(1), origin + Duration::days(35))?;
    targets.truncate(5);
    let run = Uuid::new_v4();
    sqlx::query("INSERT INTO forecast_runs VALUES($1,$2,$3,NULL,'running',$4)")
        .bind(run)
        .bind(origin)
        .bind(instant)
        .bind(json!({"kind":"prospective_shadow","protocol":PROTOCOL_ID}))
        .execute(&mut *tx)
        .await?;
    let mut issued = Vec::new();
    let mut skipped = Vec::new();
    for symbol in ["MSI", "AXON"] {
        let stream = &context.streams[symbol];
        if stream
            .last()
            .map(|b| date(&b["session_date"]))
            .transpose()?
            != Some(origin)
        {
            skipped
                .push(json!({"symbol":symbol,"reason":"Origin prices not acquired before cutoff"}));
            continue;
        }
        let baseline:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM forecasts WHERE symbol=$1 AND origin_session=$2 AND model_id='historical-baseline-v1')").bind(symbol).bind(origin).fetch_one(&mut *tx).await?;
        if !baseline {
            skipped.push(json!({"symbol":symbol,"reason":"Paired reference not issued"}));
            continue;
        }
        let panel = latest(calendar.clone(), context.clone(), symbol).await?;
        let Some(row) = panel.last().filter(|r| r.date == origin) else {
            skipped.push(json!({"symbol":symbol,"reason":"Incomplete cutoff-safe feature origin"}));
            continue;
        };
        let group = required(specification, "feature_group")?
            .as_str()
            .ok_or_else(|| invalid("Missing frozen feature group"))?;
        if (matches!(group, "news" | "all") && row.lineage["news_coverage"] != true)
            || (matches!(group, "market" | "all") && row.x[14..22].iter().any(Option::is_none))
            || (matches!(group, "sec" | "all") && row.x[26].is_none())
        {
            skipped.push(json!({"symbol":symbol,"reason":"Required information group unavailable at cutoff"}));
            continue;
        }
        let feature = point_features::persist_panel(&mut tx, symbol, &panel, "prospective")
            .await?
            .into_iter()
            .next()
            .ok_or_else(|| invalid("Missing prospective feature identity"))?;
        let manifest = json!({"kind":"prospective_shadow","bar_ids":stream.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"hashes":stream.iter().map(|r|r["content_hash"].clone()).collect::<Vec<_>>(),"feed":feed,"adjustment":"split","adjustment_asof":stream.last().map(|r|r["adjustment_asof"].clone()),"availability_basis":"Archived acquisition times for live forecasts; reconstructed exchange availability for historical evaluation","feature_id":feature,"protocol":PROTOCOL_ID,"cutoff":stamp(cutoff)});
        let dataset = canonical::digest(&manifest)?;
        sqlx::query("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES($1,'prospective_shadow',$2,'split',$3) ON CONFLICT DO NOTHING").bind(&dataset).bind(feed).bind(manifest).execute(&mut *tx).await?;
        for item in artifacts.iter().filter(|a| a["symbol"] == symbol) {
            let artifact = required(item, "artifact")?;
            let model: NumericModel = serde_json::from_value(artifact.clone())?;
            let prediction = model.predict(&row.x)?;
            let horizon = required(item, "horizon")?
                .as_i64()
                .ok_or_else(|| invalid("Invalid frozen horizon"))?;
            let entry = targets
                .first()
                .ok_or_else(|| invalid("Prospective target sessions unavailable"))?;
            let index = horizon - 1;
            let index = if index < 0 {
                targets.len() as i64 + index
            } else {
                index
            };
            let target = usize::try_from(index)
                .ok()
                .and_then(|i| targets.get(i))
                .ok_or_else(|| Error::Reference {
                    kind: "IndexError",
                    message: "Frozen forecast target index unavailable".into(),
                })?;
            let training = required(artifact, "training_dates")?
                .as_i64()
                .ok_or_else(|| invalid("Invalid frozen sample count"))?;
            let interval = required(artifact, "interval_method")?;
            let id = Uuid::new_v4();
            let inserted:Option<Uuid>=sqlx::query_scalar("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'split',$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) ON CONFLICT(symbol,origin_session,horizon,model_id) DO NOTHING RETURNING id")
                .bind(id).bind(run).bind(symbol).bind(origin).bind(horizon as i32).bind(instant).bind(cutoff).bind(entry).bind(target).bind(stream[0]["feed"].as_str()).bind(date(&stream[0]["adjustment_asof"])?).bind(&dataset).bind(MODEL_ID).bind(point_features::VERSION)
                .bind(prediction.expected_net_return).bind(prediction.probability_positive).bind(prediction.lower_return).bind(prediction.upper_return).bind(training as i32)
                .bind(json!({"kind":"prospective_shadow","status":"experimental_inconclusive","protocol":PROTOCOL_ID,"artifact_hash":protocol["artifact_hash"],"feature_id":feature,"interval_method":interval})).fetch_optional(&mut *tx).await?;
            if inserted.is_some() {
                issued.push(id.to_string());
            }
        }
    }
    let status = if issued.is_empty() {
        "skipped"
    } else {
        "completed"
    };
    sqlx::query("UPDATE forecast_runs SET status=$2,completed_at=now(),detail=$3 WHERE id=$1").bind(run).bind(status).bind(json!({"kind":"prospective_shadow","protocol":PROTOCOL_ID,"issued_count":issued.len(),"skipped":skipped})).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO shadow_events(protocol_id,event,payload) VALUES($1,'issuance',$2)")
        .bind(PROTOCOL_ID)
        .bind(json!({"origin":origin,"issued":issued,"skipped":skipped}))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(json!({"status":status,"issued":issued,"skipped":skipped}))
}

static VERDICT_WORKERS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| Arc::new(Semaphore::new(2)));
fn verdict(rows: &[Value], dates: &[chrono::NaiveDate]) -> Result<Value> {
    use crate::{financials::number, statistics::paired_block_interval};
    let mut integrity = true;
    for row in rows {
        let actual = number(&row["realized_return"])?;
        let reference = number(&row["baseline_truth"])?;
        integrity &= (actual - reference).abs() <= 1e-10 + 1e-10 * reference.abs();
    }
    let mut comparisons = serde_json::Map::new();
    for horizon in [1, 5] {
        let selected: Vec<_> = rows.iter().filter(|r| r["horizon"] == horizon).collect();
        let origins: Vec<_> = selected
            .iter()
            .map(|r| date(&r["origin_session"]))
            .collect::<Result<_>>()?;
        for metric in ["mae", "brier"] {
            let mut deltas = Vec::new();
            for r in &selected {
                let y = number(&r["realized_return"])?;
                deltas.push(if metric == "mae" {
                    (y - number(&r["baseline_return"])?).abs()
                        - (y - number(&r["expected_return"])?).abs()
                } else {
                    let truth = if y > 0.0 { 1.0 } else { 0.0 };
                    (number(&r["baseline_probability"])? - truth).powi(2)
                        - (number(&r["probability_positive"])? - truth).powi(2)
                });
            }
            comparisons.insert(
                format!("{horizon}d_{metric}"),
                paired_block_interval(&origins, &deltas, 2000, 20, 0.9875)?
                    .ok_or_else(|| invalid("Insufficient final comparison dates"))?,
            );
        }
    }
    let passed = comparisons
        .values()
        .all(|v| v["lower"].as_f64().is_some_and(|v| v > 0.0));
    let status = if !integrity {
        "inconclusive"
    } else if passed {
        "passed"
    } else {
        "failed"
    };
    Ok(
        json!({"status":status,"first_origin":dates.first(),"last_origin":dates.last(),"dates":126,"comparisons":comparisons,"integrity":integrity,"promotion":"none; final selected-stock research verdict only"}),
    )
}
pub async fn progress_and_verdict(pool: &PgPool, finalize: bool) -> Result<Value> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(753194044)")
        .execute(&mut *tx)
        .await?;
    let protocol: Option<Value> =
        sqlx::query_scalar("SELECT to_jsonb(r) FROM shadow_protocols r WHERE id=$1")
            .bind(PROTOCOL_ID)
            .fetch_optional(&mut *tx)
            .await?;
    let Some(protocol) = protocol else {
        return Ok(json!({"status":"not_enrolled","paired_dates":0}));
    };
    let existing: Option<Value> =
        sqlx::query_scalar("SELECT to_jsonb(r) FROM shadow_verdicts r WHERE protocol_id=$1")
            .bind(PROTOCOL_ID)
            .fetch_optional(&mut *tx)
            .await?;
    if let Some(existing) = existing {
        return Ok(json!({"status":existing["status"],"final_verdict":existing["report"]}));
    }
    let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT f.origin_session,f.symbol,f.horizon,f.expected_return,f.probability_positive,b.expected_return AS baseline_return,b.probability_positive AS baseline_probability,o.realized_return,bo.realized_return AS baseline_truth FROM forecasts f JOIN forecasts b ON b.symbol=f.symbol AND b.origin_session=f.origin_session AND b.horizon=f.horizon AND b.model_id='historical-baseline-v1' LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true LEFT JOIN LATERAL (SELECT realized_return FROM forecast_outcomes WHERE forecast_id=b.id ORDER BY resolved_at DESC LIMIT 1) bo ON true WHERE f.model_id=$1 AND f.origin_session>=$2 ORDER BY f.origin_session,f.symbol,f.horizon) r").bind(MODEL_ID).bind(date(&protocol["start_session"])?).fetch_all(&mut *tx).await?;
    let mut counts = std::collections::BTreeMap::new();
    for row in &rows {
        *counts.entry(date(&row["origin_session"])?).or_insert(0) += 1;
    }
    let all: Vec<_> = counts
        .into_iter()
        .filter_map(|(d, n)| if n == 4 { Some(d) } else { None })
        .collect();
    let selected: Vec<_> = all.iter().copied().take(126).collect();
    let mut paired = Vec::new();
    for row in rows {
        if selected
            .binary_search(&date(&row["origin_session"])?)
            .is_ok()
        {
            paired.push(row);
        }
    }
    let resolved = paired
        .iter()
        .filter(|r| !r["realized_return"].is_null())
        .count();
    if selected.len() != 126
        || paired
            .iter()
            .any(|r| r["realized_return"].is_null() || r["baseline_truth"].is_null())
    {
        return Ok(
            json!({"status":"inconclusive","paired_dates":all.len(),"required_dates":126,"start_session":protocol["start_session"],"resolved_records":resolved,"protocol":protocol["specification"]}),
        );
    }
    if !finalize {
        return Ok(
            json!({"status":"ready_for_one_final_analysis","paired_dates":126,"required_dates":126}),
        );
    }
    let permit = VERDICT_WORKERS
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| invalid("Research verdict workers unavailable"))?;
    let report = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        verdict(&paired, &selected)
    })
    .await
    .map_err(|_| invalid("Research verdict worker failed"))??;
    sqlx::query("INSERT INTO shadow_verdicts(protocol_id,status,report) VALUES($1,$2,$3) ON CONFLICT DO NOTHING").bind(PROTOCOL_ID).bind(report["status"].as_str()).bind(&report).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(json!({"status":report["status"],"final_verdict":report}))
}
