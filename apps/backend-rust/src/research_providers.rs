//! Original research data acquisition contracts, separate from paper-engine marks.
//! Read-only pinned hosts; no brokerage order paths or implicit feed substitutions.
use crate::{Result, calendar::Calendar, canonical, engine::stamp, financials, invalid};
use async_trait::async_trait;
use bigdecimal::{BigDecimal, ToPrimitive};
use chrono::{
    DateTime, Duration, FixedOffset, NaiveDate, NaiveDateTime, Offset, SecondsFormat, TimeZone, Utc,
};
use chrono_tz::America::New_York;
use futures::StreamExt;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::Duration as Wait,
};
use tokio::sync::Semaphore;

pub const MAX_RESPONSE_BYTES: usize = 64 * 1024 * 1024;
pub struct Request {
    pub url: String,
    pub query: Value,
    // Intentionally no Debug: credentials must never be formatted into logs.
    pub headers: BTreeMap<String, String>,
    pub timeout_seconds: u64,
}
pub struct Response {
    pub status: u16,
    pub payload: Value,
}
#[async_trait]
pub trait Transport: Send + Sync {
    async fn send(&self, request: &Request) -> Result<Response>;
}
#[async_trait]
pub trait Delay: Send + Sync {
    async fn wait(&self, milliseconds: u64);
}
pub struct TokioDelay;
#[async_trait]
impl Delay for TokioDelay {
    async fn wait(&self, milliseconds: u64) {
        tokio::time::sleep(Wait::from_millis(milliseconds)).await;
    }
}
pub struct NativeTransport(reqwest::Client);
impl NativeTransport {
    pub fn new() -> Result<Self> {
        Ok(Self(
            reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|_| invalid("Research provider HTTP client unavailable"))?,
        ))
    }
}
fn allowed(url: &str) -> Result<url::Url> {
    let u = url::Url::parse(url).map_err(|_| invalid("Data provider host is not permitted"))?;
    if u.scheme() != "https"
        || !matches!(
            u.host_str(),
            Some("data.alpaca.markets" | "data.sec.gov" | "www.sec.gov")
        )
        || u.port().is_some()
        || !u.username().is_empty()
        || u.password().is_some()
    {
        return Err(invalid("Data provider host is not permitted"));
    }
    Ok(u)
}
#[async_trait]
impl Transport for NativeTransport {
    async fn send(&self, request: &Request) -> Result<Response> {
        let url = allowed(&request.url)?;
        let mut headers = reqwest::header::HeaderMap::new();
        for (key, value) in &request.headers {
            let name = reqwest::header::HeaderName::from_bytes(key.as_bytes())
                .map_err(|_| invalid("Invalid provider header"))?;
            let mut value = reqwest::header::HeaderValue::from_str(value)
                .map_err(|_| invalid("Invalid provider header"))?;
            value.set_sensitive(true);
            headers.insert(name, value);
        }
        let response = self
            .0
            .get(url)
            .headers(headers)
            .query(&request.query)
            .timeout(Wait::from_secs(request.timeout_seconds))
            .send()
            .await
            .map_err(|e| {
                let kind = if e.is_timeout() {
                    "ReadTimeout"
                } else if e.is_connect() {
                    "ConnectError"
                } else {
                    "HTTPError"
                };
                invalid(format!(
                    "Provider connection failed ({kind}); no data substituted"
                ))
            })?;
        let status = response.status().as_u16();
        // Error bodies may contain upstream secrets and are neither read nor logged.
        if status != 200 {
            return Ok(Response {
                status,
                payload: Value::Null,
            });
        }
        if response
            .content_length()
            .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
        {
            return Err(invalid(
                "Research provider response exceeds 64 MiB; truncated data refused",
            ));
        }
        let mut stream = response.bytes_stream();
        let mut bytes = Vec::new();
        while let Some(part) = stream.next().await {
            let part = part.map_err(|_| {
                invalid("Provider connection failed (ReadError); no data substituted")
            })?;
            if bytes.len().saturating_add(part.len()) > MAX_RESPONSE_BYTES {
                return Err(invalid(
                    "Research provider response exceeds 64 MiB; truncated data refused",
                ));
            }
            bytes.extend_from_slice(&part);
        }
        Ok(Response {
            status,
            payload: serde_json::from_slice(&bytes)?,
        })
    }
}
pub struct Credentials {
    pub key: String,
    pub secret: String,
    pub feed: String,
    pub sec_user_agent: String,
}
pub struct Providers {
    credentials: Credentials,
    transport: Arc<dyn Transport>,
    delay: Arc<dyn Delay>,
    requests: Semaphore,
    sec_batches: Semaphore,
}
impl Providers {
    pub async fn research_news_page(
        &self,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        token: Option<&str>,
    ) -> Result<Value> {
        let mut query = research_news_query(start, end);
        if let Some(token) = token {
            query["page_token"] = json!(token);
        }
        self.get(&Request {
            url: "https://data.alpaca.markets/v1beta1/news".into(),
            query,
            headers: self.data_headers(),
            timeout_seconds: 40,
        })
        .await
    }
    pub fn feed(&self) -> &str {
        &self.credentials.feed
    }
    pub fn new(
        credentials: Credentials,
        transport: Arc<dyn Transport>,
        delay: Arc<dyn Delay>,
    ) -> Result<Self> {
        if !matches!(credentials.feed.as_str(), "sip" | "iex") {
            return Err(invalid("Unsupported feed"));
        }
        Ok(Self {
            credentials,
            transport,
            delay,
            requests: Semaphore::new(4),
            sec_batches: Semaphore::new(1),
        })
    }
    async fn get(&self, request: &Request) -> Result<Value> {
        let url = allowed(&request.url)?;
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|_| invalid("Research provider concurrency unavailable"))?;
        for attempt in 0..4 {
            let response = self.transport.send(request).await?;
            if matches!(response.status, 429 | 500 | 502 | 503 | 504) && attempt < 3 {
                self.delay.wait((1u64 << attempt).min(4) * 1000).await;
                continue;
            }
            if response.status != 200 {
                return Err(invalid(format!(
                    "{} returned HTTP {}; check access or retry later",
                    url.host_str().unwrap_or_default(),
                    response.status
                )));
            }
            return Ok(response.payload);
        }
        Err(invalid("Provider retry budget exhausted"))
    }
    fn data_headers(&self) -> BTreeMap<String, String> {
        BTreeMap::from([
            ("APCA-API-KEY-ID".into(), self.credentials.key.clone()),
            (
                "APCA-API-SECRET-KEY".into(),
                self.credentials.secret.clone(),
            ),
        ])
    }
    pub async fn fetch_bars(
        &self,
        symbol: &str,
        start: NaiveDate,
        end: DateTime<Utc>,
        asof: NaiveDate,
    ) -> Result<(Vec<Value>, Value, String)> {
        if !crate::broker::valid_symbol(symbol) {
            return Err(invalid("Invalid symbol"));
        }
        if self.credentials.key.is_empty() || self.credentials.secret.is_empty() {
            return Err(invalid(
                "Configure INTELLIGENCE_ALPACA_KEY and INTELLIGENCE_ALPACA_SECRET",
            ));
        }
        let url = format!("https://data.alpaca.markets/v2/stocks/{symbol}/bars");
        let mut request = Request {
            url: url.clone(),
            query: json!({"timeframe":"1Day","start":start.to_string(),"end":stamp(end),"asof":asof.to_string(),"feed":self.credentials.feed,"adjustment":"split","sort":"asc","limit":10000}),
            headers: self.data_headers(),
            timeout_seconds: 45,
        };
        let mut pages = Vec::new();
        let mut bars = Vec::new();
        let mut seen = BTreeSet::new();
        for _ in 0..25 {
            let payload = self.get(&request).await?;
            if let Some(rows) = payload["bars"].as_array() {
                bars.extend(rows.iter().cloned());
            }
            let token = payload["next_page_token"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_owned);
            pages.push(payload);
            let Some(token) = token else {
                request
                    .query
                    .as_object_mut()
                    .expect("query object")
                    .remove("page_token");
                return Ok((bars, json!({"request":request.query,"pages":pages}), url));
            };
            if !seen.insert(token.clone()) {
                return Err(invalid("Repeated pagination token; truncated data refused"));
            }
            request.query["page_token"] = json!(token);
        }
        Err(invalid(
            "Pagination ceiling exceeded; truncated data refused",
        ))
    }
    pub async fn fetch_actions(
        &self,
        symbol: &str,
        start: NaiveDate,
        end: NaiveDate,
    ) -> Result<(Value, String)> {
        if !crate::broker::valid_symbol(symbol) {
            return Err(invalid("Invalid symbol"));
        }
        let url = "https://data.alpaca.markets/v1/corporate-actions".to_owned();
        let mut request = Request {
            url: url.clone(),
            query: json!({"symbols":symbol,"start":start.to_string(),"end":end.to_string(),"limit":1000,"types":"forward_split,reverse_split,cash_dividend,stock_dividend,spin_off,name_change"}),
            headers: self.data_headers(),
            timeout_seconds: 45,
        };
        let mut pages = Vec::new();
        let mut seen = BTreeSet::new();
        for _ in 0..20 {
            let payload = self.get(&request).await?;
            let token = payload["next_page_token"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_owned);
            pages.push(payload);
            let Some(token) = token else {
                return Ok((json!({"pages":pages,"request":request.query}), url));
            };
            if !seen.insert(token.clone()) {
                return Err(invalid("Repeated corporate-action pagination token"));
            }
            request.query["page_token"] = json!(token);
        }
        Err(invalid("Corporate-action pagination ceiling exceeded"))
    }
    pub async fn fetch_sec(&self, cik: &str) -> Result<(Value, Value, String, String)> {
        if !self.credentials.sec_user_agent.contains('@') {
            return Err(invalid(
                "Configure a contact email in INTELLIGENCE_SEC_USER_AGENT",
            ));
        }
        if cik.is_empty() || !cik.bytes().all(|b| b.is_ascii_digit()) {
            return Err(invalid("Invalid SEC CIK"));
        }
        let _permit = self
            .sec_batches
            .acquire()
            .await
            .map_err(|_| invalid("SEC concurrency unavailable"))?;
        let facts_url = format!("https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json");
        let submissions_url = format!("https://data.sec.gov/submissions/CIK{cik}.json");
        let mut request = Request {
            url: submissions_url.clone(),
            query: json!({}),
            headers: BTreeMap::from([
                ("User-Agent".into(), self.credentials.sec_user_agent.clone()),
                ("Accept-Encoding".into(), "gzip, deflate".into()),
            ]),
            timeout_seconds: 60,
        };
        let submissions = self.get(&request).await?;
        self.delay.wait(550).await;
        request.url = facts_url.clone();
        let facts = self.get(&request).await?;
        Ok((facts, submissions, facts_url, submissions_url))
    }
    pub async fn lookup_company(&self, symbol: &str) -> Result<Value> {
        if !crate::broker::valid_symbol(symbol) {
            return Err(invalid("Invalid symbol"));
        }
        if !self.credentials.sec_user_agent.contains('@') {
            return Err(invalid("Configure the SEC contact User-Agent"));
        }
        let mapping = self
            .get(&Request {
                url: "https://www.sec.gov/files/company_tickers.json".into(),
                query: json!({}),
                headers: BTreeMap::from([(
                    "User-Agent".into(),
                    self.credentials.sec_user_agent.clone(),
                )]),
                timeout_seconds: 30,
            })
            .await?;
        let matches: Vec<_> = mapping
            .as_object()
            .ok_or_else(|| invalid("Invalid SEC company mapping"))?
            .values()
            .filter(|r| {
                r["ticker"]
                    .as_str()
                    .is_some_and(|t| t.to_uppercase() == symbol)
            })
            .collect();
        if matches.len() != 1 {
            return Err(invalid("No unique SEC company identity was found"));
        }
        let cik = match &matches[0]["cik_str"] {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            _ => return Err(invalid("Invalid SEC CIK")),
        };
        Ok(json!({"symbol":symbol,"name":matches[0]["title"],"cik":format!("{cik:0>10}")}))
    }
}
pub fn research_news_query(start: DateTime<Utc>, end: DateTime<Utc>) -> Value {
    json!({"symbols":"MSI,AXON","start":stamp(start),"end":stamp(end),"sort":"asc","limit":50,"include_content":"false","exclude_contentless":"false"})
}

// Keep exact decimal values for NUMERIC storage, even though Python's canonical
// identity converts Decimal to f64. Do not reuse the paper-engine float validator.
pub struct NormalizedBar {
    pub record: Value,
    pub amounts: [BigDecimal; 5],
}
pub fn normalize_bars(
    calendar: &Calendar,
    symbol: &str,
    raw: &[Value],
    feed: &str,
    asof: NaiveDate,
    acquired: DateTime<Utc>,
) -> Result<Vec<NormalizedBar>> {
    if !matches!(feed, "sip" | "iex") {
        return Err(invalid("Unsupported feed"));
    }
    let mut parsed = BTreeMap::new();
    for row in raw {
        let time = DateTime::parse_from_rfc3339(
            row["t"]
                .as_str()
                .ok_or_else(|| invalid("Provider timestamp missing"))?,
        )
        .map_err(|_| invalid("Provider timestamps must include a timezone"))?;
        let day = time.with_timezone(&New_York).date_naive();
        let values: Vec<BigDecimal> = ["o", "h", "l", "c", "v"]
            .iter()
            .map(|k| {
                let text = match &row[k] {
                    Value::String(s) => s.clone(),
                    v => v.to_string(),
                };
                text.parse()
                    .map_err(|_| invalid("Non-finite OHLCV refused"))
            })
            .collect::<Result<_>>()?;
        let amounts: [BigDecimal; 5] = values
            .try_into()
            .map_err(|_| invalid("Invalid OHLCV refused"))?;
        let [o, h, l, c, v] = &amounts;
        if !(l > &BigDecimal::from(0)
            && l <= o.min(c)
            && o.max(c) <= h
            && v >= &BigDecimal::from(0))
        {
            return Err(invalid("Invalid OHLCV refused"));
        }
        if parsed.insert(day, (amounts, row)).is_some() {
            return Err(invalid("Duplicate daily timestamps refused"));
        }
    }
    if parsed.is_empty() {
        return Err(invalid(format!("No daily bars returned for {symbol}")));
    }
    let mut out = Vec::new();
    for (day, (amounts, raw)) in parsed {
        let available = calendar
            .session(day)
            .ok_or_else(|| invalid("Provider returned a bar outside the exchange calendar"))?
            .close
            + Duration::minutes(15);
        if acquired < available {
            continue;
        }
        let mut record = json!({"symbol":symbol,"session_date":day.to_string(),"feed":feed,"adjustment":"split","adjustment_asof":asof.to_string(),"available_at":stamp(available),"acquired_at":Value::Null});
        for (i, (key, short)) in ["open", "high", "low", "close", "volume"]
            .iter()
            .zip(["o", "h", "l", "c", "v"])
            .enumerate()
        {
            let mut number = amounts[i]
                .to_f64()
                .filter(|v| v.is_finite())
                .ok_or_else(|| invalid("Non-finite canonical number"))?;
            if number == 0.0
                && (raw[short].as_str().is_some_and(|s| s.starts_with('-'))
                    || raw[short].as_f64().is_some_and(f64::is_sign_negative))
            {
                number = -0.0;
            }
            record[key] = json!(number);
        }
        record["content_hash"] = json!(canonical::digest(&record)?);
        record["acquired_at"] = json!(stamp(acquired));
        out.push(NormalizedBar { record, amounts });
    }
    Ok(out)
}

fn python_stamp(time: DateTime<FixedOffset>) -> String {
    time.to_rfc3339_opts(
        if time.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    )
}
fn eastern_naive(time: NaiveDateTime) -> Result<DateTime<Utc>> {
    // zoneinfo uses fold=0: first offset at autumn overlaps, pre-gap offset in spring.
    if let Some(value) = New_York.from_local_datetime(&time).earliest() {
        return Ok(value.with_timezone(&Utc));
    }
    let previous = New_York
        .from_local_datetime(&(time - Duration::days(1)))
        .earliest()
        .ok_or_else(|| invalid("Invalid filing availability"))?;
    Ok(time
        .and_local_timezone(previous.offset().fix())
        .single()
        .ok_or_else(|| invalid("Invalid filing availability"))?
        .with_timezone(&Utc))
}
pub fn filing_availability(filed: &str, accession: &str, submissions: &Value) -> Result<String> {
    if let Some(accessions) = submissions["filings"]["recent"]["accessionNumber"].as_array() {
        for (i, a) in accessions.iter().enumerate() {
            if a == accession
                && let Some(text) = submissions["filings"]["recent"]["acceptanceDateTime"]
                    .get(i)
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
            {
                if let Ok(time) = DateTime::parse_from_rfc3339(text) {
                    return Ok(python_stamp(time));
                }
                let time = NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f")
                    .map_err(|_| invalid("Invalid filing availability"))?;
                return Ok(stamp(eastern_naive(time)?));
            }
        }
    }
    let day = filed
        .parse::<NaiveDate>()
        .map_err(|_| invalid("Invalid filed date"))?
        + Duration::days(1);
    Ok(stamp(eastern_naive(
        day.and_hms_opt(0, 0, 0)
            .ok_or_else(|| invalid("Invalid filed date"))?,
    )?))
}
pub fn normalize_facts(
    symbol: &str,
    facts: &Value,
    submissions: &Value,
    acquired: DateTime<Utc>,
) -> Result<Vec<Value>> {
    let metadata: Value = serde_json::from_str(include_str!("../assets/financial-tags.json"))?;
    let cik = match facts.get("cik") {
        Some(Value::String(s)) => s.clone(),
        Some(n) => n.to_string(),
        None => String::new(),
    };
    let cik = cik.trim_start_matches('0');
    let mut out = Vec::new();
    // Preserve source iteration order as well as tag priority.
    for metric in [
        "revenue",
        "net_income",
        "eps",
        "operating_income",
        "operating_cash_flow",
        "capex",
        "cash",
        "current_debt",
        "long_term_debt",
        "diluted_shares",
    ] {
        for tag in metadata["tags"][metric]
            .as_array()
            .ok_or_else(|| invalid("Missing GAAP tags"))?
        {
            let tag = tag.as_str().ok_or_else(|| invalid("Invalid GAAP tag"))?;
            let unit = if metric == "eps" {
                "USD/shares"
            } else if metric == "diluted_shares" {
                "shares"
            } else {
                "USD"
            };
            if let Some(rows) = facts["facts"]["us-gaap"][tag]["units"][unit].as_array() {
                for row in rows {
                    if !matches!(
                        row["form"].as_str(),
                        Some("10-K" | "10-Q" | "10-K/A" | "10-Q/A")
                    ) || ["end", "filed", "accn", "val"]
                        .iter()
                        .any(|k| row.get(k).is_none())
                    {
                        continue;
                    }
                    let start = row
                        .get("start")
                        .filter(|v| !v.is_null() && *v != "")
                        .map(financials::date)
                        .transpose()?;
                    let end = financials::date(&row["end"])?;
                    if start.is_some_and(|s| s > end) {
                        continue;
                    }
                    let filed = financials::date(&row["filed"])?;
                    let accession = row["accn"]
                        .as_str()
                        .ok_or_else(|| invalid("Invalid accession"))?;
                    let mut item = json!({"symbol":symbol,"metric":metric,"tag":tag,"unit":unit,"value":row["val"],"period_start":start.map(|d|d.to_string()),"period_end":end.to_string(),"fiscal_year":row["fy"],"fiscal_period":row["fp"],"form":row["form"],"accession":accession,"filed_date":filed.to_string(),"available_at":filing_availability(&filed.to_string(),accession,submissions)?,"source_url":format!("https://www.sec.gov/Archives/edgar/data/{cik}/{}/",accession.replace('-',""))});
                    item["content_hash"] = json!(canonical::digest(&item)?);
                    item["acquired_at"] = json!(stamp(acquired));
                    out.push(item);
                }
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn research_transport_rejects_non_provider_targets() {
        for url in [
            "http://data.sec.gov/api",
            "https://data.sec.gov.evil.invalid/api",
            "https://data.sec.gov@evil.invalid/api",
            "https://user@data.sec.gov/api",
            "https://data.sec.gov:444/api",
            "https://paper-api.alpaca.markets/v2/orders",
            "https://api.alpaca.markets/v2/orders",
        ] {
            assert!(allowed(url).is_err(), "{url}");
        }
        for url in [
            "https://data.sec.gov/api",
            "https://www.sec.gov/files/company_tickers.json",
            "https://data.alpaca.markets/v2/stocks/MSI/bars",
        ] {
            assert!(allowed(url).is_ok(), "{url}");
        }
    }
}
