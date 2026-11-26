//! Existing reference forecast issuance and immutable outcome resolution.
//! No order execution, model selection, enrollment, or automatic promotion.
use crate::{
    Error, Result,
    calendar::{Calendar, cutoff_for},
    canonical,
    engine::{Clock, stamp},
    financials, invalid, monitoring,
};
use bigdecimal::{BigDecimal, Context, RoundingMode, num_bigint::BigInt};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde_json::{Value, json};
use sqlx::{PgConnection, PgPool};
use std::{collections::BTreeMap, num::NonZeroU64, sync::Arc};
use uuid::Uuid;
pub const MODEL: &str = "historical-baseline-v1";
const INTERVAL: &str =
    "Historical 10th\u{e2}\u{20ac}\u{201c}90th percentiles; not a coverage guarantee";
pub async fn bars_at(
    connection: &mut PgConnection,
    feed: &str,
    symbol: &str,
    cutoff: DateTime<Utc>,
    acquired: Option<DateTime<Utc>>,
) -> Result<Vec<Value>> {
    // JSON's ordinary numeric decoder would lose NUMERIC precision before the
    // Decimal outcome calculation. Keep exact decimal text inside this layer.
    Ok(sqlx::query_scalar("WITH basis AS (SELECT max(adjustment_asof) AS d FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND available_at<=$3 AND ($4::timestamptz IS NULL OR acquired_at<=$4)) SELECT to_jsonb(r)||jsonb_build_object('open',r.open::text,'close',r.close::text) FROM (SELECT DISTINCT ON(session_date) * FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND available_at<=$3 AND ($4::timestamptz IS NULL OR acquired_at<=$4) AND adjustment_asof=(SELECT d FROM basis) ORDER BY session_date,acquired_at DESC,id DESC) r")
        .bind(symbol).bind(feed).bind(cutoff).bind(acquired).fetch_all(connection).await?)
}
fn decimal(value: &Value) -> Result<BigDecimal> {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
        .parse()
        .map_err(|_| invalid("Invalid decimal outcome price"))
}
/// Python Decimal's 28 significant digits / half-even division, followed by
/// a separately rounded subtraction. Integer division avoids double rounding.
pub fn decimal_return(close: &str, open: &str) -> Result<f64> {
    let close = decimal(&json!(close))?;
    let open = decimal(&json!(open))?;
    if close <= 0 || open <= 0 {
        return Err(invalid("Nonpositive outcome price"));
    }
    let (n, ns) = close.as_bigint_and_exponent();
    let (d, ds) = open.as_bigint_and_exponent();
    let mut order = n.to_string().len() as i64 - d.to_string().len() as i64;
    let ten = BigInt::from(10);
    let pow = |n: i64| -> Result<BigInt> {
        Ok(ten.pow(u32::try_from(n).map_err(|_| invalid("Outcome decimal scale unavailable"))?))
    };
    if if order >= 0 {
        n < &d * pow(order)?
    } else {
        &n * pow(-order)? < d
    } {
        order -= 1;
    }
    let scale = 27 - order;
    let (numerator, denominator) = if scale >= 0 {
        (n * pow(scale)?, d)
    } else {
        (n, d * pow(-scale)?)
    };
    let mut q = &numerator / &denominator;
    let remainder = &numerator % &denominator;
    let doubled = &remainder * 2;
    if doubled > denominator || (doubled == denominator && &q % 2 != BigInt::from(0)) {
        q += 1;
    }
    let quotient = BigDecimal::new(q, scale + ns - ds);
    let context = Context::new(
        NonZeroU64::new(28).expect("nonzero constant"),
        RoundingMode::HalfEven,
    );
    context
        .round_decimal(quotient - BigDecimal::from(1))
        .to_string()
        .parse::<f64>()
        .ok()
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("Nonfinite outcome return"))
}
fn outcome(entry: &Value, exit: &Value) -> Result<f64> {
    decimal_return(
        exit["close"]
            .as_str()
            .ok_or_else(|| invalid("Missing exact close"))?,
        entry["open"]
            .as_str()
            .ok_or_else(|| invalid("Missing exact open"))?,
    )
}
pub fn historical_samples(calendar: &Calendar, bars: &[Value], horizon: usize) -> Result<Vec<f64>> {
    let (valid, reason) = monitoring::continuity(calendar, bars)?;
    if !valid {
        return Err(invalid(reason.unwrap_or("Invalid continuity")));
    }
    if !matches!(horizon, 1 | 5) {
        return Err(invalid("Unsupported forecast horizon"));
    }
    let mut values = Vec::new();
    for i in 0..bars.len().saturating_sub(horizon - 1) {
        values.push(
            financials::number(&bars[i + horizon - 1]["close"])?
                / financials::number(&bars[i]["open"])?
                - 1.0,
        );
    }
    Ok(values)
}
pub fn baseline(samples: &[f64]) -> Result<Value> {
    if samples.len() < 252 || samples.iter().any(|v| !v.is_finite()) {
        return Err(invalid(
            "At least 252 finite completed historical outcomes are required",
        ));
    }
    let mut sorted = samples.to_vec();
    sorted.sort_by(f64::total_cmp);
    let quantile = |q: f64| {
        let pos = (sorted.len() - 1) as f64 * q;
        let i = pos.floor() as usize;
        let t = pos - i as f64;
        let a = sorted[i];
        let b = sorted[(i + 1).min(sorted.len() - 1)];
        if t >= 0.5 {
            b - (b - a) * (1.0 - t)
        } else {
            a + (b - a) * t
        }
    };
    Ok(
        json!({"expected_return":samples.iter().sum::<f64>()/samples.len() as f64,"probability_positive":samples.iter().filter(|v|**v>0.0).count() as f64/samples.len() as f64,"lower_return":quantile(0.1),"upper_return":quantile(0.9),"sample_count":samples.len(),"interval_method":INTERVAL}),
    )
}
pub struct Forecaster {
    pool: PgPool,
    calendar: Arc<Calendar>,
    clock: Arc<dyn Clock>,
    feed: String,
}
impl Forecaster {
    pub(crate) fn evaluator(&self) -> crate::research_evaluation_store::Evaluator {
        crate::research_evaluation_store::Evaluator::new(
            self.pool.clone(),
            self.calendar.clone(),
            self.feed.clone(),
        )
    }
    pub(crate) fn auditor(&self) -> crate::research_audit::Auditor {
        crate::research_audit::Auditor::new(self.pool.clone(), self.calendar.clone())
    }
    pub fn new(
        pool: PgPool,
        calendar: Arc<Calendar>,
        clock: Arc<dyn Clock>,
        feed: String,
    ) -> Result<Self> {
        if !matches!(feed.as_str(), "sip" | "iex") {
            return Err(invalid("Unsupported feed"));
        }
        Ok(Self {
            pool,
            calendar,
            clock,
            feed,
        })
    }
    fn targets(&self, origin: NaiveDate) -> Result<Vec<NaiveDate>> {
        let mut days = self
            .calendar
            .sessions_between(origin + Duration::days(1), origin + Duration::days(35))?;
        days.truncate(5);
        if days.len() != 5 {
            return Err(invalid("Forecast target sessions unavailable"));
        }
        Ok(days)
    }
    fn maturity(&self, day: NaiveDate) -> Result<DateTime<Utc>> {
        Ok(self
            .calendar
            .session(day)
            .ok_or_else(|| invalid("Forecast target session unavailable"))?
            .close
            + Duration::minutes(15))
    }
    pub async fn issue_existing_enrollment(&self) -> Result<Value> {
        crate::prospective::issue(
            &self.pool,
            self.calendar.clone(),
            self.clock.now(),
            &self.feed,
        )
        .await
    }
    pub async fn monitor(&self) -> Result<Value> {
        crate::monitoring::snapshot(&self.pool, &self.calendar, &self.feed, self.clock.now()).await
    }
    pub async fn issue(&self) -> Result<Value> {
        let instant = self.clock.now();
        let (origin, blocked) = self.calendar.issuance_window(instant)?;
        let run = Uuid::new_v4();
        let mut tx = self.pool.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock(753194010)")
            .execute(&mut *tx)
            .await?;
        sqlx::query("INSERT INTO forecast_runs VALUES($1,$2,$3,NULL,'running',$4)")
            .bind(run)
            .bind(origin.unwrap_or(instant.date_naive()))
            .bind(instant)
            .bind(json!({"reason":blocked}))
            .execute(&mut *tx)
            .await?;
        if let Some(reason) = blocked {
            sqlx::query("UPDATE forecast_runs SET status='skipped',completed_at=now() WHERE id=$1")
                .bind(run)
                .execute(&mut *tx)
                .await?;
            tx.commit().await?;
            return Ok(json!({"status":"skipped","reason":reason,"issued":[]}));
        }
        let origin = origin.ok_or_else(|| invalid("Missing forecast origin"))?;
        let cutoff = cutoff_for(origin)?;
        let targets = self.targets(origin)?;
        let listing: Value =
            sqlx::query_scalar("SELECT symbols FROM watchlist_versions ORDER BY id DESC LIMIT 1")
                .fetch_one(&mut *tx)
                .await?;
        let mut symbols: Vec<String> = serde_json::from_value(listing)?;
        symbols.push("SPY".into());
        let mut issued = Vec::new();
        let mut skipped = Vec::new();
        for symbol in symbols {
            let bars = bars_at(&mut tx, &self.feed, &symbol, cutoff, Some(cutoff)).await?;
            let (valid, reason) = monitoring::continuity(&self.calendar, &bars)?;
            if !valid
                || bars
                    .last()
                    .map(|r| financials::date(&r["session_date"]))
                    .transpose()?
                    != Some(origin)
            {
                let reason = if bars.is_empty() {
                    "No complete origin-session snapshot was acquired before the 20:00 cutoff"
                } else {
                    reason.unwrap_or("Latest origin session was not acquired by cutoff")
                };
                skipped.push(json!({"symbol":symbol,"reason":reason}));
                continue;
            }
            let manifest = json!({"kind":"live_forecast","bar_ids":bars.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"hashes":bars.iter().map(|r|r["content_hash"].clone()).collect::<Vec<_>>(),"feed":self.feed,"adjustment":"split","adjustment_asof":bars.last().map(|r|r["adjustment_asof"].clone()),"availability_basis":"Archived acquisition times for live forecasts; reconstructed exchange availability for historical evaluation","cutoff":stamp(cutoff)});
            let dataset = canonical::digest(&manifest)?;
            sqlx::query("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES($1,'live_forecast',$2,'split',$3) ON CONFLICT DO NOTHING").bind(&dataset).bind(&self.feed).bind(manifest).execute(&mut *tx).await?;
            for horizon in [1usize, 5] {
                let prediction =
                    historical_samples(&self.calendar, &bars, horizon).and_then(|s| baseline(&s));
                let prediction = match prediction {
                    Ok(p) => p,
                    Err(Error::Invalid(reason)) => {
                        skipped.push(json!({"symbol":symbol,"horizon":horizon,"reason":reason}));
                        continue;
                    }
                    Err(e) => return Err(e),
                };
                let id = Uuid::new_v4();
                let inserted:Option<Uuid>=sqlx::query_scalar("INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'split',$11,$12,$13,'daily-reference-v1',$14,$15,$16,$17,$18,$19) ON CONFLICT(symbol,origin_session,horizon,model_id) DO NOTHING RETURNING id")
                    .bind(id).bind(run).bind(&symbol).bind(origin).bind(horizon as i32).bind(instant).bind(cutoff).bind(targets[0]).bind(targets[horizon-1]).bind(bars[0]["feed"].as_str()).bind(financials::date(&bars[0]["adjustment_asof"])?).bind(&dataset).bind(MODEL)
                    .bind(financials::number(&prediction["expected_return"])?).bind(financials::number(&prediction["probability_positive"])?).bind(financials::number(&prediction["lower_return"])?).bind(financials::number(&prediction["upper_return"])?).bind(prediction["sample_count"].as_i64().ok_or_else(||invalid("Missing sample count"))? as i32)
                    .bind(json!({"kind":"prospective","interval_method":INTERVAL,"target":"Split-adjusted next-open to horizon-close price return; dividends excluded","limitations":"Selected securities, overlapping training outcomes, reference only; no execution assumptions"})).fetch_optional(&mut *tx).await?;
                if inserted.is_some() {
                    issued.push(json!({"id":id.to_string(),"symbol":symbol,"horizon":horizon}));
                }
            }
        }
        let status = if issued.is_empty() {
            "skipped"
        } else {
            "completed"
        };
        sqlx::query("UPDATE forecast_runs SET completed_at=now(),status=$2,detail=$3 WHERE id=$1")
            .bind(run)
            .bind(status)
            .bind(json!({"issued_count":issued.len(),"skipped":skipped}))
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok(json!({"status":status,"issued":issued,"skipped":skipped}))
    }
    pub async fn resolve(&self) -> Result<Value> {
        let instant = self.clock.now();
        let mut tx = self.pool.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock(753194011)")
            .execute(&mut *tx)
            .await?;
        let pending:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(f) FROM forecasts f WHERE NOT EXISTS(SELECT 1 FROM forecast_outcomes o WHERE o.forecast_id=f.id) ORDER BY issued_at").fetch_all(&mut *tx).await?;
        let mut resolved = Vec::new();
        for f in pending {
            if instant < self.maturity(financials::date(&f["target_session"])?)? {
                continue;
            }
            let rows = bars_at(
                &mut tx,
                &self.feed,
                f["symbol"]
                    .as_str()
                    .ok_or_else(|| invalid("Missing forecast symbol"))?,
                instant,
                None,
            )
            .await?;
            let selected: BTreeMap<_, _> = rows
                .iter()
                .map(|r| (r["session_date"].as_str().unwrap_or_default(), r))
                .collect();
            let (Some(entry), Some(exit)) = (
                selected.get(f["entry_session"].as_str().unwrap_or_default()),
                selected.get(f["target_session"].as_str().unwrap_or_default()),
            ) else {
                continue;
            };
            if entry["feed"] != f["feed"] {
                continue;
            }
            let spy = bars_at(&mut tx, &self.feed, "SPY", instant, None).await?;
            let a = spy.iter().find(|r| r["session_date"] == f["entry_session"]);
            let b = spy
                .iter()
                .find(|r| r["session_date"] == f["target_session"]);
            let spy_return = match (a, b) {
                (Some(a), Some(b)) => Some(outcome(a, b)?),
                _ => None,
            };
            let id = f["id"]
                .as_str()
                .ok_or_else(|| invalid("Missing forecast identity"))?
                .parse::<Uuid>()
                .map_err(|_| invalid("Invalid forecast identity"))?;
            sqlx::query("INSERT INTO forecast_outcomes VALUES($1,$2,$3,$4,$5,$6,$7,$8,NULL)").bind(Uuid::new_v4()).bind(id).bind(instant).bind(outcome(entry,exit)?).bind(spy_return).bind(entry["id"].as_i64()).bind(exit["id"].as_i64()).bind(json!({"feed":entry["feed"],"adjustment_asof":entry["adjustment_asof"],"entry_hash":entry["content_hash"],"exit_hash":exit["content_hash"],"dividends":"excluded","spy_bar_ids":match(a,b){(Some(a),Some(b))=>vec![a["id"].clone(),b["id"].clone()],_=>Vec::new()}})).execute(&mut *tx).await?;
            resolved.push(id.to_string());
        }
        tx.commit().await?;
        Ok(json!({"count":resolved.len(),"resolved":resolved}))
    }
    pub async fn resolve_detections(&self) -> Result<Value> {
        let instant = self.clock.now();
        let mut tx = self.pool.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock(753194012)")
            .execute(&mut *tx)
            .await?;
        let detections: Vec<Value> =
            sqlx::query_scalar("SELECT to_jsonb(d) FROM chart_detections d")
                .fetch_all(&mut *tx)
                .await?;
        let mut count = 0;
        for d in detections {
            let targets =
                self.targets(financials::date(&d["payload"]["manifest"]["last_session"])?)?;
            let rows = bars_at(
                &mut tx,
                &self.feed,
                d["symbol"]
                    .as_str()
                    .ok_or_else(|| invalid("Missing detection symbol"))?,
                instant,
                None,
            )
            .await?;
            for horizon in [1usize, 5] {
                let a = rows
                    .iter()
                    .find(|r| r["session_date"] == targets[0].to_string());
                let b = rows
                    .iter()
                    .find(|r| r["session_date"] == targets[horizon - 1].to_string());
                let (Some(a), Some(b)) = (a, b) else {
                    continue;
                };
                if instant < self.maturity(targets[horizon - 1])?
                    || financials::timestamp(&d["created_at"])?
                        >= self
                            .calendar
                            .session(targets[0])
                            .ok_or_else(|| invalid("Missing entry session"))?
                            .open
                {
                    continue;
                }
                let inserted:Option<Uuid>=sqlx::query_scalar("INSERT INTO detection_outcomes VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING RETURNING id").bind(Uuid::new_v4()).bind(d["id"].as_str().ok_or_else(||invalid("Missing detection identity"))?.parse::<Uuid>().map_err(|_|invalid("Invalid detection identity"))?).bind(horizon as i32).bind(outcome(a,b)?).bind(instant).bind(json!({"entry_bar_id":a["id"],"exit_bar_id":b["id"],"feed":a["feed"]})).fetch_optional(&mut *tx).await?;
                count += usize::from(inserted.is_some());
            }
        }
        tx.commit().await?;
        Ok(json!({"resolved":count}))
    }
}
