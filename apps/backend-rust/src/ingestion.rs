//! Native research ingestion over the original schema and immutable identities.
use crate::{
    Error, Result,
    calendar::Calendar,
    canonical,
    engine::{Clock, stamp},
    financials, invalid,
    research_providers::{self, Providers},
};
use async_trait::async_trait;
use bigdecimal::BigDecimal;
use chrono::{DateTime, Duration, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use sqlx::{PgConnection, PgPool};
use std::{
    collections::BTreeSet,
    path::{Component, Path, PathBuf},
    sync::Arc,
};
use uuid::Uuid;
pub const PRICE_PROJECTION: u64 = 32 * 1024 * 1024;
pub const FACT_PROJECTION: u64 = 20 * 1024 * 1024;
pub const DATABASE_CEILING: u64 = 256 * 1024 * 1024;
// Python's dispatcher accepts iterable strings and mapping keys as well as lists.
// Keep their persisted input shape while using the same ordered symbol iteration.
pub(crate) fn iteration_symbols(input: &Value) -> Result<Vec<String>> {
    match input {
        Value::String(text) => Ok(text.chars().map(|c| c.to_string()).collect()),
        Value::Object(map) => Ok(map.keys().cloned().collect()),
        Value::Array(items) => {
            for item in items {
                if item.is_array() || item.is_object() {
                    return Err(Error::Reference {
                        kind: "TypeError",
                        message: format!(
                            "unhashable type: '{}'",
                            if item.is_array() { "list" } else { "dict" }
                        ),
                    });
                }
            }
            items
                .iter()
                .map(|v| {
                    v.as_str().map(str::to_owned).ok_or_else(|| {
                        invalid("Refresh symbols must belong to the verified securities registry")
                    })
                })
                .collect()
        }
        _ => Err(Error::Reference {
            kind: "TypeError",
            message: "Refresh symbols are not iterable".into(),
        }),
    }
}
#[async_trait]
pub trait ResourceCheck: Send + Sync {
    async fn check(&self, projected_bytes: u64) -> Result<()>;
}
pub trait Disk: Send + Sync {
    fn free_bytes(&self) -> Result<u64>;
}
pub struct NativeDisk {
    root: PathBuf,
    storage: PathBuf,
}
fn lexical(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}
impl NativeDisk {
    pub fn new(project_root: &Path, data_dir: &Path) -> Result<Self> {
        let root = lexical(&std::path::absolute(project_root)?);
        std::fs::canonicalize(&root)?;
        let storage = lexical(&if data_dir.is_absolute() {
            data_dir.to_path_buf()
        } else {
            root.join(data_dir)
        });
        if !storage.starts_with(root.join(".local")) {
            return Err(invalid(
                "Data storage must remain inside this project's .local directory",
            ));
        }
        Ok(Self { root, storage })
    }
}
impl Disk for NativeDisk {
    fn free_bytes(&self) -> Result<u64> {
        // Validate existing ancestors before mkdir; never traverse a .local link
        // into another project/service while probing storage headroom.
        let allowed = std::fs::canonicalize(&self.root)?.join(".local");
        let relative = self
            .storage
            .strip_prefix(&self.root)
            .map_err(|_| invalid("Invalid data storage"))?;
        let mut prefix = self.root.clone();
        for part in relative.components() {
            prefix.push(part);
            match std::fs::symlink_metadata(&prefix) {
                Ok(_) => {
                    if !std::fs::canonicalize(&prefix)?.starts_with(&allowed) {
                        return Err(invalid(
                            "Data storage resolves outside this project's .local directory",
                        ));
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => break,
                Err(e) => return Err(e.into()),
            }
        }
        std::fs::create_dir_all(&self.storage)?;
        Ok(fs2::available_space(&self.storage)?)
    }
}
pub struct ResourceGate {
    pool: PgPool,
    disk: Arc<dyn Disk>,
    min_free_gb: f64,
}
impl ResourceGate {
    pub fn new(pool: PgPool, disk: Arc<dyn Disk>, min_free_gb: f64) -> Result<Self> {
        if !min_free_gb.is_finite() || min_free_gb < 0.0 {
            return Err(invalid("Invalid storage reserve"));
        }
        Ok(Self {
            pool,
            disk,
            min_free_gb,
        })
    }
}
pub fn check_limits(free: u64, projected: u64, reserve_gb: f64, size: Option<u64>) -> Result<()> {
    if (free as f64) - (projected as f64) < reserve_gb * 1024f64.powi(3) {
        return Err(invalid(
            "Insufficient storage headroom; no cleanup or deletion was attempted",
        ));
    }
    if size.is_some_and(|n| {
        n.checked_add(projected)
            .is_none_or(|n| n > DATABASE_CEILING)
    }) {
        return Err(invalid(
            "Independent research database reached its 256 MiB growth gate; ingestion paused",
        ));
    }
    Ok(())
}
#[async_trait]
impl ResourceCheck for ResourceGate {
    async fn check(&self, projected: u64) -> Result<()> {
        let disk = self.disk.clone();
        let free = tokio::task::spawn_blocking(move || disk.free_bytes())
            .await
            .map_err(|_| invalid("Storage probe unavailable"))??;
        check_limits(free, projected, self.min_free_gb, None)?;
        let size: i64 = sqlx::query_scalar("SELECT pg_database_size(current_database())")
            .fetch_one(&self.pool)
            .await?;
        check_limits(free, projected, self.min_free_gb, Some(size as u64))
    }
}
pub async fn store_document(
    connection: &mut PgConnection,
    provider: &str,
    url: &str,
    payload: &Value,
    acquired: DateTime<Utc>,
) -> Result<Uuid> {
    let hash = canonical::digest(payload)?;
    if let Some(id) = sqlx::query_scalar(
        "SELECT id FROM source_documents WHERE provider=$1 AND url=$2 AND content_hash=$3",
    )
    .bind(provider)
    .bind(url)
    .bind(&hash)
    .fetch_optional(&mut *connection)
    .await?
    {
        return Ok(id);
    }
    sqlx::query("INSERT INTO source_documents VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(provider,url,content_hash) DO NOTHING").bind(Uuid::new_v4()).bind(provider).bind(url).bind(&hash).bind(acquired).bind(payload).execute(&mut *connection).await?;
    Ok(sqlx::query_scalar(
        "SELECT id FROM source_documents WHERE provider=$1 AND url=$2 AND content_hash=$3",
    )
    .bind(provider)
    .bind(url)
    .bind(hash)
    .fetch_one(connection)
    .await?)
}
pub struct Ingester {
    pool: PgPool,
    providers: Arc<Providers>,
    clock: Arc<dyn Clock>,
    calendar: Arc<Calendar>,
    resources: Arc<dyn ResourceCheck>,
}
struct RunLease(sqlx::pool::PoolConnection<sqlx::Postgres>);
impl RunLease {
    async fn acquire(pool: &PgPool) -> Result<Self> {
        let mut connection = pool.acquire().await?;
        connection.close_on_drop();
        let acquired: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock(753194023)")
            .fetch_one(&mut *connection)
            .await?;
        if !acquired {
            return Err(invalid("Another research ingestion run is active"));
        }
        Ok(Self(connection))
    }
    async fn finish(mut self) -> Result<()> {
        sqlx::query("SELECT pg_advisory_unlock(753194023)")
            .execute(&mut *self.0)
            .await?;
        self.0.close().await?;
        Ok(())
    }
}
impl Ingester {
    pub fn new(
        pool: PgPool,
        providers: Arc<Providers>,
        clock: Arc<dyn Clock>,
        calendar: Arc<Calendar>,
        resources: Arc<dyn ResourceCheck>,
    ) -> Self {
        Self {
            pool,
            providers,
            clock,
            calendar,
            resources,
        }
    }
    pub async fn prices(&self, symbol: &str) -> Result<Value> {
        self.resources.check(PRICE_PROJECTION).await?;
        let started = self.clock.now();
        let asof = started.with_timezone(&New_York).date_naive();
        let start = asof - Duration::days(5 * 366 + 70);
        let (raw, payload, url) = self
            .providers
            .fetch_bars(symbol, start, started - Duration::minutes(20), asof)
            .await?;
        let acquired = self.clock.now();
        let bars = research_providers::normalize_bars(
            &self.calendar,
            symbol,
            &raw,
            self.providers.feed(),
            asof,
            acquired,
        )?;
        if bars.is_empty() {
            return Err(invalid("No complete exchange sessions returned"));
        }
        let mut tx = self.pool.begin().await?;
        let source = store_document(&mut tx, "alpaca_bars", &url, &payload, acquired).await?;
        for bar in &bars {
            let r = &bar.record;
            sqlx::query("INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash) VALUES($1,$2,$3,'split',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT DO NOTHING")
                .bind(symbol).bind(financials::date(&r["session_date"])?).bind(self.providers.feed()).bind(asof)
                .bind(&bar.amounts[0]).bind(&bar.amounts[1]).bind(&bar.amounts[2]).bind(&bar.amounts[3]).bind(&bar.amounts[4])
                .bind(financials::timestamp(&r["available_at"])?).bind(acquired).bind(source).bind(r["content_hash"].as_str()).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        // Corporate actions are a separate transaction, as in the original.
        // Provider unavailability does not discard successfully committed bars.
        let action_status = match self.providers.fetch_actions(symbol, start, asof).await {
            Ok((actions, url)) => {
                self.actions(symbol, &actions, &url).await?;
                "available".to_owned()
            }
            Err(Error::Invalid(message)) => message,
            Err(e) => return Err(e),
        };
        let first = financials::date(&bars[0].record["session_date"])?;
        let last = financials::date(&bars[bars.len() - 1].record["session_date"])?;
        let present: BTreeSet<_> = bars
            .iter()
            .map(|r| financials::date(&r.record["session_date"]))
            .collect::<Result<_>>()?;
        let missing: Vec<_> = self
            .calendar
            .sessions_between(first, last)?
            .into_iter()
            .filter(|d| !present.contains(d))
            .map(|d| d.to_string())
            .collect();
        Ok(
            json!({"symbol":symbol,"count":bars.len(),"first":first.to_string(),"last":last.to_string(),"feed":self.providers.feed(),"adjustment":"split","missing_sessions":missing,"corporate_actions":action_status,"price_convention":"Alpaca daily eligible-trade OHLC; no dividend adjustment"}),
        )
    }
    async fn actions(&self, symbol: &str, actions: &Value, url: &str) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        let source = store_document(
            &mut tx,
            "alpaca_corporate_actions",
            url,
            actions,
            self.clock.now(),
        )
        .await?;
        for page in actions["pages"]
            .as_array()
            .ok_or_else(|| invalid("Invalid corporate-action pages"))?
        {
            if let Some(kinds) = page["corporate_actions"].as_object() {
                for (kind, rows) in kinds {
                    for row in rows
                        .as_array()
                        .ok_or_else(|| invalid("Invalid corporate-action rows"))?
                    {
                        let hash =
                            canonical::digest(&json!({"symbol":symbol,"kind":kind,"row":row}))?;
                        let effective = ["ex_date", "process_date", "effective_date"]
                            .iter()
                            .filter_map(|key| row[key].as_str())
                            .find(|s| !s.is_empty())
                            .map(|s| {
                                s.parse::<NaiveDate>()
                                    .map_err(|_| invalid("Invalid corporate-action date"))
                            })
                            .transpose()?;
                        sqlx::query("INSERT INTO corporate_actions(symbol,action_type,effective_date,acquired_at,source_id,content_hash,payload) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING")
                            .bind(symbol).bind(kind).bind(effective).bind(self.clock.now()).bind(source).bind(hash).bind(row).execute(&mut *tx).await?;
                    }
                }
            }
        }
        tx.commit().await?;
        Ok(())
    }
    pub async fn financials(&self, symbol: &str) -> Result<Value> {
        self.resources.check(FACT_PROJECTION).await?;
        let cik: Option<Option<String>> =
            sqlx::query_scalar("SELECT cik FROM securities WHERE symbol=$1")
                .bind(symbol)
                .fetch_optional(&self.pool)
                .await?;
        let cik = cik
            .flatten()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| invalid("A verified SEC CIK is required for financial ingestion"))?;
        let (facts, submissions, facts_url, submissions_url) =
            self.providers.fetch_sec(&cik).await?;
        let acquired = self.clock.now();
        let rows = research_providers::normalize_facts(symbol, &facts, &submissions, acquired)?;
        if rows.is_empty() {
            return Err(invalid("SEC returned no supported GAAP facts"));
        }
        let mut tx = self.pool.begin().await?;
        let source =
            store_document(&mut tx, "sec_companyfacts", &facts_url, &facts, acquired).await?;
        store_document(
            &mut tx,
            "sec_submissions",
            &submissions_url,
            &submissions,
            acquired,
        )
        .await?;
        for r in &rows {
            let value = r["value"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| r["value"].to_string())
                .parse::<BigDecimal>()
                .map_err(|_| invalid("Invalid financial value"))?;
            let period_start = r
                .get("period_start")
                .filter(|v| !v.is_null())
                .map(financials::date)
                .transpose()?;
            let fiscal_year = r["fiscal_year"]
                .as_i64()
                .map(i32::try_from)
                .transpose()
                .map_err(|_| invalid("Invalid fiscal year"))?;
            sqlx::query("INSERT INTO financial_facts(symbol,metric,tag,unit,value,period_start,period_end,fiscal_year,fiscal_period,form,accession,filed_date,available_at,acquired_at,source_id,source_url,content_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17) ON CONFLICT DO NOTHING")
                .bind(symbol).bind(r["metric"].as_str()).bind(r["tag"].as_str()).bind(r["unit"].as_str()).bind(value).bind(period_start).bind(financials::date(&r["period_end"])?).bind(fiscal_year).bind(r["fiscal_period"].as_str()).bind(r["form"].as_str()).bind(r["accession"].as_str()).bind(financials::date(&r["filed_date"])?).bind(financials::timestamp(&r["available_at"])?).bind(acquired).bind(source).bind(r["source_url"].as_str()).bind(r["content_hash"].as_str()).execute(&mut *tx).await?;
        }
        let all: Vec<Value> =
            sqlx::query_scalar("SELECT to_jsonb(f) FROM financial_facts f WHERE symbol=$1")
                .bind(symbol)
                .fetch_all(&mut *tx)
                .await?;
        let metrics = financials::calculate_metrics(&all, acquired)?;
        let mut hashes = Vec::new();
        for r in &all {
            if financials::timestamp(&r["available_at"])? <= acquired {
                hashes.push(
                    r["content_hash"]
                        .as_str()
                        .ok_or_else(|| invalid("Missing fact hash"))?,
                );
            }
        }
        hashes.sort_unstable();
        let inputs = canonical::digest(&json!(hashes))?;
        sqlx::query("INSERT INTO calculated_metrics VALUES($1,$2,$3,'gaap-metrics-v3',$4,$5) ON CONFLICT DO NOTHING").bind(Uuid::new_v4()).bind(symbol).bind(acquired).bind(inputs).bind(&metrics).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(
            json!({"symbol":symbol,"facts":rows.len(),"metrics":metrics["metrics"].as_object().map_or(0,|m|m.len()),"as_of":stamp(acquired)}),
        )
    }
    pub async fn all(&self, symbols: &[String]) -> Result<Value> {
        self.resume(Uuid::new_v4(), symbols).await
    }
    /// Stable run identity is supplied by durable dispatch during retry/recovery.
    /// Evidence rows remain immutable; replay repeats the original conflict-safe inserts.
    pub async fn resume(&self, run: Uuid, symbols: &[String]) -> Result<Value> {
        self.resume_input(run, &json!(symbols)).await
    }
    pub(crate) async fn resume_input(&self, run: Uuid, input: &Value) -> Result<Value> {
        let symbols = iteration_symbols(input)?;
        let lease = RunLease::acquire(&self.pool).await?;
        let result = self.resume_inner(run, &symbols, input).await;
        lease.finish().await?;
        result
    }
    async fn resume_inner(&self, run: Uuid, symbols: &[String], input: &Value) -> Result<Value> {
        let mut tx = self.pool.begin().await?;
        let existing: Option<Value> =
            sqlx::query_scalar("SELECT to_jsonb(r) FROM ingest_runs r WHERE id=$1 FOR UPDATE")
                .bind(run)
                .fetch_optional(&mut *tx)
                .await?;
        if let Some(row) = existing {
            if &row["symbols"] != input || row["kind"] != "refresh" {
                return Err(invalid("Ingestion run identity mismatch"));
            }
            if matches!(row["status"].as_str(), Some("completed" | "partial")) {
                tx.commit().await?;
                return Ok(
                    json!({"run_id":run.to_string(),"status":row["status"],"results":row["result"]}),
                );
            }
        } else {
            sqlx::query(
                "INSERT INTO ingest_runs(id,kind,symbols,status) VALUES($1,'refresh',$2,'running')",
            )
            .bind(run)
            .bind(input)
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        let mut results = Vec::new();
        let mut partial = false;
        for symbol in symbols {
            let mut item = json!({"symbol":symbol});
            for kind in ["prices", "financials"] {
                if symbol == "SPY" && kind == "financials" {
                    continue;
                }
                let outcome = if kind == "prices" {
                    self.prices(symbol).await
                } else {
                    self.financials(symbol).await
                };
                item[kind] = match outcome {
                    Ok(value) => value,
                    Err(Error::Invalid(reason)) => {
                        partial = true;
                        json!({"status":"unavailable","reason":reason})
                    }
                    Err(e) => return Err(e),
                };
            }
            results.push(item);
        }
        let status = if partial { "partial" } else { "completed" };
        sqlx::query("UPDATE ingest_runs SET completed_at=now(),status=$2,result=$3 WHERE id=$1")
            .bind(run)
            .bind(status)
            .bind(json!(results))
            .execute(&self.pool)
            .await?;
        Ok(json!({"run_id":run.to_string(),"status":status,"results":results}))
    }
}
