use crate::{
    Result,
    calendar::Calendar,
    canonical,
    intraday::{data::QuoteSample, research},
    invalid,
    market::AlpacaData,
};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    pub version: u32,
    pub declared_at: DateTime<Utc>,
    pub start: NaiveDate,
    pub end: NaiveDate,
    pub feed: String,
    pub symbols: Vec<String>,
    pub monitoring_minutes: i64,
    pub maximum_requests: usize,
}
impl Plan {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1
            || !matches!(self.feed.as_str(), "sip" | "iex")
            || self.monitoring_minutes != 5
            || self.start > self.end
            || self.end - self.start > Duration::days(14)
            || self.end
                >= NaiveDate::from_ymd_opt(2025, 4, 3).ok_or_else(|| invalid("Audit date"))?
            || self.maximum_requests == 0
            || self.maximum_requests > 10000
            || self.symbols != ["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]
        {
            return Err(invalid(
                "Bounded quote plan violates selection/data boundaries",
            ));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Observation {
    pub plan_id: String,
    pub at: DateTime<Utc>,
    pub symbol: String,
    pub before: Option<QuoteSample>,
    pub after: Option<QuoteSample>,
    pub failures: Vec<String>,
}
pub async fn collect(
    provider: &AlpacaData,
    plan: &Plan,
    directory: &Path,
) -> Result<serde_json::Value> {
    plan.validate()?;
    std::fs::create_dir_all(directory)?;
    let plan_id = canonical::digest(&serde_json::to_value(plan)?)?;
    let calendar = Calendar::embedded()?;
    let mut attempted = 0;
    let mut cached = 0;
    let mut total = 0;
    let mut paired = 0;
    for day in calendar.sessions_between(plan.start, plan.end)? {
        let s = calendar
            .session(day)
            .ok_or_else(|| invalid("Quote calendar"))?;
        let mut at = s.open + Duration::minutes(5) + Duration::seconds(16);
        while at < s.close - Duration::seconds(16) {
            for symbol in &plan.symbols {
                total += 1;
                let key = canonical::digest(&serde_json::json!([plan_id, at, symbol]))?;
                let index = directory.join(format!("point-{key}.json"));
                if index.exists() {
                    let v: serde_json::Value = serde_json::from_slice(&std::fs::read(&index)?)?;
                    let path = v["artifact"]
                        .as_str()
                        .ok_or_else(|| invalid("Quote point index"))?;
                    if path.contains(['/', '\\']) {
                        return Err(invalid("Quote point path"));
                    }
                    let observation: Observation = research::load(&directory.join(path))?;
                    if observation.plan_id != plan_id
                        || observation.at != at
                        || observation.symbol != *symbol
                    {
                        return Err(invalid("Quote resume identity mismatch"));
                    }
                    cached += 1;
                    if observation.before.is_some() && observation.after.is_some() {
                        paired += 1;
                    }
                    continue;
                }
                if attempted + 2 > plan.maximum_requests {
                    return Ok(
                        serde_json::json!({"status":"REQUEST_BUDGET_REACHED_RESUMABLE","attempted_requests":attempted,"cached_points":cached,"visited_points":total,"paired_points":paired,"complete":false,"broker_requests":0}),
                    );
                }
                let mut obs = Observation {
                    plan_id: plan_id.clone(),
                    at,
                    symbol: symbol.clone(),
                    before: None,
                    after: None,
                    failures: Vec::new(),
                };
                for after in [false, true] {
                    tokio::time::sleep(std::time::Duration::from_millis(400)).await;
                    attempted += 1;
                    match provider.point_quote(symbol, at, &plan.feed, after).await {
                        Ok(q) => {
                            if after {
                                obs.after = q
                            } else {
                                obs.before = q
                            }
                        }
                        Err(e) => {
                            let reason = e.to_string();
                            obs.failures.push(reason.clone());
                            if reason.contains("HTTP 403") || reason.contains("HTTP 401") {
                                let path = research::save(
                                    directory,
                                    "dataset",
                                    &serde_json::to_value(&obs)?,
                                )?;
                                return Err(invalid(format!(
                                    "Declared quote feed unauthorized; evidence {}; no fallback",
                                    path.display()
                                )));
                            }
                        }
                    }
                }
                if obs.before.is_some() && obs.after.is_some() {
                    paired += 1;
                }
                let path = research::save(directory, "dataset", &serde_json::to_value(&obs)?)?;
                let bytes = serde_json::to_vec(
                    &serde_json::json!({"artifact":path.file_name().and_then(|s|s.to_str()).ok_or_else(||invalid("Quote artifact name"))?}),
                )?;
                use std::io::Write;
                let mut f = tempfile::NamedTempFile::new_in(directory)?;
                f.write_all(&bytes)?;
                f.as_file().sync_all()?;
                f.persist_noclobber(index).map_err(|e| e.error)?;
                if total % 60 == 0 {
                    eprintln!(
                        "quote points={total} paired={paired} requests={attempted} cached={cached}"
                    );
                }
            }
            at += Duration::minutes(plan.monitoring_minutes);
        }
    }
    Ok(
        serde_json::json!({"status":"COMPLETE","attempted_requests":attempted,"cached_points":cached,"visited_points":total,"paired_points":paired,"paired_coverage":paired as f64/total.max(1) as f64,"complete":true,"broker_requests":0,"historical_availability_reconstructed":true}),
    )
}
pub fn load(directory: &Path, plan: &Plan) -> Result<Vec<Observation>> {
    plan.validate()?;
    let id = canonical::digest(&serde_json::to_value(plan)?)?;
    let calendar = Calendar::embedded()?;
    let mut result = Vec::new();
    for entry in std::fs::read_dir(directory)? {
        let path = entry?.path();
        if !path
            .file_name()
            .and_then(|s| s.to_str())
            .is_some_and(|s| s.starts_with("point-") && s.ends_with(".json"))
        {
            continue;
        }
        let v: serde_json::Value = serde_json::from_slice(&std::fs::read(path)?)?;
        let name = v["artifact"]
            .as_str()
            .ok_or_else(|| invalid("Point artifact"))?;
        if name.contains(['/', '\\']) {
            return Err(invalid("Point artifact path"));
        }
        let row: Observation = research::load(&directory.join(name))?;
        if row.plan_id != id {
            return Err(invalid("Mixed quote plans"));
        }
        let day = row
            .at
            .with_timezone(&chrono_tz::America::New_York)
            .date_naive();
        let session = calendar
            .session(day)
            .ok_or_else(|| invalid("Quote point outside session"))?;
        let delta = row.at - session.open;
        if day < plan.start
            || day > plan.end
            || !plan.symbols.contains(&row.symbol)
            || delta.num_seconds() < 316
            || delta.num_seconds() % 300 != 16
            || row.at >= session.close - Duration::seconds(16)
        {
            return Err(invalid("Quote point outside declared grid"));
        }
        for (sample, after) in [(&row.before, false), (&row.after, true)] {
            if let Some(s) = sample {
                let q = &s.quote;
                q.validate(q.source_time)?;
                if s.symbol != row.symbol
                    || q.feed != plan.feed
                    || !s.bid_shares.is_finite()
                    || !s.ask_shares.is_finite()
                    || s.bid_shares <= 0.
                    || s.ask_shares <= 0.
                    || q.acquired_at < q.source_time
                    || !s.historical_reconstruction
                    || if after {
                        q.source_time < row.at + Duration::seconds(1)
                            || q.source_time > row.at + Duration::seconds(14)
                    } else {
                        q.source_time < row.at - Duration::seconds(15)
                            || q.source_time > row.at - Duration::seconds(1)
                    }
                {
                    return Err(invalid("Quote sample violates source/size/timing contract"));
                }
            }
        }
        result.push(row);
    }
    result.sort_by_key(|r| (r.at, r.symbol.clone()));
    Ok(result)
}
