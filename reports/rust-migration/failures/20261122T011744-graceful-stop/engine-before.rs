//! Faithful two-account orchestration. No alternate strategy or shadow mode.
use crate::{
    Result,
    allocation::{self, Observation},
    broker::BrokerApi,
    calendar::Calendar,
    canonical, db,
    features::Quote,
    invalid,
    learning::{Outcome, ResidualFit},
    paper_api::VERSION,
};
use async_trait::async_trait;
use chrono::{DateTime, Duration, SecondsFormat, Utc};
use futures::TryStreamExt;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Row};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::Duration as StdDuration,
};
use tokio::sync::watch;

#[derive(Clone, Debug)]
pub struct AccountPolicy {
    pub id: String,
    pub label: String,
    pub adaptive: bool,
}
pub trait Clock: Send + Sync {
    fn now(&self) -> DateTime<Utc>;
}
pub struct SystemClock;
impl Clock for SystemClock {
    fn now(&self) -> DateTime<Utc> {
        let instant = Utc::now();
        DateTime::from_timestamp_micros(instant.timestamp_micros()).unwrap_or(instant)
    }
}
pub fn stamp(instant: DateTime<Utc>) -> String {
    instant.to_rfc3339_opts(
        if instant.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    )
}
fn date(value: &Value) -> Result<DateTime<Utc>> {
    value
        .as_str()
        .ok_or_else(|| invalid("timestamp missing"))?
        .parse()
        .map_err(|_| invalid("timestamp invalid"))
}
fn number(value: &Value) -> Result<f64> {
    let n = if let Some(s) = value.as_str() {
        s.parse().map_err(|_| invalid("broker numeric value"))?
    } else {
        value
            .as_f64()
            .ok_or_else(|| invalid("numeric value missing"))?
    };
    if !f64::is_finite(n) {
        return Err(invalid("non-finite broker value"));
    }
    Ok(n)
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64() != Some(0.),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

#[async_trait]
pub trait MarketApi: Send + Sync {
    async fn universe(&self) -> Result<Vec<String>>;
    async fn observations(
        &self,
        symbols: &[String],
        broker: &dyn BrokerApi,
        instant: DateTime<Utc>,
    ) -> Result<Vec<Observation>>;
    async fn quote(&self, symbol: &str) -> Result<Quote>;
}

pub struct Engine {
    pool: PgPool,
    accounts: Vec<AccountPolicy>,
    brokers: BTreeMap<String, Arc<dyn BrokerApi>>,
    market: Arc<dyn MarketApi>,
    clock: Arc<dyn Clock>,
    calendar: Arc<Calendar>,
    last_slot: BTreeMap<String, String>,
    last_snapshot: BTreeMap<String, DateTime<Utc>>,
    last_enabled: BTreeMap<String, bool>,
}
impl Engine {
    pub fn new(
        pool: PgPool,
        accounts: Vec<AccountPolicy>,
        brokers: BTreeMap<String, Arc<dyn BrokerApi>>,
        market: Arc<dyn MarketApi>,
        clock: Arc<dyn Clock>,
        calendar: Arc<Calendar>,
    ) -> Result<Self> {
        let ids: BTreeSet<_> = accounts.iter().map(|a| a.id.as_str()).collect();
        if accounts.len() != 2
            || ids.len() != 2
            || ids.contains("")
            || brokers.len() != 2
            || ids.iter().any(|id| !brokers.contains_key(*id))
            || accounts[0].adaptive
            || !accounts[1].adaptive
        {
            return Err(invalid(
                "exactly two distinct pinned paper accounts required",
            ));
        }
        Ok(Self {
            pool,
            accounts,
            brokers,
            market,
            clock,
            calendar,
            last_slot: BTreeMap::new(),
            last_snapshot: BTreeMap::new(),
            last_enabled: BTreeMap::new(),
        })
    }

    pub async fn tick(&mut self) -> Result<()> {
        let Some(lease) = db::CycleLease::acquire(&self.pool).await? else {
            return Ok(());
        };
        for account in self.accounts.clone() {
            let broker = self
                .brokers
                .get(&account.id)
                .cloned()
                .ok_or_else(|| invalid("paper broker missing"))?;
            if let Err(error) = self.process_account(&account, broker.as_ref()).await {
                let reason = error.to_string().chars().take(160).collect::<String>();
                if db::record(
                    &self.pool,
                    &account.id,
                    "runtime_failure",
                    &json!({"at":stamp(self.clock.now()),"reason":reason}),
                )
                .await
                .is_err()
                {
                    tracing::error!(account_id=%account.id,"Paper cycle and evidence write failed");
                }
            }
        }
        lease.finish().await
    }

    async fn process_account(
        &mut self,
        config: &AccountPolicy,
        broker: &dyn BrokerApi,
    ) -> Result<()> {
        let instant = self.clock.now();
        let account = &config.id;
        let pending = db::reconcile(&self.pool, account, broker).await?;
        let a = broker.account().await?;
        if a["id"].as_str() != Some(account.as_str()) {
            return Err(invalid("different pinned paper account returned"));
        }
        let mut positions = broker.positions().await?;
        let open_orders = broker.orders().await?;
        sqlx::query("INSERT INTO learning_accounts(account_id,label,account_number,baseline_equity) VALUES($1,$2,$3,$4::text::numeric) ON CONFLICT DO NOTHING")
            .bind(account).bind(&config.label).bind(a["account_number"].as_str().ok_or_else(||invalid("account number missing"))?).bind(a["equity"].as_str().ok_or_else(||invalid("equity missing"))?).execute(&self.pool).await?;
        let enabled: bool = sqlx::query_scalar("SELECT enabled FROM learning_controls WHERE id=1")
            .fetch_one(&self.pool)
            .await?;
        let clock = broker.clock().await?;
        if (instant - date(&clock["timestamp"])?).abs() > Duration::seconds(30) {
            return Err(invalid("Broker clock mismatch"));
        }
        let is_open = clock["is_open"]
            .as_bool()
            .ok_or_else(|| invalid("broker market clock missing"))?;
        if self.last_enabled.get(account) != Some(&enabled)
            || self.last_snapshot.get(account).is_none_or(|previous| {
                instant - *previous >= Duration::seconds(if is_open { 60 } else { 180 })
            })
        {
            let fields = [
                "account_number",
                "cash",
                "equity",
                "buying_power",
                "regt_buying_power",
                "multiplier",
                "daytrading_buying_power",
                "pattern_day_trader",
                "daytrade_count",
                "status",
                "trading_blocked",
                "account_blocked",
                "shorting_enabled",
            ];
            let balances: serde_json::Map<String, Value> = fields
                .into_iter()
                .map(|key| (key.into(), a.get(key).cloned().unwrap_or(Value::Null)))
                .collect();
            db::record(&self.pool,account,"snapshot",&json!({"at":stamp(instant),"account":balances,"positions":positions,"open_orders":open_orders,"enabled":enabled,"market_open":is_open,"next_open":clock.get("next_open"),"policy":if config.adaptive{"adaptive"}else{"control"}})).await?;
            self.last_snapshot.insert(account.clone(), instant);
            self.last_enabled.insert(account.clone(), enabled);
        }
        let slot = instant.timestamp().div_euclid(300).to_string();
        if !enabled || !is_open || self.last_slot.get(account) == Some(&slot) {
            return Ok(());
        }
        if truthy(&a["account_blocked"]) || truthy(&a["trading_blocked"]) {
            return Err(invalid("Broker account blocks trading"));
        }
        if open_orders.len() >= 500 {
            return Err(invalid("Broker open-order page is incomplete"));
        }
        self.last_slot.insert(account.clone(), slot.clone());
        let mut symbols: BTreeSet<_> = self.market.universe().await?.into_iter().collect();
        for position in &positions {
            symbols.insert(
                position["symbol"]
                    .as_str()
                    .ok_or_else(|| invalid("position symbol missing"))?
                    .into(),
            );
        }
        let observations = self
            .market
            .observations(&symbols.into_iter().collect::<Vec<_>>(), broker, instant)
            .await?;
        if observations.is_empty() {
            return Err(invalid("No causal observations with fresh quotes"));
        }
        let beta = self
            .resolve_and_learn(config, &observations, self.clock.now())
            .await?;
        if pending > 0 || !open_orders.is_empty() {
            return Ok(());
        }
        if !db::claim_cycle(
            &self.pool,
            account,
            VERSION,
            &slot,
            &stamp(self.clock.now()),
        )
        .await?
        {
            return Ok(());
        }
        let exposure = positions
            .iter()
            .map(|p| number(&p["market_value"]).map(f64::abs))
            .collect::<Result<Vec<_>>>()?
            .iter()
            .sum::<f64>();
        let power = number(&a["buying_power"])?;
        let regt = if truthy(&a["regt_buying_power"]) {
            number(&a["regt_buying_power"])?
        } else {
            power
        };
        let mut plan = allocation::allocate(
            &observations,
            power.min(regt).max(0.) + exposure,
            config.adaptive,
            &format!("{account}{slot}"),
            beta.as_deref(),
        )?;
        let mut predictions = BTreeMap::new();
        for (symbol, target) in &mut plan {
            let quote = self.market.quote(symbol).await?;
            quote.validate(self.clock.now())?;
            target.midpoint = quote.midpoint();
            let at = self.clock.now();
            let target_at = self.calendar.target_time(self.clock.now())?;
            let id=db::record(&self.pool,account,"prediction",&json!({"at":stamp(at),"target_time":stamp(target_at),"symbol":symbol,"x":target.x,"midpoint":target.midpoint,
                "prior_return":target.prior_return,"forecast_return":target.forecast_return,"target_value":target.target_value,"slot":slot,
                "source_id":target.source_id,"policy":if config.adaptive{"adaptive"}else{"control"},"policy_version":VERSION,"round_trip_cost_allowance":target.cost_allowance,
                "issuance_quote":{"bid":quote.bid,"ask":quote.ask,"source_time":stamp(quote.source_time),"acquired_at":stamp(quote.acquired_at),"feed":quote.feed},
                "forecast_basis":"Uncalibrated daily gross-return prior scaled to one trading hour; residual update from prior matured midpoint returns"})).await?;
            predictions.insert(symbol.clone(), id);
        }
        for phase in ["reduce", "increase"] {
            for (symbol, target) in &plan {
                if broker.clock().await?["is_open"].as_bool() != Some(true) {
                    break;
                }
                let quote = self.market.quote(symbol).await?;
                quote.validate(self.clock.now())?;
                let price = if target.target_value >= 0. {
                    quote.ask
                } else {
                    quote.bid
                };
                positions = broker.positions().await?;
                let current = positions
                    .iter()
                    .find(|p| p["symbol"].as_str() == Some(symbol.as_str()));
                let change = allocation::quantity_change(
                    current.and_then(|p| p["qty"].as_str()),
                    target.target_value,
                    price,
                    target.asset["fractionable"].as_bool().unwrap_or(false),
                    target.asset["shortable"].as_bool().unwrap_or(false),
                )?;
                let Some(change) = change else {
                    continue;
                };
                if change.phase != phase {
                    continue;
                }
                let fresh = broker.account().await?;
                if phase == "increase"
                    && !allocation::within_buying_power(
                        &change.qty,
                        price,
                        fresh["buying_power"]
                            .as_str()
                            .ok_or_else(|| invalid("buying power missing"))?,
                    )?
                {
                    db::record(
                        &self.pool,
                        account,
                        "broker_capacity_wait",
                        &json!({"symbol":symbol,"at":stamp(self.clock.now())}),
                    )
                    .await?;
                    continue;
                }
                db::submit(
                    &self.pool,
                    account,
                    broker,
                    predictions
                        .get(symbol)
                        .ok_or_else(|| invalid("prediction missing"))?,
                    symbol,
                    &change,
                    &stamp(self.clock.now()),
                )
                .await?;
                if db::reconcile(&self.pool, account, broker).await? > 0 {
                    break;
                }
            }
            if db::reconcile(&self.pool, account, broker).await? > 0 {
                break;
            }
        }
        Ok(())
    }

    pub async fn resolve_and_learn(
        &self,
        config: &AccountPolicy,
        observations: &[Observation],
        instant: DateTime<Utc>,
    ) -> Result<Option<Vec<f64>>> {
        let mut transaction = self.pool.begin().await?;
        let pending=sqlx::query("SELECT id,payload FROM learning_records p WHERE account_id=$1 AND kind='prediction' AND NOT EXISTS(SELECT 1 FROM learning_records o WHERE o.kind IN ('outcome','outcome_unavailable') AND o.payload->>'prediction_id'=p.id)").bind(&config.id).fetch_all(&mut *transaction).await?;
        for row in pending {
            let id: String = row.try_get("id")?;
            let p: Value = row.try_get("payload")?;
            let target = date(&p["target_time"])?;
            let observation = observations
                .iter()
                .find(|o| p["symbol"].as_str() == Some(o.symbol.as_str()));
            let quote_time = observation.and_then(|o| o.quote_time).unwrap_or(instant);
            if target <= quote_time
                && quote_time <= target + Duration::minutes(5)
                && let Some(observation) = observation
            {
                let midpoint = number(&p["midpoint"])?;
                if midpoint <= 0. {
                    return Err(invalid("invalid prediction midpoint"));
                }
                db::record_connection(&mut transaction,&config.id,"outcome",&json!({"prediction_id":id,"symbol":p["symbol"],"x":p["x"],"prior_return":p["prior_return"],"forecast_return":p["forecast_return"],"actual_return":observation.midpoint/midpoint-1.,"resolved_at":stamp(instant),"mark_basis":"IEX midpoint; hypothetical forecast outcome, not fill P/L"})).await?;
            } else if instant > target + Duration::minutes(5) {
                db::record_connection(&mut transaction,&config.id,"outcome_unavailable",&json!({"prediction_id":id,"at":stamp(instant),"reason":"Fresh target-window quote unavailable"})).await?;
            }
        }
        transaction.commit().await?;
        if !config.adaptive {
            return Ok(None);
        }
        let count: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM learning_records WHERE account_id=$1 AND kind='outcome'",
        )
        .bind(&config.id)
        .fetch_one(&self.pool)
        .await?;
        if count == 0 {
            return Ok(None);
        }
        let previous:Option<Value>=sqlx::query_scalar("SELECT payload FROM learning_records WHERE account_id=$1 AND kind='model_update' ORDER BY recorded_at DESC LIMIT 1").bind(&config.id).fetch_optional(&self.pool).await?;
        if let Some(previous) = previous
            && previous["observations"].as_i64() == Some(count)
        {
            return Ok(Some(serde_json::from_value(
                previous["coefficients"].clone(),
            )?));
        }
        let mut fit = ResidualFit::default();
        let mut identity = Sha256::new();
        identity.update(b"[");
        let mut index = 0;
        let mut stream=sqlx::query("SELECT payload FROM learning_records WHERE account_id=$1 AND kind='outcome' ORDER BY recorded_at").bind(&config.id).fetch(&self.pool);
        while let Some(row) = stream.try_next().await? {
            let p: Value = row.try_get("payload")?;
            let outcome: Outcome = serde_json::from_value(p.clone())?;
            fit.push(&outcome)?;
            if index > 0 {
                identity.update(b",");
            }
            let prediction_id = p["prediction_id"]
                .as_str()
                .ok_or_else(|| invalid("outcome prediction identity missing"))?;
            identity.update(canonical::canonical(&json!(prediction_id))?.as_bytes());
            index += 1;
            if index % 1024 == 0 {
                tokio::task::yield_now().await;
            }
        }
        identity.update(b"]");
        let beta = fit.finish()?;
        db::record(&self.pool,&config.id,"model_update",&json!({"observations":count,"outcome_identity":hex::encode(identity.finalize()),"coefficients":beta,"method":"prequential residual ridge alpha=100","at":stamp(instant)})).await?;
        Ok(Some(beta))
    }

    pub async fn run(mut self, mut stop: watch::Receiver<bool>) {
        loop {
            if *stop.borrow() {
                break;
            }
            tokio::select! {
                result=self.tick()=>{if let Err(error)=result{tracing::error!(reason=%error,"Paper cycle failed");}},
                changed=stop.changed()=>{if changed.is_err() || *stop.borrow(){break;}}
            }
            tokio::select! {_=tokio::time::sleep(StdDuration::from_secs(15))=>{},changed=stop.changed()=>{if changed.is_err() || *stop.borrow(){break;}}}
        }
    }
}
