//! Existing table identities and append-only evidence are preserved unchanged.
use crate::{
    Result,
    allocation::QuantityChange,
    broker::{BrokerApi, BrokerError, OrderBody},
    canonical, invalid,
};
use bigdecimal::BigDecimal;
use serde_json::{Value, json};
use sqlx::{
    Executor, PgPool, Row,
    postgres::{PgPoolOptions, PgRow},
};
use std::{str::FromStr, time::Duration};

const PENDING: &str = "state NOT IN ('filled','canceled','expired','rejected','replaced')";

pub async fn migrate_paper(pool: &PgPool) -> Result<()> {
    let mut tx = pool.begin().await?;
    migrate_paper_in(&mut tx, true).await?;
    tx.commit().await?;
    Ok(())
}
pub(crate) async fn migrate_paper_in(
    connection: &mut sqlx::PgConnection,
    learning: bool,
) -> Result<()> {
    sqlx::query("SELECT pg_advisory_xact_lock(753194100)")
        .execute(&mut *connection)
        .await?;
    (&mut *connection)
        .execute(include_str!("../migrations/paper/legacy.sql"))
        .await?;
    if learning {
        (&mut *connection)
            .execute(include_str!("../migrations/paper/learning.sql"))
            .await?;
    }
    Ok(())
}

pub async fn migrate_research(pool: &PgPool) -> Result<()> {
    let mut tx = pool.begin().await?;
    migrate_research_in(&mut tx).await?;
    tx.commit().await?;
    Ok(())
}
pub(crate) async fn migrate_research_in(connection: &mut sqlx::PgConnection) -> Result<()> {
    let migrations = [
        (
            "001_foundation.sql",
            include_str!("../migrations/research/001_foundation.sql"),
        ),
        (
            "002_scheduler.sql",
            include_str!("../migrations/research/002_scheduler.sql"),
        ),
        (
            "003_evaluation_identity.sql",
            include_str!("../migrations/research/003_evaluation_identity.sql"),
        ),
        (
            "004_research_ledger.sql",
            include_str!("../migrations/research/004_research_ledger.sql"),
        ),
        (
            "005_price_snapshot_semantics.sql",
            include_str!("../migrations/research/005_price_snapshot_semantics.sql"),
        ),
        (
            "006_phase3.sql",
            include_str!("../migrations/research/006_phase3.sql"),
        ),
    ];
    sqlx::query("SELECT pg_advisory_xact_lock(753194002)")
        .execute(&mut *connection)
        .await?;
    sqlx::query("CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY,applied_at TIMESTAMPTZ DEFAULT now())").execute(&mut *connection).await?;
    for (name, sql) in migrations {
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name=$1)")
                .bind(name)
                .fetch_one(&mut *connection)
                .await?;
        if !exists {
            (&mut *connection).execute(sql).await?;
            sqlx::query("INSERT INTO schema_migrations(name) VALUES($1)")
                .bind(name)
                .execute(&mut *connection)
                .await?;
        }
    }
    Ok(())
}

pub struct CycleLease {
    connection: sqlx::pool::PoolConnection<sqlx::Postgres>,
}
impl CycleLease {
    pub async fn acquire(pool: &PgPool) -> Result<Option<Self>> {
        let mut connection = pool.acquire().await?;
        // Closing on cancellation/drop is essential: a pooled session must
        // never retain the advisory lock after its owner future disappears.
        connection.close_on_drop();
        let acquired: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock(753194199)")
            .fetch_one(&mut *connection)
            .await?;
        Ok(if acquired {
            Some(Self { connection })
        } else {
            None
        })
    }
    pub async fn finish(mut self) -> Result<()> {
        sqlx::query("SELECT pg_advisory_unlock(753194199)")
            .execute(&mut *self.connection)
            .await?;
        self.connection.close().await?;
        Ok(())
    }
}

pub async fn pool(uri: &str, paper: bool) -> Result<PgPool> {
    let parsed = url::Url::parse(uri).map_err(|_| invalid("database URI"))?;
    let name = parsed.path().trim_start_matches('/');
    let valid = if paper {
        name == "intelligence_paper" || name.starts_with("intelligence_paper_test_")
    } else {
        name == "intelligence"
            || name.starts_with("intelligence_test_")
            || name.starts_with("intelligence_restore_")
    };
    if !valid {
        return Err(invalid("database target is outside Mainkraft"));
    }
    Ok(PgPoolOptions::new()
        .max_connections(6)
        .acquire_timeout(Duration::from_secs(5))
        .connect(uri)
        .await?)
}

pub async fn record(pool: &PgPool, account: &str, kind: &str, payload: &Value) -> Result<String> {
    let mut connection = pool.acquire().await?;
    record_connection(&mut connection, account, kind, payload).await
}

pub async fn record_connection(
    connection: &mut sqlx::PgConnection,
    account: &str,
    kind: &str,
    payload: &Value,
) -> Result<String> {
    let id = canonical::digest(&json!([account, kind, payload]))?;
    sqlx::query("INSERT INTO learning_records(id,account_id,kind,payload) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING")
        .bind(&id).bind(account).bind(kind).bind(payload).execute(connection).await?;
    Ok(id)
}

pub fn client_id(account: &str, prediction: &str, symbol: &str, phase: &str) -> Result<String> {
    Ok(format!(
        "mkx-{}",
        &canonical::digest(&json!([account, prediction, symbol, phase]))?[..38]
    ))
}

pub async fn claim(
    pool: &PgPool,
    account: &str,
    prediction: &str,
    symbol: &str,
    change: &QuantityChange,
) -> Result<Option<OrderBody>> {
    let key = client_id(account, prediction, symbol, &change.phase)?;
    let body = OrderBody {
        symbol: symbol.into(),
        side: change.side.clone(),
        qty: change.qty.clone(),
        order_type: "market".into(),
        time_in_force: "day".into(),
        client_order_id: key.clone(),
    };
    let mut transaction = pool.begin().await?;
    let enabled: bool =
        sqlx::query_scalar("SELECT enabled FROM learning_controls WHERE id=1 FOR SHARE")
            .fetch_one(&mut *transaction)
            .await?;
    if !enabled {
        return Ok(None);
    }
    let inserted=sqlx::query("INSERT INTO learning_intents(client_id,account_id,prediction_id,symbol,phase,state,body) VALUES($1,$2,$3,$4,$5,'submitting',$6) ON CONFLICT DO NOTHING")
        .bind(&key).bind(account).bind(prediction).bind(symbol).bind(&change.phase).bind(serde_json::to_value(&body)?).execute(&mut *transaction).await?.rows_affected();
    transaction.commit().await?;
    Ok(if inserted == 1 { Some(body) } else { None })
}

fn filled(order: &Value) -> Result<BigDecimal> {
    BigDecimal::from_str(order["filled_qty"].as_str().unwrap_or("0"))
        .map_err(|_| invalid("invalid cumulative fill"))
}

pub async fn observe_order(pool: &PgPool, account: &str, key: &str, order: &Value) -> Result<()> {
    let mut tx = pool.begin().await?;
    let row = sqlx::query(
        "SELECT account_id,order_payload FROM learning_intents WHERE client_id=$1 FOR UPDATE",
    )
    .bind(key)
    .fetch_one(&mut *tx)
    .await?;
    if row.try_get::<String, _>("account_id")? != account {
        return Err(invalid("order account isolation"));
    }
    let previous: Option<Value> = row.try_get("order_payload")?;
    if let Some(previous) = previous
        && filled(order)? < filled(&previous)?
    {
        return Err(invalid("broker cumulative fill regressed"));
    }
    let state = order["status"]
        .as_str()
        .ok_or_else(|| invalid("order status missing"))?;
    let payload = json!({"client_id":key,"order":order});
    let id = canonical::digest(&json!([account, "order_version", payload]))?;
    sqlx::query("INSERT INTO learning_records(id,account_id,kind,payload) VALUES($1,$2,'order_version',$3) ON CONFLICT DO NOTHING")
        .bind(id).bind(account).bind(payload).execute(&mut *tx).await?;
    sqlx::query("UPDATE learning_intents SET state=$1,order_payload=$2 WHERE client_id=$3")
        .bind(state)
        .bind(order)
        .bind(key)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}

pub async fn pending(pool: &PgPool, account: &str) -> Result<Vec<PgRow>> {
    Ok(sqlx::query(&format!(
        "SELECT * FROM learning_intents WHERE account_id=$1 AND {PENDING}"
    ))
    .bind(account)
    .fetch_all(pool)
    .await?)
}
pub async fn reconcile(pool: &PgPool, account: &str, broker: &dyn BrokerApi) -> Result<usize> {
    let rows = pending(pool, account).await?;
    if rows.is_empty() {
        return Ok(0);
    }
    for row in rows {
        let key: String = row.try_get("client_id")?;
        if let Some(order) = broker
            .by_client(&key)
            .await
            .map_err(|_| invalid("broker reconciliation unavailable"))?
        {
            observe_order(pool, account, &key, &order).await?;
        }
    }
    Ok(pending(pool, account).await?.len())
}

pub async fn submit(
    pool: &PgPool,
    account: &str,
    broker: &dyn BrokerApi,
    prediction: &str,
    symbol: &str,
    change: &QuantityChange,
    at: &str,
) -> Result<()> {
    let Some(body) = claim(pool, account, prediction, symbol, change).await? else {
        return Ok(());
    };
    let key = &body.client_order_id;
    match broker.submit(&body).await {
        Ok(order) => observe_order(pool, account, key, &order).await?,
        Err(BrokerError::Rejected(reason)) => {
            sqlx::query("UPDATE learning_intents SET state='rejected' WHERE client_id=$1")
                .bind(key)
                .execute(pool)
                .await?;
            record(
                pool,
                account,
                "order_rejected",
                &json!({"client_id":key,"at":at,"reason":reason}),
            )
            .await?;
        }
        Err(_) => {
            sqlx::query("UPDATE learning_intents SET state='unknown' WHERE client_id=$1")
                .bind(key)
                .execute(pool)
                .await?;
            record(
                pool,
                account,
                "unknown_acknowledgement",
                &json!({"client_id":key,"at":at,"resubmitted":false}),
            )
            .await?;
        }
    }
    Ok(())
}

pub async fn claim_cycle(
    pool: &PgPool,
    account: &str,
    version: &str,
    slot: &str,
    at: &str,
) -> Result<bool> {
    let id = canonical::digest(&json!([account, version, slot]))?;
    Ok(sqlx::query("INSERT INTO learning_records(id,account_id,kind,payload) VALUES($1,$2,'cycle',$3) ON CONFLICT DO NOTHING")
        .bind(id).bind(account).bind(json!({"slot":slot,"at":at})).execute(pool).await?.rows_affected()==1)
}
