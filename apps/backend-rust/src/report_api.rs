//! Read-only verified research evidence. No model promotion or execution routes.
use crate::{
    Result,
    api::{ApiError, ApiResult},
    canonical, invalid,
};
use axum::http::StatusCode;
use serde_json::{Map, Value, json};
use sqlx::{Connection, SqliteConnection, sqlite::SqliteConnectOptions};
use std::{path::Path, time::Duration};

pub fn read_record(root: &Path, key: &str) -> Result<Value> {
    if key.len() != 64
        || !key
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid("Invalid research record ID"));
    }
    let record: Value = serde_json::from_slice(&std::fs::read(
        root.join("records").join(format!("{key}.json")),
    )?)?;
    if canonical::digest(&record)? != key {
        return Err(invalid("Research evidence integrity failure"));
    }
    Ok(record)
}
pub fn latest(root: &Path, lab: bool) -> Result<Value> {
    let pointer = root.join("latest.json");
    if !pointer.try_exists()? {
        return Ok(if lab {
            json!({"status":"not_run","prospective":{"status":"not_nominated"}})
        } else {
            json!({"status":"NOT_RUN","execution_authority":false})
        });
    }
    let pointer: Value = serde_json::from_slice(&std::fs::read(pointer)?)?;
    let key = pointer["id"]
        .as_str()
        .ok_or_else(|| invalid("Invalid research pointer"))?;
    let record = read_record(root, key)?;
    let mut payload = record["payload"]
        .as_object()
        .ok_or_else(|| invalid("Invalid research payload"))?
        .clone();
    if lab {
        payload.insert("report_id".into(), json!(key));
    } else {
        payload.entry("record_id").or_insert(json!(key));
    }
    Ok(Value::Object(payload))
}
pub async fn monitor(root: &Path) -> Result<Value> {
    let path = root.join("controller.sqlite");
    if !path.try_exists()? {
        return Ok(
            json!({"status":"NOT_RUNNING","predictions":[],"runs":[],"outcomes":[],"execution_authority":false}),
        );
    }
    let options = SqliteConnectOptions::new()
        .filename(path)
        .read_only(true)
        .create_if_missing(false)
        .busy_timeout(Duration::from_secs(2));
    let mut db = SqliteConnection::connect_with(&options).await?;
    let mut data = Map::new();
    data.insert("status".into(), json!("RECORDED"));
    for kind in [
        "prediction",
        "outcome",
        "controller_run",
        "offline_proposal",
    ] {
        let records: Vec<String> = sqlx::query_scalar(
            "SELECT payload FROM evidence WHERE kind=? ORDER BY rowid DESC LIMIT 100",
        )
        .bind(kind)
        .fetch_all(&mut db)
        .await?;
        let payloads: std::result::Result<Vec<Value>, _> =
            records.iter().map(|r| serde_json::from_str(r)).collect();
        data.insert(kind.into(), json!(payloads?));
    }
    db.close().await?;
    data.insert("execution_authority".into(), json!(false));
    Ok(Value::Object(data))
}
fn unavailable(message: &str) -> ApiError {
    ApiError::new(StatusCode::SERVICE_UNAVAILABLE, message)
}
pub async fn phase7(root: &Path) -> ApiResult<Value> {
    let folder = root.join("phase7");
    let report = tokio::task::spawn_blocking(move || latest(&folder, false))
        .await
        .map_err(|_| unavailable("Phase 7 immutable evidence unavailable"))?
        .map_err(|_| unavailable("Phase 7 immutable evidence unavailable"))?;
    let mut audit = Map::new();
    if let Some(items) = report["audit"].as_object() {
        for (name, value) in items {
            let scenario = value["scenarios"]["base_30min"]
                .as_object()
                .ok_or_else(|| unavailable("Phase 7 immutable evidence unavailable"))?;
            let base: Map<String, Value> = scenario
                .iter()
                .filter(|(k, _)| !matches!(k.as_str(), "orders" | "trades" | "daily_net_pnl"))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            audit.insert(
                name.clone(),
                json!({"prediction":value["prediction"],"base_scenario":base}),
            );
        }
    }
    Ok(
        json!({"status":report["status"],"winner":report["winner"],"audit":audit,"audit_classification":report["audit_classification"],"acceptance":report["acceptance"],"prospective":report["prospective"],"execution_authority":false,"promotion_authority":false}),
    )
}
pub async fn chart_status(root: &Path) -> ApiResult<Value> {
    let folder = root.join("chart-intelligence");
    let read_folder = folder.clone();
    let mut report = tokio::task::spawn_blocking(move || latest(&read_folder, false))
        .await
        .map_err(|_| unavailable("Research evidence integrity unavailable"))?
        .map_err(|_| unavailable("Research evidence integrity unavailable"))?;
    let mut monitoring = monitor(&folder)
        .await
        .map_err(|_| unavailable("Research evidence integrity unavailable"))?;
    if let Some(report) = report.as_object_mut() {
        report.remove("predictions");
    }
    for kind in ["prediction", "outcome"] {
        let rows = monitoring
            .as_object_mut()
            .ok_or_else(|| unavailable("Research evidence integrity unavailable"))?
            .entry(kind)
            .or_insert(json!([]));
        if let Some(rows) = rows.as_array_mut() {
            for row in rows {
                if let Some(row) = row.as_object_mut() {
                    row.remove("features");
                }
            }
        }
    }
    Ok(
        json!({"research":report,"monitoring":monitoring,"deployment":{"status":"LOCAL_RESEARCH_ONLY","promotion_authority":false,"execution_authority":false,"paper_policy":"Existing deployed policy preserved"}}),
    )
}
pub async fn lab_results(root: &Path) -> ApiResult<Value> {
    let folder = root.join("strategy-lab");
    let report = tokio::task::spawn_blocking(move || latest(&folder, true))
        .await
        .map_err(|_| unavailable("Research report integrity could not be verified"))?
        .map_err(|_| unavailable("Research report integrity could not be verified"))?;
    let mut output: Map<String, Value> = report
        .as_object()
        .ok_or_else(|| unavailable("Research report integrity could not be verified"))?
        .iter()
        .filter(|(k, _)| {
            !matches!(
                k.as_str(),
                "coverage" | "news" | "frozen_comparison" | "monthly_walk_forward"
            )
        })
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    output.insert(
        "monthly_walk_forward".into(),
        json!({"summary":report["monthly_walk_forward"]["summary"]}),
    );
    let mut comparison = Map::new();
    if let Some(values) = report["frozen_comparison"].as_object() {
        for (key, value) in values {
            comparison.insert(
                key.clone(),
                if value.is_object() && value.get("summary").is_some() {
                    json!({"summary":value["summary"]})
                } else {
                    value.clone()
                },
            );
        }
    }
    output.insert("frozen_comparison".into(), Value::Object(comparison));
    Ok(Value::Object(output))
}
