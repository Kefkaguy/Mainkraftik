//! Descriptive past-price risk and prospective forecast reliability; no promotion.
use crate::{
    Result,
    calendar::Calendar,
    financials::{date, number},
    invalid,
};
use chrono::{DateTime, Duration, Utc};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::collections::{BTreeMap, BTreeSet};

pub async fn snapshot(
    pool: &PgPool,
    calendar: &Calendar,
    feed: &str,
    instant: DateTime<Utc>,
) -> Result<Value> {
    let expected = calendar
        .sessions_between(
            instant.date_naive() - Duration::days(12),
            instant.date_naive(),
        )?
        .into_iter()
        .filter(|d| {
            calendar
                .session(*d)
                .is_some_and(|s| s.close + Duration::minutes(15) <= instant)
        })
        .max();
    let mut tx = pool.begin().await?;
    let mut symbols = Vec::new();
    for symbol in ["MSI", "AXON", "SPY"] {
        let bars = crate::forecasting::bars_at(&mut tx, feed, symbol, instant, None).await?;
        let (valid, reason) = continuity(calendar, &bars)?;
        let history:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT f.*,o.realized_return,o.spy_return,o.resolved_at FROM forecasts f LEFT JOIN LATERAL (SELECT * FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true WHERE f.symbol=$1 ORDER BY f.issued_at DESC,f.symbol,f.horizon LIMIT 20000) r").bind(symbol).fetch_all(&mut *tx).await?;
        let mut overdue = 0;
        for row in &history {
            if row["realized_return"].is_null()
                && date(&row["target_session"])? < instant.date_naive() - Duration::days(1)
            {
                overdue += 1;
            }
        }
        let last = bars.last().map(|r| r["session_date"].clone());
        symbols.push(json!({"symbol":symbol,"last_session":last,"expected_completed_session":expected,"fresh":!bars.is_empty() && last==expected.map(|d|json!(d)),"contiguous":valid,"reason":reason,"overdue_outcomes":overdue,"risk":risk_summary(calendar,&bars)?,"reliability":reliability(&history)?}));
    }
    let worker: Option<Value> =
        sqlx::query_scalar("SELECT to_jsonb(r) FROM worker_status r WHERE id='research-worker'")
            .fetch_optional(&mut *tx)
            .await?;
    let fresh = worker
        .as_ref()
        .map(|w| {
            crate::financials::timestamp(&w["heartbeat_at"])
                .map(|at| instant - at < Duration::minutes(5))
        })
        .transpose()?
        .unwrap_or(false);
    let failed:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT id,kind,error,completed_at FROM jobs WHERE status='failed' ORDER BY created_at DESC LIMIT 10) r").fetch_all(&mut *tx).await?;
    tx.commit().await?;
    Ok(
        json!({"as_of":crate::engine::stamp(instant),"symbols":symbols,"worker":worker,"worker_fresh":fresh,"failed_jobs":failed,"interpretation":"Health and descriptive reliability only; no model promotion or trading authority"}),
    )
}

pub fn continuity(calendar: &Calendar, bars: &[Value]) -> Result<(bool, Option<&'static str>)> {
    if bars.is_empty() {
        return Ok((false, Some("No market data")));
    }
    let first = &bars[0];
    if bars.iter().any(|r| {
        ["feed", "adjustment", "adjustment_asof"]
            .iter()
            .any(|k| r[k] != first[k])
    }) {
        return Ok((false, Some("Mixed feeds or adjustment bases refused")));
    }
    let dates: Result<Vec<_>> = bars.iter().map(|r| date(&r["session_date"])).collect();
    let dates = dates?;
    if dates.windows(2).any(|w| w[0] >= w[1]) {
        return Ok((false, Some("Duplicate or unordered sessions")));
    }
    if dates != calendar.sessions_between(dates[0], dates[dates.len() - 1])? {
        return Ok((
            false,
            Some("Missing exchange sessions; data is not interpolated"),
        ));
    }
    Ok((true, None))
}
fn mean(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len() as f64
}
fn std(values: &[f64]) -> f64 {
    let average = mean(values);
    (values.iter().map(|v| (v - average).powi(2)).sum::<f64>() / values.len() as f64).sqrt()
}
pub fn risk_summary(calendar: &Calendar, bars: &[Value]) -> Result<Value> {
    let (valid, reason) = continuity(calendar, bars)?;
    if !valid || bars.len() < 61 {
        return Ok(
            json!({"available":false,"reason":reason.unwrap_or("61 completed sessions required")}),
        );
    }
    let closes: Result<Vec<_>> = bars.iter().map(|r| number(&r["close"])).collect();
    let closes = closes?;
    if closes.iter().any(|v| *v <= 0.0) {
        return Err(invalid("nonpositive risk price"));
    }
    let returns: Vec<_> = closes[closes.len() - 21..]
        .windows(2)
        .map(|w| w[1].ln() - w[0].ln())
        .collect();
    let mut gaps = Vec::new();
    for k in bars.len() - 20..bars.len() {
        gaps.push(number(&bars[k]["open"])? / closes[k - 1] - 1.0);
    }
    Ok(
        json!({"available":true,"as_of_session":bars[bars.len()-1]["session_date"],"return_20":closes[closes.len()-1]/closes[closes.len()-21]-1.0,
        "realized_volatility_20_annualized":std(&returns)*252.0f64.sqrt(),"mean_overnight_gap_20":mean(&gaps),"drawdown_60":closes[closes.len()-1]/closes[closes.len()-60..].iter().copied().fold(f64::NEG_INFINITY,f64::max)-1.0,
        "description":"Descriptive past-price measurements; not causal explanations or return predictions"}),
    )
}
pub fn metrics(
    y: &[f64],
    expected: &[f64],
    probability: &[f64],
    intervals: Option<(&[f64], &[f64])>,
) -> Result<Value> {
    if y.is_empty() || expected.len() != y.len() || probability.len() != y.len() {
        return Err(invalid("Metrics require equal nonempty arrays"));
    }
    if y.iter()
        .chain(expected)
        .chain(probability)
        .any(|v| !v.is_finite())
    {
        return Err(invalid("Nonfinite forecast or label"));
    }
    if probability.iter().any(|v| !(0.0..=1.0).contains(v)) {
        return Err(invalid("Probability outside [0,1]"));
    }
    let p: Vec<_> = probability
        .iter()
        .map(|v| v.clamp(1e-8, 1.0 - 1e-8))
        .collect();
    let labels: Vec<_> = y.iter().map(|v| if *v > 0.0 { 1.0 } else { 0.0 }).collect();
    let mut bins = Vec::new();
    for index in 0..10 {
        let rows: Vec<_> = p
            .iter()
            .enumerate()
            .filter(|(_, p)| ((**p * 10.0) as usize).min(9) == index)
            .map(|(i, _)| i)
            .collect();
        if !rows.is_empty() {
            bins.push(json!({"lower":index as f64/10.0,"count":rows.len(),"predicted":rows.iter().map(|i|p[*i]).sum::<f64>()/rows.len() as f64,"observed":rows.iter().map(|i|labels[*i]).sum::<f64>()/rows.len() as f64}));
        }
    }
    let n = y.len() as f64;
    let errors: Vec<_> = expected.iter().zip(y).map(|(a, b)| a - b).collect();
    let ece = bins
        .iter()
        .map(|b| {
            b["count"].as_f64().unwrap_or(0.0)
                * (b["observed"].as_f64().unwrap_or(0.0) - b["predicted"].as_f64().unwrap_or(0.0))
                    .abs()
        })
        .sum::<f64>()
        / n;
    let mut result = json!({"mae":errors.iter().map(|v|v.abs()).sum::<f64>()/n,"brier":p.iter().zip(&labels).map(|(p,y)|(p-y).powi(2)).sum::<f64>()/n,
        "log_loss":-p.iter().zip(&labels).map(|(p,y)|y*p.ln()+(1.0-y)*(1.0-p).ln()).sum::<f64>()/n,"calibration":bins,
        "rmse":(errors.iter().map(|e|e*e).sum::<f64>()/n).sqrt(),"bias":mean(&errors),"directional_accuracy":probability.iter().zip(y).filter(|(p,y)|(**p>=0.5)==(**y>0.0)).count() as f64/n,
        "positive_rate":mean(&labels),"probability_std":std(probability),"probability_min":probability.iter().copied().fold(f64::INFINITY,f64::min),"probability_max":probability.iter().copied().fold(f64::NEG_INFINITY,f64::max),"expected_return_std":std(expected),"ece":ece});
    if let Some((lower, upper)) = intervals {
        if lower.len() != y.len()
            || upper.len() != y.len()
            || lower
                .iter()
                .zip(upper)
                .any(|(a, b)| !a.is_finite() || !b.is_finite() || a > b)
        {
            return Err(invalid("Invalid interval"));
        }
        result["interval_coverage"] = json!(
            y.iter()
                .zip(lower)
                .zip(upper)
                .filter(|((y, l), u)| **y >= **l && **y <= **u)
                .count() as f64
                / n
        );
        result["interval_width"] =
            json!(upper.iter().zip(lower).map(|(u, l)| u - l).sum::<f64>() / n);
        result["interval_score_80"] = json!(
            upper
                .iter()
                .zip(lower)
                .zip(y)
                .map(|((u, l), y)| u - l + 10.0 * (l - y).max(0.0) + 10.0 * (y - u).max(0.0))
                .sum::<f64>()
                / n
        );
    }
    Ok(result)
}
pub fn reliability(history: &[Value]) -> Result<Vec<Value>> {
    let mut groups: BTreeMap<(String, i64), Vec<&Value>> = BTreeMap::new();
    for row in history {
        if !row["realized_return"].is_null() {
            groups
                .entry((
                    row["model_id"]
                        .as_str()
                        .ok_or_else(|| invalid("Missing model ID"))?
                        .into(),
                    row["horizon"]
                        .as_i64()
                        .ok_or_else(|| invalid("Missing horizon"))?,
                ))
                .or_default()
                .push(row);
        }
    }
    let mut output = Vec::new();
    for ((model, horizon), rows) in groups {
        let values =
            |key: &str| -> Result<Vec<f64>> { rows.iter().map(|r| number(&r[key])).collect() };
        let y = values("realized_return")?;
        let expected = values("expected_return")?;
        let probability = values("probability_positive")?;
        let lower = values("lower_return")?;
        let upper = values("upper_return")?;
        let dates: Result<BTreeSet<_>> = rows.iter().map(|r| date(&r["origin_session"])).collect();
        let dates = dates?;
        let mut metric = metrics(&y, &expected, &probability, Some((&lower, &upper)))?;
        let extra = json!({"model_id":model,"horizon":horizon,"observations":rows.len(),"distinct_dates":dates.len(),"first_origin":dates.first().map(ToString::to_string),"last_origin":dates.last().map(ToString::to_string),"evidence":"prospective_descriptive_not_repeated_confirmation_test"});
        metric
            .as_object_mut()
            .ok_or_else(|| invalid("Invalid reliability metrics"))?
            .extend(
                extra
                    .as_object()
                    .ok_or_else(|| invalid("Invalid reliability metadata"))?
                    .clone(),
            );
        output.push(metric);
    }
    Ok(output)
}
