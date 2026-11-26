//! Durable research jobs with the original Python worker's exclusive ownership.
//! Native composition is provided through Dispatch; unmigrated workflows remain unavailable.
use crate::{Result, calendar::Calendar, canonical, engine::Clock, invalid};
use async_trait::async_trait;
use chrono::{DateTime, Timelike, Utc};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use sqlx::{PgPool, Row};
use std::sync::Arc;
use tokio::{
    sync::{Mutex, watch},
    task::JoinHandle,
};
use uuid::Uuid;

pub const KINDS: [&str; 11] = [
    "refresh",
    "issue",
    "resolve",
    "evaluate",
    "vision",
    "research",
    "audit",
    "monitor",
    "information",
    "tune",
    "nominate",
];
pub fn supported(kind: &str) -> bool {
    KINDS.contains(&kind)
}
pub async fn enqueue_tx(
    connection: &mut sqlx::PgConnection,
    kind: &str,
    payload: &Value,
) -> Result<Uuid> {
    if !supported(kind) {
        return Err(invalid("Unsupported job kind"));
    }
    sqlx::query("SELECT pg_advisory_xact_lock(753194022)")
        .execute(&mut *connection)
        .await?;
    canonical::canonical(payload)?;
    let id = Uuid::new_v4();
    sqlx::query("INSERT INTO jobs(id,kind,payload) VALUES($1,$2,$3)")
        .bind(id)
        .bind(kind)
        .bind(payload)
        .execute(&mut *connection)
        .await?;
    sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,'queued',$2)")
        .bind(id)
        .bind(json!({"kind":kind}))
        .execute(connection)
        .await?;
    Ok(id)
}
pub async fn enqueue(pool: &PgPool, kind: &str, payload: &Value) -> Result<Uuid> {
    let mut tx = pool.begin().await?;
    let id = enqueue_tx(&mut tx, kind, payload).await?;
    tx.commit().await?;
    Ok(id)
}

struct Lease {
    connection: sqlx::pool::PoolConnection<sqlx::Postgres>,
}
impl Lease {
    async fn acquire(pool: &PgPool) -> Result<Option<Self>> {
        let mut connection = pool.acquire().await?;
        connection.close_on_drop();
        let acquired: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock(753194020)")
            .fetch_one(&mut *connection)
            .await?;
        Ok(if acquired {
            Some(Self { connection })
        } else {
            None
        })
    }
    async fn finish(mut self) -> Result<()> {
        sqlx::query("SELECT pg_advisory_unlock(753194020)")
            .execute(&mut *self.connection)
            .await?;
        self.connection.close().await?;
        Ok(())
    }
}
pub enum DispatchError {
    Value(String),
    Unexpected(&'static str),
}
#[async_trait]
pub trait Dispatch: Send + Sync {
    async fn execute(
        &self,
        kind: &str,
        payload: &Value,
    ) -> std::result::Result<Value, DispatchError>;
    async fn execute_job(
        &self,
        _id: Uuid,
        kind: &str,
        payload: &Value,
    ) -> std::result::Result<Value, DispatchError> {
        self.execute(kind, payload).await
    }
}
type Task = JoinHandle<Result<Option<Value>>>;
pub struct Worker {
    pub pool: PgPool,
    pub dispatcher: Arc<dyn Dispatch>,
    inflight: Mutex<Option<Task>>,
}
impl Worker {
    pub fn new(pool: PgPool, dispatcher: Arc<dyn Dispatch>) -> Self {
        Self {
            pool,
            dispatcher,
            inflight: Mutex::new(None),
        }
    }
    /// The owned task retains the PG lease if its requesting future is cancelled.
    pub async fn run_once(&self) -> Result<Option<Value>> {
        let mut running = self.inflight.lock().await;
        if running.is_none() {
            let pool = self.pool.clone();
            let dispatch = self.dispatcher.clone();
            *running = Some(tokio::spawn(async move {
                execute_one(&pool, dispatch.as_ref()).await
            }));
        }
        let result = running
            .as_mut()
            .ok_or_else(|| invalid("Missing job task"))?
            .await
            .map_err(|_| invalid("Research job task failed"));
        running.take();
        result?
    }
    pub async fn drain(&self) -> Result<Option<Value>> {
        let mut running = self.inflight.lock().await;
        let Some(task) = running.as_mut() else {
            return Ok(None);
        };
        let result = task.await.map_err(|_| invalid("Research job task failed"));
        running.take();
        result?
    }
    pub async fn run(
        &self,
        calendar: &Calendar,
        clock: &dyn Clock,
        mut stop: watch::Receiver<bool>,
    ) -> Result<()> {
        loop {
            if *stop.borrow() || stop.has_changed().is_err() {
                self.drain().await?;
                return Ok(());
            }
            let cycle = async {
                sqlx::query("INSERT INTO worker_status VALUES('research-worker',now(),'{\"status\":\"polling\"}') ON CONFLICT(id) DO UPDATE SET heartbeat_at=now(),detail=EXCLUDED.detail").execute(&self.pool).await?;
                schedule(&self.pool, calendar, clock.now()).await?;
                self.run_once().await
            };
            let result = tokio::select! {
                result=cycle=>result,
                changed=stop.changed()=>{if changed.is_err() || *stop.borrow(){self.drain().await?;return Ok(());}continue;},
            };
            let delay = match result {
                Ok(Some(_)) => 2,
                Ok(None) => 30,
                Err(_) => {
                    tracing::error!(component = "research_worker", event = "polling_failed");
                    30
                }
            };
            tokio::select! {
                _=tokio::time::sleep(std::time::Duration::from_secs(delay))=>{},
                changed=stop.changed()=>{if changed.is_err() || *stop.borrow(){self.drain().await?;return Ok(());}},
            }
        }
    }
}
async fn execute_one(pool: &PgPool, dispatch: &dyn Dispatch) -> Result<Option<Value>> {
    let Some(lease) = Lease::acquire(pool).await? else {
        return Ok(None);
    };
    let mut tx = pool.begin().await?;
    let recovered:Vec<Uuid>=sqlx::query_scalar("UPDATE jobs SET status='queued',error='Previous worker exited; idempotent retry' WHERE status='running' RETURNING id").fetch_all(&mut *tx).await?;
    for id in recovered {
        sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,'recovered','{}')")
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    let job=sqlx::query("SELECT id,kind,payload FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'issue' THEN 0 WHEN 'refresh' THEN 1 WHEN 'resolve' THEN 2 ELSE 3 END,created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetch_optional(&mut *tx).await?;
    let Some(job) = job else {
        tx.commit().await?;
        lease.finish().await?;
        return Ok(None);
    };
    let id: Uuid = job.try_get("id")?;
    let kind: String = job.try_get("kind")?;
    let payload: Value = job.try_get("payload")?;
    sqlx::query("UPDATE jobs SET status='running',started_at=now(),heartbeat_at=now(),attempts=attempts+1 WHERE id=$1").bind(id).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,'started','{}')")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    tracing::info!(component="research_worker",event="job_started",job_id=%id,kind=%kind);
    let (result, status, error) = match dispatch.execute_job(id, &kind, &payload).await {
        Ok(result) => {
            canonical::canonical(&result)?;
            let status = if kind == "refresh" && result["status"] == "partial" {
                "partial"
            } else if kind == "issue" && result["status"] == "skipped" {
                "skipped"
            } else {
                "completed"
            };
            (result, status, None)
        }
        Err(error) => {
            let error = match error {
                DispatchError::Value(message) => message.chars().take(500).collect(),
                DispatchError::Unexpected(name) => format!("{name}: inspect local worker logs"),
            };
            (Value::Null, "failed", Some(error))
        }
    };
    let mut tx = pool.begin().await?;
    sqlx::query("UPDATE jobs SET status=$1,completed_at=now(),heartbeat_at=now(),result=$2,error=$3 WHERE id=$4").bind(status).bind(&result).bind(&error).bind(id).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO job_events(job_id,event,detail) VALUES($1,$2,$3)")
        .bind(id)
        .bind(status)
        .bind(json!({"error":error}))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    lease.finish().await?;
    tracing::info!(component="research_worker",event="job_finished",job_id=%id,kind=%kind,status=status);
    Ok(Some(
        json!({"id":id.to_string(),"status":status,"error":error,"result":result}),
    ))
}

pub fn due_tasks(calendar: &Calendar, instant: DateTime<Utc>) -> Vec<(&'static str, &'static str)> {
    let local = instant.with_timezone(&New_York);
    if calendar.session(local.date_naive()).is_none() {
        return Vec::new();
    }
    let second = local.num_seconds_from_midnight();
    [
        ("refresh", "refresh", 19 * 3600, 19 * 3600 + 30 * 60),
        (
            "refresh_1930",
            "refresh",
            19 * 3600 + 30 * 60,
            19 * 3600 + 50 * 60,
        ),
        ("refresh_1950", "refresh", 19 * 3600 + 50 * 60, 20 * 3600),
        (
            "issue",
            "issue",
            20 * 3600 + 30 * 60,
            23 * 3600 + 59 * 60 + 59,
        ),
        (
            "monitor",
            "monitor",
            20 * 3600 + 35 * 60,
            23 * 3600 + 59 * 60 + 59,
        ),
    ]
    .into_iter()
    .filter(|(_, _, start, end)| *start <= second && second < *end)
    .map(|(task, kind, _, _)| (task, kind))
    .collect()
}
pub async fn schedule(pool: &PgPool, calendar: &Calendar, instant: DateTime<Utc>) -> Result<()> {
    let tasks = due_tasks(calendar, instant);
    if tasks.is_empty() {
        return Ok(());
    }
    let day = instant.with_timezone(&New_York).date_naive();
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(753194021)")
        .execute(&mut *tx)
        .await?;
    for (task, kind) in tasks {
        let marked: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM scheduler_marks WHERE task=$1 AND session_date=$2)",
        )
        .bind(task)
        .bind(day)
        .fetch_one(&mut *tx)
        .await?;
        if marked {
            continue;
        }
        if kind == "refresh" {
            let pending:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM jobs WHERE kind='refresh' AND status IN ('queued','running'))").fetch_one(&mut *tx).await?;
            if pending {
                continue;
            }
        }
        let id = enqueue_tx(&mut tx, kind, &json!({})).await?;
        sqlx::query("INSERT INTO scheduler_marks(task,session_date,job_id) VALUES($1,$2,$3)")
            .bind(task)
            .bind(day)
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}
