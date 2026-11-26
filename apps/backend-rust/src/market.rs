//! Causal daily data and IEX execution marks, matching the deployed experiment.
//! The hourly scaling below is explicitly legacy behavior, not a trained hourly model.
use crate::{
    Result,
    allocation::Observation,
    broker::{BrokerApi, valid_symbol},
    calendar::Calendar,
    canonical, db,
    engine::{Clock, MarketApi, stamp},
    features::{self, Bar, Quote},
    invalid,
    models::NumericModel,
};
use async_trait::async_trait;
use chrono::{DateTime, Duration, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::Duration as StdDuration,
};
use tokio::sync::{RwLock, Semaphore};

fn api_rows<'a>(response: &'a Value, key: &str) -> Result<&'a [Value]> {
    match response.get(key) {
        Some(Value::Null) => Ok(&[]),
        Some(Value::Array(rows)) => Ok(rows),
        _ => Err(invalid("Malformed market-data row container")),
    }
}
pub const LEGACY_TRADING_DAY_HOURS: f64 = 6.5;
pub const LEGACY_DAILY_COST: f64 = 0.0025;
pub struct DataCredentials {
    pub key: String,
    pub secret: String,
    pub feed: String,
}
#[async_trait]
pub trait DataApi: Send + Sync {
    async fn news_page(
        &self,
        _start: DateTime<Utc>,
        _end: DateTime<Utc>,
        _token: Option<&str>,
    ) -> Result<Value> {
        Err(invalid("news feed unavailable"))
    }
    async fn bars(
        &self,
        symbol: &str,
        start: NaiveDate,
        end: DateTime<Utc>,
        asof: NaiveDate,
    ) -> Result<Vec<Value>>;
    async fn latest_quote(&self, symbol: &str) -> Result<Value>;
}

pub struct AlpacaData {
    credentials: DataCredentials,
    client: reqwest::Client,
    requests: Semaphore,
}
impl AlpacaData {
    /// Real current inputs, explicitly separate from reconstructed historical
    /// availability. The caller retains risk management for any missing symbol.
    pub async fn hourly_inputs(
        &self,
        calendar: &Calendar,
        symbols: &[String],
        news: &[crate::intraday::features::News],
    ) -> Result<crate::intraday::data::CurrentSnapshot> {
        if symbols.len() > 6 || symbols.iter().any(|s| !valid_symbol(s)) {
            return Err(invalid("Bounded hourly universe required"));
        }
        let instant = Utc::now();
        let day = instant.with_timezone(&New_York).date_naive();
        let mut snapshot = crate::intraday::data::CurrentSnapshot {
            observed_at: instant,
            inputs: Vec::new(),
            quotes: BTreeMap::new(),
            failures: Vec::new(),
        };
        if !calendar
            .session(day)
            .is_some_and(|s| instant >= s.open && instant < s.close)
        {
            snapshot
                .failures
                .push(json!({"reason":"MARKET_CLOSED","delayed_data_substituted":false}));
            return Ok(snapshot);
        }
        let names: BTreeSet<_> = symbols
            .iter()
            .cloned()
            .chain(["SPY".into(), "XLK".into()])
            .collect();
        let mut rows = Vec::new();
        for name in names {
            for minutes in [1, 5] {
                match self
                    .minute_bars(&name, minutes, instant - Duration::days(7), instant, false)
                    .await
                {
                    Ok(mut bars) => rows.append(&mut bars),
                    Err(e) => snapshot
                        .failures
                        .push(json!({"symbol":name,"minutes":minutes,"reason":e.to_string()})),
                }
            }
        }
        for symbol in symbols {
            let result: Result<_> = async {
                let row = self.latest_quote(symbol).await?;
                let at = Utc::now();
                let quote = Quote {
                    bid: numeric(&row["bp"])?,
                    ask: numeric(&row["ap"])?,
                    source_time: datetime(&row["t"])?,
                    acquired_at: at,
                    feed: "iex".into(),
                };
                quote.validate(at)?;
                snapshot.quotes.insert(symbol.clone(), quote.clone());
                crate::intraday::features::build(
                    calendar,
                    &rows,
                    news,
                    symbol,
                    at,
                    Some(&quote),
                    false,
                )
            }
            .await;
            match result {
                Ok(input) => snapshot.inputs.push(input),
                Err(e) => snapshot
                    .failures
                    .push(json!({"symbol":symbol,"reason":e.to_string()})),
            }
        }
        snapshot.observed_at = Utc::now();
        Ok(snapshot)
    }
    /// Last observable quote in seconds 0..14 of each minute. Sizes use the
    /// provider's documented shares convention from 2025-11-03 onward.
    pub async fn historical_quote_samples(
        &self,
        symbol: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        feed: &str,
    ) -> Result<Vec<crate::intraday::data::QuoteSample>> {
        if !valid_symbol(symbol)
            || !matches!(feed, "iex" | "sip")
            || end <= start
            || end - start > Duration::days(7)
        {
            return Err(invalid("Invalid historical quote request"));
        }
        let mut query = vec![
            ("start".into(), stamp(start)),
            ("end".into(), stamp(end)),
            ("feed".into(), feed.into()),
            ("sort".into(), "asc".into()),
            ("limit".into(), "10000".into()),
        ];
        let mut samples = BTreeMap::new();
        let mut seen = BTreeSet::new();
        for _ in 0..50 {
            let response = self
                .get(&format!("/v2/stocks/{symbol}/quotes"), &query)
                .await?;
            let acquired = Utc::now();
            for row in api_rows(&response, "quotes")? {
                let time = datetime(&row["t"])?;
                if time < start || time > end || time.timestamp().rem_euclid(60) >= 15 {
                    continue;
                }
                let quote = Quote {
                    bid: numeric(&row["bp"])?,
                    ask: numeric(&row["ap"])?,
                    source_time: time,
                    acquired_at: acquired,
                    feed: feed.into(),
                };
                if quote.validate(time).is_err() {
                    continue;
                }
                let unit = if time.date_naive()
                    < chrono::NaiveDate::from_ymd_opt(2025, 11, 3)
                        .ok_or_else(|| invalid("Quote unit boundary"))?
                {
                    100.
                } else {
                    1.
                };
                let bid_shares = numeric(&row["bs"])? * unit;
                let ask_shares = numeric(&row["as"])? * unit;
                if bid_shares < 0. || ask_shares < 0. {
                    return Err(invalid("Invalid historical quote sizes"));
                }
                samples.insert(
                    time.timestamp().div_euclid(60),
                    crate::intraday::data::QuoteSample {
                        symbol: symbol.into(),
                        quote,
                        bid_shares,
                        ask_shares,
                        historical_reconstruction: true,
                    },
                );
            }
            let Some(token) = response["next_page_token"]
                .as_str()
                .filter(|s| !s.is_empty())
            else {
                return Ok(samples.into_values().collect());
            };
            if !seen.insert(token.to_owned()) {
                return Err(invalid("Repeated quote pagination token"));
            }
            query.retain(|(k, _)| k != "page_token");
            query.push(("page_token".into(), token.into()));
        }
        Err(invalid(
            "Historical quote page limit; partial coverage not silently accepted",
        ))
    }
    /// One real quote adjacent to a declared decision. Avoids downloading the
    /// entire tick stream and never manufactures a missing observation.
    pub async fn point_quote(
        &self,
        symbol: &str,
        at: DateTime<Utc>,
        feed: &str,
        after: bool,
    ) -> Result<Option<crate::intraday::data::QuoteSample>> {
        if !valid_symbol(symbol) || !matches!(feed, "sip" | "iex") {
            return Err(invalid("Invalid point quote request"));
        }
        let (start, end, sort) = if after {
            (at + Duration::seconds(1), at + Duration::seconds(14), "asc")
        } else {
            (
                at - Duration::seconds(15),
                at - Duration::seconds(1),
                "desc",
            )
        };
        let response = self
            .get(
                &format!("/v2/stocks/{symbol}/quotes"),
                &[
                    ("start".into(), stamp(start)),
                    ("end".into(), stamp(end)),
                    ("feed".into(), feed.into()),
                    ("sort".into(), sort.into()),
                    ("limit".into(), "1".into()),
                ],
            )
            .await?;
        let Some(row) = api_rows(&response, "quotes")?.first() else {
            return Ok(None);
        };
        let time = datetime(&row["t"])?;
        if time < start || time > end {
            return Err(invalid("Point quote outside requested interval"));
        }
        let quote = Quote {
            bid: numeric(&row["bp"])?,
            ask: numeric(&row["ap"])?,
            source_time: time,
            acquired_at: Utc::now(),
            feed: feed.into(),
        };
        quote.validate(time)?;
        let unit = if time.date_naive()
            < chrono::NaiveDate::from_ymd_opt(2025, 11, 3)
                .ok_or_else(|| invalid("Quote unit date"))?
        {
            100.
        } else {
            1.
        };
        let bid_shares = numeric(&row["bs"])? * unit;
        let ask_shares = numeric(&row["as"])? * unit;
        if bid_shares <= 0. || ask_shares <= 0. {
            return Ok(None);
        }
        Ok(Some(crate::intraday::data::QuoteSample {
            symbol: symbol.into(),
            quote,
            bid_shares,
            ask_shares,
            historical_reconstruction: true,
        }))
    }
    /// Explicit feed, raw intraday prices and bounded pagination. A 403/429 or
    /// missing bar is an error, never a silent delayed-feed replacement.
    pub async fn minute_bars(
        &self,
        symbol: &str,
        minutes: u8,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        historical: bool,
    ) -> Result<Vec<crate::intraday::features::Candle>> {
        if !valid_symbol(symbol)
            || !matches!(minutes, 1 | 5)
            || end <= start
            || end - start > Duration::days(7)
        {
            return Err(invalid(
                "Intraday request requires a valid symbol, 1/5-minute timeframe and at most seven days",
            ));
        }
        let mut query = vec![
            ("timeframe".into(), format!("{minutes}Min")),
            ("start".into(), stamp(start)),
            ("end".into(), stamp(end)),
            ("feed".into(), self.credentials.feed.clone()),
            ("adjustment".into(), "raw".into()),
            ("sort".into(), "asc".into()),
            ("limit".into(), "10000".into()),
        ];
        let mut result = Vec::new();
        let mut tokens = BTreeSet::new();
        let mut seen = BTreeSet::new();
        for _ in 0..25 {
            let response = self
                .get(&format!("/v2/stocks/{symbol}/bars"), &query)
                .await?;
            let acquired = Utc::now();
            let bars = api_rows(&response, "bars")?;
            for row in bars {
                let at = datetime(&row["t"])?;
                let completed = at + Duration::minutes(minutes as i64);
                if at < start || completed > end || completed > acquired {
                    continue;
                }
                if !seen.insert(at) {
                    return Err(invalid("Duplicate intraday page candle"));
                }
                let bar = crate::intraday::features::Candle {
                    symbol: symbol.into(),
                    start: at,
                    minutes,
                    open: numeric(&row["o"])?,
                    high: numeric(&row["h"])?,
                    low: numeric(&row["l"])?,
                    close: numeric(&row["c"])?,
                    volume: numeric(&row["v"])?,
                    vwap: row
                        .get("vw")
                        .filter(|v| !v.is_null())
                        .map(numeric)
                        .transpose()?,
                    available_at: if historical {
                        completed + Duration::seconds(1)
                    } else {
                        acquired
                    },
                    acquired_at: acquired,
                    historical_availability_assumed: historical,
                    feed: self.credentials.feed.clone(),
                };
                bar.validate()?;
                result.push(bar);
                if result.len() > 20000 {
                    return Err(invalid("Intraday page resource limit"));
                }
            }
            let Some(token) = response["next_page_token"]
                .as_str()
                .filter(|v| !v.is_empty())
            else {
                return Ok(result);
            };
            if !tokens.insert(token.to_owned()) {
                return Err(invalid(
                    "Repeated intraday page token; partial history refused",
                ));
            }
            query.retain(|(k, _)| k != "page_token");
            query.push(("page_token".into(), token.into()));
        }
        Err(invalid(
            "Intraday pagination incomplete; partial history refused",
        ))
    }
    pub fn new(credentials: DataCredentials) -> Result<Self> {
        if !matches!(credentials.feed.as_str(), "sip" | "iex") {
            return Err(invalid("unsupported data feed"));
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(StdDuration::from_secs(45))
            .build()
            .map_err(|_| invalid("data HTTP client"))?;
        Ok(Self {
            credentials,
            client,
            requests: Semaphore::new(4),
        })
    }
    async fn get(&self, path: &str, query: &[(String, String)]) -> Result<Value> {
        let mut headers = reqwest::header::HeaderMap::new();
        for (name, secret) in [
            ("apca-api-key-id", &self.credentials.key),
            ("apca-api-secret-key", &self.credentials.secret),
        ] {
            let mut value = reqwest::header::HeaderValue::from_str(secret)
                .map_err(|_| invalid("invalid data credentials"))?;
            value.set_sensitive(true);
            headers.insert(name, value);
        }
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|_| invalid("data concurrency closed"))?;
        let quote_request = path.ends_with("/quotes/latest");
        let news_request = path == "/v1beta1/news";
        for attempt in 0..4 {
            let response = self
                .client
                .get(format!("https://data.alpaca.markets{path}"))
                .query(query)
                .headers(headers.clone())
                .timeout(StdDuration::from_secs(if quote_request {
                    10
                } else if news_request {
                    20
                } else {
                    45
                }))
                .send()
                .await
                .map_err(|_| invalid("data provider connection unavailable; no substitution"))?;
            let status = response.status().as_u16();
            if !quote_request && matches!(status, 429 | 500 | 502 | 503 | 504) && attempt < 3 {
                tokio::time::sleep(StdDuration::from_secs((1u64 << attempt).min(4))).await;
                continue;
            }
            if status != 200 {
                return Err(invalid(format!(
                    "data.alpaca.markets returned HTTP {status}"
                )));
            }
            return response
                .json()
                .await
                .map_err(|_| invalid("data provider JSON invalid"));
        }
        Err(invalid("data retry budget exhausted"))
    }
}
#[async_trait]
impl DataApi for AlpacaData {
    async fn news_page(
        &self,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        token: Option<&str>,
    ) -> Result<Value> {
        if end < start {
            return Err(invalid("invalid news window"));
        }
        let mut query = vec![
            ("start".into(), stamp(start)),
            ("end".into(), stamp(end)),
            ("sort".into(), "desc".into()),
            ("limit".into(), "50".into()),
            ("include_content".into(), "false".into()),
        ];
        if let Some(token) = token {
            query.push(("page_token".into(), token.into()));
        }
        self.get("/v1beta1/news", &query).await
    }
    async fn bars(
        &self,
        symbol: &str,
        start: NaiveDate,
        end: DateTime<Utc>,
        asof: NaiveDate,
    ) -> Result<Vec<Value>> {
        if !valid_symbol(symbol) {
            return Err(invalid("invalid data symbol"));
        }
        let mut query = vec![
            ("timeframe".into(), "1Day".into()),
            ("start".into(), start.to_string()),
            ("end".into(), stamp(end)),
            ("asof".into(), asof.to_string()),
            ("feed".into(), self.credentials.feed.clone()),
            ("adjustment".into(), "split".into()),
            ("sort".into(), "asc".into()),
            ("limit".into(), "10000".into()),
        ];
        let mut rows = Vec::new();
        let mut seen = BTreeSet::new();
        for _ in 0..25 {
            let data = self
                .get(&format!("/v2/stocks/{symbol}/bars"), &query)
                .await?;
            if let Some(bars) = data["bars"].as_array() {
                rows.extend(bars.iter().cloned());
            }
            let Some(token) = data["next_page_token"].as_str().filter(|s| !s.is_empty()) else {
                return Ok(rows);
            };
            if !seen.insert(token.to_owned()) {
                return Err(invalid(
                    "repeated daily pagination token; truncated data refused",
                ));
            }
            query.retain(|(key, _)| key != "page_token");
            query.push(("page_token".into(), token.into()));
        }
        Err(invalid(
            "daily pagination ceiling reached; truncated data refused",
        ))
    }
    async fn latest_quote(&self, symbol: &str) -> Result<Value> {
        if !valid_symbol(symbol) {
            return Err(invalid("invalid quote symbol"));
        }
        let response = self
            .get(
                &format!("/v2/stocks/{symbol}/quotes/latest"),
                &[("feed".into(), "iex".into())],
            )
            .await?;
        response
            .get("quote")
            .cloned()
            .ok_or_else(|| invalid("execution quote missing"))
    }
}

pub struct Bundle {
    pub id: String,
    pub day: NumericModel,
    pub swing: NumericModel,
}
impl Bundle {
    pub fn from_json(bytes: &[u8]) -> Result<Self> {
        let mut root: Value = serde_json::from_slice(bytes)?;
        let id = root
            .as_object_mut()
            .ok_or_else(|| invalid("bundle object"))?
            .remove("bundle_id")
            .and_then(|v| v.as_str().map(str::to_owned))
            .ok_or_else(|| invalid("bundle identity missing"))?;
        if root["protocol"] != "mainkraft-paper-v1" || canonical::digest(&root)? != id {
            return Err(invalid("strategy bundle integrity check failed"));
        }
        let strategies = root["strategies"]
            .as_object()
            .ok_or_else(|| invalid("strategies missing"))?;
        if strategies.len() != 2
            || !strategies.contains_key("day")
            || !strategies.contains_key("swing")
        {
            return Err(invalid("both frozen strategies required"));
        }
        Ok(Self {
            id,
            day: serde_json::from_value(root["strategies"]["day"]["model"].clone())?,
            swing: serde_json::from_value(root["strategies"]["swing"]["model"].clone())?,
        })
    }
}
struct Cache {
    day: Option<NaiveDate>,
    daily: BTreeMap<String, Arc<Vec<Value>>>,
}
pub struct Market {
    pool: PgPool,
    data: Arc<dyn DataApi>,
    clock: Arc<dyn Clock>,
    calendar: Arc<Calendar>,
    bundle: Arc<Bundle>,
    feed: String,
    cache: RwLock<Cache>,
}
fn numeric(value: &Value) -> Result<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|v| v.parse().ok()))
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("invalid market numeric value"))
}
fn datetime(value: &Value) -> Result<DateTime<Utc>> {
    let parsed: DateTime<Utc> = value
        .as_str()
        .ok_or_else(|| invalid("market timestamp missing"))?
        .parse()
        .map_err(|_| invalid("market timestamp invalid"))?;
    DateTime::from_timestamp_micros(parsed.timestamp_micros())
        .ok_or_else(|| invalid("market timestamp range"))
}

pub fn normalize_bars(
    calendar: &Calendar,
    symbol: &str,
    raw: &[Value],
    feed: &str,
    asof: NaiveDate,
    acquired: DateTime<Utc>,
) -> Result<Vec<Value>> {
    if raw.is_empty() {
        return Err(invalid(format!("No daily bars returned for {symbol}")));
    }
    if !matches!(feed, "sip" | "iex") {
        return Err(invalid("unsupported feed"));
    }
    let mut indexed = BTreeMap::new();
    for b in raw {
        let date = datetime(&b["t"])?.with_timezone(&New_York).date_naive();
        let o = numeric(&b["o"])?;
        let h = numeric(&b["h"])?;
        let l = numeric(&b["l"])?;
        let c = numeric(&b["c"])?;
        let v = numeric(&b["v"])?;
        if !(0. < l && l <= o.min(c) && o.max(c) <= h && v >= 0.) {
            return Err(invalid("invalid OHLCV refused"));
        }
        if indexed.insert(date, (o, h, l, c, v)).is_some() {
            return Err(invalid("duplicate daily timestamps refused"));
        }
    }
    let mut rows = Vec::new();
    for (date, (o, h, l, c, v)) in indexed {
        let session = calendar
            .session(date)
            .ok_or_else(|| invalid("daily bar outside exchange calendar"))?;
        let available = session.close + Duration::minutes(15);
        if acquired < available {
            continue;
        }
        let mut row = json!({"symbol":symbol,"session_date":date.to_string(),"feed":feed,"adjustment":"split","adjustment_asof":asof.to_string(),
            "open":o,"high":h,"low":l,"close":c,"volume":v,"available_at":stamp(available),"acquired_at":Value::Null});
        let hash = canonical::digest(&row)?;
        row["acquired_at"] = json!(stamp(acquired));
        row["content_hash"] = json!(hash);
        rows.push(row);
    }
    Ok(rows)
}

impl Market {
    pub fn new(
        pool: PgPool,
        data: Arc<dyn DataApi>,
        clock: Arc<dyn Clock>,
        calendar: Arc<Calendar>,
        bundle: Arc<Bundle>,
        feed: String,
    ) -> Self {
        Self {
            pool,
            data,
            clock,
            calendar,
            bundle,
            feed,
            cache: RwLock::new(Cache {
                day: None,
                daily: BTreeMap::new(),
            }),
        }
    }
    pub async fn daily_inputs(
        &self,
        symbol: &str,
        instant: DateTime<Utc>,
    ) -> Result<Arc<Vec<Value>>> {
        let day = instant.with_timezone(&New_York).date_naive();
        {
            let mut cache = self.cache.write().await;
            if cache.day != Some(day) {
                cache.day = Some(day);
                cache.daily.clear();
            }
            if let Some(rows) = cache.daily.get(symbol) {
                return Ok(rows.clone());
            }
        }
        let raw = self
            .data
            .bars(
                symbol,
                (instant - Duration::days(60)).date_naive(),
                instant - Duration::minutes(15),
                instant.date_naive(),
            )
            .await?;
        let rows = Arc::new(normalize_bars(
            &self.calendar,
            symbol,
            &raw,
            &self.feed,
            instant.date_naive(),
            self.clock.now(),
        )?);
        let mut cache = self.cache.write().await;
        if cache.day == Some(day) {
            cache.daily.insert(symbol.into(), rows.clone());
        }
        Ok(rows)
    }
    fn features_rows(rows: &[Value]) -> Result<Vec<Bar>> {
        rows.iter()
            .map(|r| {
                Ok(Bar {
                    session_date: r["session_date"]
                        .as_str()
                        .ok_or_else(|| invalid("session date"))?
                        .parse()
                        .map_err(|_| invalid("session date"))?,
                    close: numeric(&r["close"])?,
                    volume: numeric(&r["volume"])?,
                })
            })
            .collect()
    }
    fn last_prior(rows: &[Value], date: NaiveDate) -> Result<&Value> {
        rows.iter()
            .rev()
            .find(|r| {
                r["session_date"]
                    .as_str()
                    .is_some_and(|d| d < date.to_string().as_str())
            })
            .ok_or_else(|| invalid("prior daily bar missing"))
    }
    async fn observation(
        &self,
        symbol: &str,
        broker: &dyn BrokerApi,
        instant: DateTime<Utc>,
        spy: &[Value],
        tech: &[Value],
        spyq: &Quote,
    ) -> Result<Observation> {
        let own = self.daily_inputs(symbol, instant).await?;
        let session = instant.with_timezone(&New_York).date_naive();
        let features = features::features(
            &self.calendar,
            session,
            &Self::features_rows(&own)?,
            &Self::features_rows(spy)?,
            &Self::features_rows(tech)?,
        )?;
        let signal = self
            .bundle
            .day
            .predict(&features.x.iter().copied().map(Some).collect::<Vec<_>>())?;
        let asset = broker.asset(symbol).await?;
        if asset["tradable"] != true || asset["status"] != "active" || asset["class"] != "us_equity"
        {
            return Err(invalid("Broker asset unavailable"));
        }
        let quote = self.quote(symbol).await?;
        let midpoint = quote.midpoint();
        let prior = Self::last_prior(&own, session)?;
        let market = Self::last_prior(spy, session)?;
        let mut x = vec![1.];
        x.extend(features.x);
        x.extend([
            midpoint / numeric(&prior["close"])? - 1.,
            spyq.midpoint() / numeric(&market["close"])? - 1.,
            quote.ask / quote.bid - 1.,
        ]);
        let source = json!({"symbol":symbol,"quote":{"bid":quote.bid,"ask":quote.ask,"source_time":stamp(quote.source_time),"acquired_at":stamp(quote.acquired_at),"feed":"iex"},
            "daily_last":prior,"market_last":market,"features":x,"model_bundle_id":self.bundle.id});
        let source_id = db::record(&self.pool, "shared", "source", &source).await?;
        Ok(Observation {
            symbol: symbol.into(),
            x,
            prior_return: (signal.expected_net_return + LEGACY_DAILY_COST)
                / LEGACY_TRADING_DAY_HOURS,
            uncertainty: ((signal.upper_return - signal.lower_return)
                / 2.56
                / LEGACY_TRADING_DAY_HOURS.sqrt())
            .max(0.0001),
            price: midpoint,
            midpoint,
            shortable: asset["shortable"] == true && asset["easy_to_borrow"] == true,
            asset,
            source_id,
            cost_allowance: quote.ask / quote.bid - 1. + 0.0010,
            quote_time: Some(quote.source_time),
        })
    }
}
#[async_trait]
impl MarketApi for Market {
    async fn universe(&self) -> Result<Vec<String>> {
        let mut names: BTreeSet<String> = ["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        let latest: Option<Value> = sqlx::query_scalar(
            "SELECT payload FROM pilot_research_runs ORDER BY completed_at DESC LIMIT 1",
        )
        .fetch_optional(&self.pool)
        .await?;
        if let Some(p) = latest {
            if let Some(candidates) = p["candidates"].as_object() {
                names.extend(candidates.keys().cloned());
            }
            if let Some(picks) = p["picks"].as_array() {
                for pick in picks {
                    if let Some(symbol) = pick["symbol"].as_str() {
                        names.insert(symbol.into());
                    }
                }
            }
        }
        Ok(names.into_iter().collect())
    }
    async fn observations(
        &self,
        symbols: &[String],
        broker: &dyn BrokerApi,
        instant: DateTime<Utc>,
    ) -> Result<Vec<Observation>> {
        let (spy, tech) = tokio::try_join!(
            self.daily_inputs("SPY", instant),
            self.daily_inputs("XLK", instant)
        )?;
        let spyq = self.quote("SPY").await?;
        let mut rows = Vec::new();
        for symbol in symbols {
            match self
                .observation(symbol, broker, instant, &spy, &tech, &spyq)
                .await
            {
                Ok(row) => rows.push(row),
                Err(error) => {
                    let reason = error.to_string().chars().take(160).collect::<String>();
                    db::record(
                        &self.pool,
                        "shared",
                        "data_failure",
                        &json!({"symbol":symbol,"at":stamp(instant),"reason":reason}),
                    )
                    .await?;
                }
            }
        }
        Ok(rows)
    }
    async fn quote(&self, symbol: &str) -> Result<Quote> {
        let raw = self.data.latest_quote(symbol).await?;
        let quote = Quote {
            bid: numeric(&raw["bp"])?,
            ask: numeric(&raw["ap"])?,
            source_time: datetime(&raw["t"])?,
            acquired_at: self.clock.now(),
            feed: "iex".into(),
        };
        quote.validate(self.clock.now())?;
        Ok(quote)
    }
}
