//! Sourced premarket research; selections never carry order authority.
use crate::{
    Result,
    broker::BrokerApi,
    calendar::Calendar,
    canonical, db,
    engine::{Clock, stamp},
    invalid, market,
    market::DataApi,
    ranking, sentiment,
};
use chrono::{DateTime, Duration, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde_json::{Map, Value, json};
use sqlx::PgPool;
use std::{collections::BTreeSet, sync::Arc};
use tokio::sync::{Mutex, watch};

pub fn eligible_catalog(catalog: &[Value]) -> Value {
    let mut valid = Map::new();
    for asset in catalog {
        let symbol = asset["symbol"].as_str().unwrap_or("");
        let name = asset["name"].as_str().unwrap_or("");
        let excluded = name
            .split(|c: char| !c.is_alphanumeric() && c != '_')
            .any(|word| {
                matches!(
                    word.to_ascii_uppercase().as_str(),
                    "ETF"
                        | "ETN"
                        | "FUND"
                        | "TRUST"
                        | "WARRANT"
                        | "PREFERRED"
                        | "INVERSE"
                        | "LEVERAGED"
                )
            });
        if (1..=5).contains(&symbol.len())
            && symbol.bytes().all(|c| c.is_ascii_uppercase())
            && asset["tradable"] == true
            && asset["fractionable"] == true
            && asset["class"] == "us_equity"
            && matches!(
                asset["exchange"].as_str(),
                Some("NYSE" | "NASDAQ" | "AMEX" | "ARCA" | "BATS")
            )
            && !excluded
        {
            valid.insert(symbol.into(), asset.clone());
        }
    }
    Value::Object(valid)
}

/// Replacement deliberately compares the original provider strings, matching Python.
pub fn absorb(articles: &mut Map<String, Value>, rows: &[Value], started: DateTime<Utc>) {
    for article in rows {
        let parse = |key: &str| {
            article[key]
                .as_str()
                .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        };
        let (Some(created), Some(updated)) = (parse("created_at"), parse("updated_at")) else {
            continue;
        };
        if created.with_timezone(&Utc) > started || updated.with_timezone(&Utc) > started {
            continue;
        }
        let id = match &article["id"] {
            Value::String(id) => id.clone(),
            Value::Number(id) => id.to_string(),
            _ => continue,
        };
        let previous = articles.get(&id);
        if previous.is_none_or(|p| article["updated_at"].as_str() > p["updated_at"].as_str()) {
            let row: Map<String, Value> = [
                "id",
                "headline",
                "summary",
                "created_at",
                "updated_at",
                "symbols",
                "source",
                "url",
            ]
            .into_iter()
            .map(|k| (k.into(), article[k].clone()))
            .collect();
            articles.insert(id, Value::Object(row));
        }
    }
}

pub struct Acquisition {
    pub source_id: String,
    pub payload: Value,
}
pub async fn acquire(
    pool: &PgPool,
    data: &dyn DataApi,
    clock: &dyn Clock,
    started: DateTime<Utc>,
) -> Result<Acquisition> {
    let mut articles = Map::new();
    let mut token: Option<String> = None;
    let mut pages = 0;
    for _ in 0..20 {
        let page = data
            .news_page(
                started - Duration::hours(72),
                started - Duration::minutes(20),
                token.as_deref(),
            )
            .await?;
        pages += 1;
        if let Some(rows) = page["news"].as_array() {
            absorb(&mut articles, rows, started);
        }
        token = page["next_page_token"]
            .as_str()
            .filter(|t| !t.is_empty())
            .map(str::to_owned);
        if token.is_none() {
            break;
        }
    }
    let acquired = clock.now();
    let payload = json!({"articles":articles,"pages":pages,"truncated":token.is_some(),"acquired_at":stamp(acquired),"query_end":stamp(started-Duration::minutes(20))});
    let source_id = store(pool, "*", "discovery_news", &payload, None, acquired).await?;
    Ok(Acquisition { source_id, payload })
}

pub async fn store(
    pool: &PgPool,
    symbol: &str,
    provider: &str,
    payload: &Value,
    identity: Option<&Value>,
    acquired: DateTime<Utc>,
) -> Result<String> {
    let id = canonical::digest(&json!([symbol, provider, identity.unwrap_or(payload)]))?;
    sqlx::query("INSERT INTO pilot_sources VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING")
        .bind(&id)
        .bind(symbol)
        .bind(provider)
        .bind(acquired)
        .bind(payload)
        .execute(pool)
        .await?;
    Ok(id)
}

/// Liquidity screen is research eligibility, never an execution gate.
pub fn liquidity(bars: &[Value]) -> Result<(f64, f64)> {
    if bars.len() < 20 {
        return Err(invalid("Fewer than 20 liquidity observations"));
    }
    let number = |v: &Value| {
        v.as_f64()
            .or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
            .filter(|v| v.is_finite())
            .ok_or_else(|| invalid("invalid liquidity observation"))
    };
    let price = number(&bars[bars.len() - 1]["close"])?;
    let mut volume = 0.0;
    for bar in &bars[bars.len() - 20..] {
        volume += number(&bar["close"])? * number(&bar["volume"])?;
    }
    volume /= 20.0;
    if price < 5.0 || volume < 20_000_000.0 {
        return Err(invalid("Price/liquidity gate failed"));
    }
    Ok((price, volume))
}

pub struct Research {
    pub pool: PgPool,
    pub broker: Arc<dyn BrokerApi>,
    pub data: Arc<dyn DataApi>,
    pub ranker: Arc<ranking::Ranker>,
    pub sentiment: Option<Arc<dyn sentiment::Transport>>,
    pub clock: Arc<dyn Clock>,
    pub calendar: Arc<Calendar>,
    pub feed: String,
}

pub fn in_window(calendar: &Calendar, instant: DateTime<Utc>) -> bool {
    let local = instant.with_timezone(&New_York);
    calendar.session(local.date_naive()).is_some()
        && local.time() >= NaiveTime::from_hms_opt(7, 30, 0).unwrap_or(NaiveTime::MIN)
        && local.time() < NaiveTime::from_hms_opt(9, 0, 0).unwrap_or(NaiveTime::MIN)
}

async fn selected(pool: &PgPool, day: NaiveDate) -> Result<bool> {
    Ok(
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pilot_selections WHERE session_date=$1)")
            .bind(day)
            .fetch_one(pool)
            .await?,
    )
}
async fn event(pool: &PgPool, kind: &str, payload: &Value) -> Result<()> {
    sqlx::query("INSERT INTO pilot_events(kind,payload) VALUES($1,$2)")
        .bind(kind)
        .bind(payload)
        .execute(pool)
        .await?;
    Ok(())
}
fn reason(error: &crate::Error) -> String {
    match error {
        crate::Error::Invalid(text) => text.chars().take(100).collect(),
        _ => "Research input unavailable".into(),
    }
}

/// Preserve CPython's per-process string-set order and Counter first-seen ties.
/// Neither the frequency ranking nor the sixty-name screen is changed.
pub fn candidate_order(articles: &Value, valid: &Value) -> Result<Vec<(String, usize)>> {
    let seed = crate::python_set::process_seed().map_err(invalid)?;
    candidate_order_seeded(articles, valid, seed)
}
pub fn candidate_order_seeded(
    articles: &Value,
    valid: &Value,
    seed: &crate::python_set::Seed,
) -> Result<Vec<(String, usize)>> {
    let articles = articles
        .as_object()
        .ok_or_else(|| invalid("Invalid acquired news dictionary"))?;
    if !valid.is_object() {
        return Err(invalid("Invalid eligible asset catalog"));
    }
    let mut all = Vec::new();
    let mut total = 0_usize;
    let mut bytes = 0_usize;
    for article in articles.values() {
        let article = article
            .as_object()
            .ok_or_else(|| invalid("Invalid acquired news article"))?;
        let mut symbols = Vec::new();
        let mut add = |symbol: &str| -> Result<()> {
            total = total
                .checked_add(1)
                .ok_or_else(|| invalid("News symbol count exceeds resource boundary"))?;
            bytes = bytes
                .checked_add(symbol.len())
                .ok_or_else(|| invalid("News symbol bytes exceed resource boundary"))?;
            if total > crate::python_set::MAX_SYMBOLS || bytes > crate::python_set::MAX_SYMBOL_BYTES
            {
                return Err(invalid("News symbol collection exceeds resource boundary"));
            }
            symbols.push(symbol.to_owned());
            Ok(())
        };
        match article.get("symbols") {
            None | Some(Value::Null) | Some(Value::Bool(false)) => {}
            Some(Value::Number(n)) if n.as_f64() == Some(0.) => {}
            Some(Value::Array(values)) => {
                for value in values {
                    add(value
                        .as_str()
                        .ok_or_else(|| invalid("Unvalidated non-string news symbol"))?)?;
                }
            }
            // Preserve Python's string-iterable case as well as documented string arrays.
            Some(Value::String(text)) => {
                for ch in text.chars() {
                    let mut bytes = [0; 4];
                    add(ch.encode_utf8(&mut bytes))?;
                }
            }
            _ => return Err(invalid("News symbols are not a validated iterable")),
        }
        all.push(symbols);
    }
    crate::python_set::ranked(
        all.iter().map(Vec::as_slice),
        |symbol| valid.get(symbol).is_some(),
        seed,
    )
    .map_err(invalid)
}

impl Research {
    pub async fn refresh_prices(&self, symbols: &[String]) -> Result<Vec<Value>> {
        let instant = self.clock.now();
        let day = instant.with_timezone(&New_York).date_naive();
        let mut seen = BTreeSet::new();
        let mut results = Vec::new();
        for symbol in symbols {
            if !seen.insert(symbol) {
                continue;
            }
            let result:Result<Value>=async {
                let raw=self.data.bars(symbol,day-Duration::days(160),instant-Duration::minutes(20),day).await?;
                let acquired=self.clock.now();
                let bars=market::normalize_bars(&self.calendar,symbol,&raw,&self.feed,day,acquired)?;
                if bars.len()<21 {return Err(invalid("Insufficient complete history for the declared per-stock model"));}
                let url=format!("https://data.alpaca.markets/v2/stocks/{symbol}/bars");
                let prices:Vec<Value>=bars.iter().map(|bar| Value::Object(["session_date","open","high","low","close","volume"].into_iter().map(|k|(k.into(),bar[k].clone())).collect())).collect();
                let identity=json!({"prices":prices,"feed":self.feed,"snapshot_date":day.to_string()});
                let payload=json!({"bars":bars,"feed":self.feed,"adjustment":"split","url":url});
                let id=store(&self.pool,symbol,"daily_prices",&payload,Some(&identity),acquired).await?;
                Ok(json!({"symbol":symbol,"status":"available","sessions":bars.len(),"source_id":id,"last_session":bars.last().map(|b|b["session_date"].clone())}))
            }.await;
            results.push(match result {
                Ok(row)=>row,
                Err(error)=>json!({"symbol":symbol,"status":"unavailable","reason":match error {crate::Error::Invalid(_)=>"ValueError",_=>"ProviderError"}}),
            });
        }
        event(&self.pool, "price_refresh", &json!(results)).await?;
        Ok(results)
    }

    pub async fn research(&self, session: Option<NaiveDate>, preview: bool) -> Result<Value> {
        let started = self.clock.now();
        let local = started.with_timezone(&New_York);
        let session = session.unwrap_or(local.date_naive());
        let cutoff = New_York
            .from_local_datetime(
                &session
                    .and_hms_opt(9, 0, 0)
                    .ok_or_else(|| invalid("invalid research session"))?,
            )
            .single()
            .ok_or_else(|| invalid("invalid research cutoff"))?
            .with_timezone(&Utc);
        if !preview {
            if session != local.date_naive() || !in_window(&self.calendar, started) {
                return Ok(
                    json!({"status":"skipped","reason":"Outside premarket research window; no backdating"}),
                );
            }
            if selected(&self.pool, session).await? {
                return Ok(json!({"status":"already_frozen"}));
            }
        }
        let valid = eligible_catalog(&self.broker.active_equities().await?);
        let acquired =
            acquire(&self.pool, self.data.as_ref(), self.clock.as_ref(), started).await?;
        let articles = &acquired.payload["articles"];
        let ordered = candidate_order(articles, &valid)?;
        let mut candidates = Map::new();
        let mut rejections = Vec::new();
        for (symbol, _) in ordered {
            let result:Result<Value>=async {
                let raw=self.data.bars(&symbol,local.date_naive()-Duration::days(65),started-Duration::minutes(20),local.date_naive()).await?;
                let bars=market::normalize_bars(&self.calendar,&symbol,&raw,&self.feed,local.date_naive(),self.clock.now())?;
                let (price,volume)=liquidity(&bars)?;
                Ok(json!({"price":price,"dollar_volume_20":volume,"source_url":format!("https://data.alpaca.markets/v2/stocks/{symbol}/bars"),"name":valid[&symbol]["name"]}))
            }.await;
            match result {
                Ok(row) => {
                    candidates.insert(symbol, row);
                    if candidates.len() >= 40 {
                        break;
                    }
                }
                Err(error) => rejections.push(json!({"symbol":symbol,"reason":reason(&error)})),
            }
        }
        if candidates.is_empty() {
            return Err(invalid(
                "No news-supported liquid stock candidates; no fallback to fixed watchlist",
            ));
        }
        let candidates = Value::Object(candidates);
        let tone = match &self.sentiment {
            Some(transport) => Some(
                sentiment::assess(
                    &self.pool,
                    transport.as_ref(),
                    &self.clock,
                    &candidates,
                    articles,
                    &acquired.source_id,
                )
                .await?,
            ),
            None => None,
        };
        let ranked = self
            .ranker
            .rank(session, &candidates, articles, tone.as_ref())
            .await?;
        let mut symbols: Vec<_> = ranked.picks.iter().map(|p| p.symbol.clone()).collect();
        symbols.extend(["SPY".into(), "XLK".into()]);
        let coverage = self.refresh_prices(&symbols).await?;
        let complete = self.clock.now();
        let payload = json!({"version":if tone.is_some(){"news-research-pilot-v4.2-hf-balanced-context"}else{"news-research-pilot-v4.2-balanced-context"},"hf_sentiment":tone,
            "started_at":stamp(started),"completed_at":stamp(complete),"news_source_id":acquired.source_id,"model":ranked.metadata,"picks":ranked.picks,
            "candidates":candidates,"coverage":coverage,"rejections":rejections,"preview":preview,"probability_authority":"Numerical model only; Groq output is not a win probability"});
        let id = canonical::digest(&payload)?;
        let mut tx = self.pool.begin().await?;
        sqlx::query("INSERT INTO pilot_research_runs VALUES($1,$2,$3,$4,$5)")
            .bind(&id)
            .bind(session)
            .bind(if preview { "preview" } else { "scheduled" })
            .bind(complete)
            .bind(&payload)
            .execute(&mut *tx)
            .await?;
        if !preview && complete < cutoff {
            let eligible: BTreeSet<_> = coverage
                .iter()
                .filter(|r| r["status"] == "available")
                .filter_map(|r| r["symbol"].as_str())
                .collect();
            let symbols: Vec<_> = ranked
                .picks
                .iter()
                .filter(|p| eligible.contains(p.symbol.as_str()))
                .map(|p| p.symbol.as_str())
                .collect();
            if !eligible.contains("SPY") {
                return Err(invalid(
                    "Benchmark history unavailable; daily selection blocked",
                ));
            }
            sqlx::query("INSERT INTO pilot_selections VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING")
                .bind(session)
                .bind(&id)
                .bind(complete)
                .bind(json!(symbols))
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await?;
        event(&self.pool,"daily_research_complete",&json!({"research_id":id,"symbols":ranked.picks.iter().map(|p|p.symbol.as_str()).collect::<Vec<_>>(),"preview":preview,"before_cutoff":complete<cutoff})).await?;
        Ok(
            json!({"status":if preview{"preview"}else if complete<cutoff{"frozen"}else{"missed_cutoff"},"research_id":id,"picks":ranked.picks}),
        )
    }
}

/// At most one attempt per twenty minutes, checked once per minute; no order worker.
pub struct Scheduler {
    pub research: Arc<Research>,
    pub enabled: bool,
    pub attempted: Mutex<Option<DateTime<Utc>>>,
}
impl Scheduler {
    pub async fn tick(&self) -> Result<()> {
        let instant = self.research.clock.now();
        if !self.enabled || !in_window(&self.research.calendar, instant) {
            return Ok(());
        }
        let mut attempted = self.attempted.lock().await;
        if attempted.is_some_and(|at| instant - at < Duration::minutes(20)) {
            return Ok(());
        }
        *attempted = Some(instant);
        if !selected(
            &self.research.pool,
            instant.with_timezone(&New_York).date_naive(),
        )
        .await?
        {
            let result = self.research.research(None, false).await;
            let at = stamp(self.research.clock.now());
            let (kind, payload) = match result {
                Ok(result) => ("research_update", json!({"at":at,"result":result})),
                Err(error) => (
                    "research_failure",
                    json!({"at":at,"reason":match error {crate::Error::Invalid(_)=>"ValueError",crate::Error::Database(_)=>"DatabaseError",_=>"ProviderError"}}),
                ),
            };
            db::record(&self.research.pool, "shared", kind, &payload).await?;
        }
        Ok(())
    }
    pub async fn run(&self, mut stop: watch::Receiver<bool>) -> Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(60));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            if *stop.borrow() {
                return Ok(());
            }
            tokio::select! {
                _=interval.tick()=>{self.tick().await?;},
                changed=stop.changed()=>{if changed.is_err() || *stop.borrow(){return Ok(());}},
            }
        }
    }
}
