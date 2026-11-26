use crate::{Result, calendar::Calendar, features::Quote, invalid};
use chrono::{DateTime, Duration, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const VERSION: &str = "causal-intraday-60m-v1";
pub fn implementation_hash() -> &'static str {
    use sha2::{Digest, Sha256};
    static HASH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    HASH.get_or_init(|| hex::encode(Sha256::digest(include_bytes!("features.rs"))))
}
pub const NAMES: [&str; 27] = [
    "return_5m",
    "return_15m",
    "return_30m",
    "return_60m",
    "ema_12_gap",
    "ema_26_gap",
    "macd",
    "macd_signal",
    "rsi_14",
    "session_vwap_gap",
    "atr_14",
    "relative_volume_tod",
    "volatility_30m",
    "momentum_5m",
    "spread",
    "log_dollar_volume_5m",
    "spy_return_5m",
    "spy_return_30m",
    "xlk_return_5m",
    "xlk_return_30m",
    "return_5m_candles",
    "news_sentiment",
    "news_count",
    "news_missing",
    "minutes_from_open",
    "timeframe_5m_missing",
    "tod_volume_missing",
];
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Candle {
    pub symbol: String,
    pub start: DateTime<Utc>,
    pub minutes: u8,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    #[serde(default)]
    pub vwap: Option<f64>,
    pub available_at: DateTime<Utc>,
    pub acquired_at: DateTime<Utc>,
    pub feed: String,
    pub historical_availability_assumed: bool,
}
impl Candle {
    pub fn end(&self) -> DateTime<Utc> {
        self.start + Duration::minutes(self.minutes as i64)
    }
    pub fn validate(&self) -> Result<()> {
        if !matches!(self.minutes, 1 | 5)
            || !matches!(self.feed.as_str(), "sip" | "iex")
            || [self.open, self.high, self.low, self.close, self.volume]
                .iter()
                .any(|v| !v.is_finite())
            || self.low <= 0.
            || self.volume < 0.
            || self.high < self.open.max(self.close)
            || self.low > self.open.min(self.close)
            || self.high < self.low
            || self.available_at < self.end()
            || self.vwap.is_some_and(|v| !v.is_finite() || v <= 0.)
        {
            return Err(invalid("Invalid intraday candle"));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct News {
    #[serde(default)]
    pub source_id: String,
    #[serde(default)]
    pub model_revision: String,
    pub symbol: String,
    pub published_at: DateTime<Utc>,
    pub acquired_at: DateTime<Utc>,
    pub scored_at: DateTime<Utc>,
    pub sentiment: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FeatureRow {
    pub feed: String,
    pub symbol: String,
    pub at: DateTime<Utc>,
    pub x: Vec<f64>,
    pub hypothetical_history: bool,
    pub quote_available: bool,
}
fn visible<'a>(
    rows: &[&'a Candle],
    symbol: &str,
    minutes: u8,
    at: DateTime<Utc>,
    historical: bool,
) -> Result<Vec<&'a Candle>> {
    let mut found = BTreeMap::new();
    for row in rows.iter().copied().filter(|r| {
        r.symbol == symbol && r.minutes == minutes && r.end() <= at && r.available_at <= at
    }) {
        row.validate()?;
        if row.historical_availability_assumed && !historical {
            continue;
        }
        if !row.historical_availability_assumed && row.acquired_at > at {
            continue;
        }
        if found.insert(row.start, row).is_some() {
            return Err(invalid(
                "Duplicate intraday candle revision; select causal vintage explicitly",
            ));
        }
    }
    Ok(found.into_values().collect())
}
fn mean(x: &[f64]) -> f64 {
    x.iter().sum::<f64>() / x.len() as f64
}
fn ema(values: &[f64], period: f64) -> Vec<f64> {
    let mut prior = values[0];
    values
        .iter()
        .map(|v| {
            prior += 2. / (period + 1.) * (v - prior);
            prior
        })
        .collect()
}
pub fn build(
    calendar: &Calendar,
    rows: &[Candle],
    news: &[News],
    symbol: &str,
    at: DateTime<Utc>,
    quote: Option<&Quote>,
    historical: bool,
) -> Result<FeatureRow> {
    build_refs(
        calendar,
        &rows.iter().collect::<Vec<_>>(),
        news,
        symbol,
        at,
        quote,
        historical,
    )
}
pub fn build_refs(
    calendar: &Calendar,
    rows: &[&Candle],
    news: &[News],
    symbol: &str,
    at: DateTime<Utc>,
    quote: Option<&Quote>,
    historical: bool,
) -> Result<FeatureRow> {
    let day = at.with_timezone(&New_York).date_naive();
    let session = calendar
        .session(day)
        .ok_or_else(|| invalid("Not an exchange session"))?;
    if at < session.open || at > session.close {
        return Err(invalid("Outside regular session"));
    }
    let own = visible(rows, symbol, 1, at, historical)?;
    let own: Vec<_> = own
        .into_iter()
        .filter(|r| r.start >= session.open)
        .collect();
    if own.len() < 61 {
        return Err(invalid("61 completed contiguous intraday minutes required"));
    }
    let tail = &own[own.len() - 61..];
    if tail
        .windows(2)
        .any(|w| w[1].start - w[0].start != Duration::minutes(1))
        || at - tail.last().ok_or_else(|| invalid("Missing minute"))?.end() > Duration::seconds(75)
    {
        return Err(invalid("Missing or stale intraday minutes"));
    }
    if own.iter().any(|r| r.feed != tail[0].feed) {
        return Err(invalid("Mixed intraday feeds"));
    }
    let closes: Vec<_> = own.iter().map(|r| r.close).collect();
    let last = closes.len() - 1;
    let ret = |n: usize| closes[last] / closes[last - n] - 1.;
    let fast = ema(&closes, 12.);
    let slow = ema(&closes, 26.);
    let macd: Vec<_> = fast.iter().zip(&slow).map(|(f, s)| f - s).collect();
    let signal = ema(&macd, 9.);
    let changes: Vec<_> = closes[last - 14..=last]
        .windows(2)
        .map(|w| w[1] - w[0])
        .collect();
    let gain = changes.iter().map(|r| r.max(0.)).sum::<f64>();
    let loss = changes.iter().map(|r| (-r).max(0.)).sum::<f64>();
    let rsi = if gain + loss == 0. {
        0.5
    } else {
        gain / (gain + loss)
    };
    let volume: f64 = own.iter().map(|r| r.volume).sum();
    if volume <= 0. {
        return Err(invalid("Intraday volume unavailable"));
    }
    let vwap = own
        .iter()
        .map(|r| r.vwap.unwrap_or((r.high + r.low + r.close) / 3.) * r.volume)
        .sum::<f64>()
        / volume;
    let atr = own[own.len() - 15..]
        .windows(2)
        .map(|w| {
            (w[1].high - w[1].low)
                .max((w[1].high - w[0].close).abs())
                .max((w[1].low - w[0].close).abs())
        })
        .sum::<f64>()
        / 14.
        / closes[last];
    let logs: Vec<_> = closes[last - 30..=last]
        .windows(2)
        .map(|w| (w[1] / w[0]).ln())
        .collect();
    let average = mean(&logs);
    let volatility = (logs.iter().map(|v| (v - average).powi(2)).sum::<f64>() / 30.).sqrt();
    let slot = own[last].start.with_timezone(&New_York).time();
    let prior = visible(rows, symbol, 1, at, historical)?;
    let matching: Vec<_> = prior
        .iter()
        .filter(|r| {
            r.start.with_timezone(&New_York).date_naive() < day
                && r.start.with_timezone(&New_York).time() == slot
        })
        .map(|r| r.volume)
        .collect();
    let tod_missing = matching.len() < 5;
    let relative = if tod_missing {
        1.
    } else {
        own[last].volume / mean(&matching).max(1.)
    };
    let context = |name: &str, n: usize| -> Result<f64> {
        let data = visible(rows, name, 1, at, historical)?;
        let data: Vec<_> = data
            .into_iter()
            .filter(|r| r.start >= session.open && r.feed == tail[0].feed)
            .collect();
        if data.len() < n + 1 {
            return Err(invalid("Intraday SPY/XLK context unavailable"));
        }
        let end = data.len() - 1;
        if data[end].start != own[last].start
            || data[end].start - data[end - n].start != Duration::minutes(n as i64)
        {
            return Err(invalid("Intraday context timestamps are not aligned"));
        }
        Ok(data[end].close / data[end - n].close - 1.)
    };
    let five = visible(rows, symbol, 5, at, historical)?;
    let five = five
        .last()
        .filter(|r| r.start >= session.open && at - r.end() <= Duration::minutes(5));
    let (spread, liquidity) = match quote {
        Some(q) => {
            q.validate(at)?;
            if q.acquired_at > at {
                return Err(invalid("Future-acquired quote"));
            }
            (
                q.ask / q.bid - 1.,
                own[last - 4..=last]
                    .iter()
                    .map(|r| r.close * r.volume)
                    .sum::<f64>()
                    .ln_1p(),
            )
        }
        None if historical => (
            0.,
            own[last - 4..=last]
                .iter()
                .map(|r| r.close * r.volume)
                .sum::<f64>()
                .ln_1p(),
        ),
        None => {
            return Err(invalid(
                "Executable quote unavailable; no delayed substitution",
            ));
        }
    };
    // Missing historical quotes are explicitly excluded from quote feature groups by the evaluator.
    let valid_news: Vec<_> = news
        .iter()
        .filter(|n| {
            n.symbol == symbol
                && !n.source_id.is_empty()
                && n.model_revision == crate::sentiment::REVISION
                && n.published_at <= at
                && n.acquired_at <= at
                && n.scored_at <= at
                && n.scored_at >= n.acquired_at
                && at - n.published_at <= Duration::hours(24)
                && n.sentiment.is_finite()
                && n.sentiment.abs() <= 1.
        })
        .collect();
    let sentiment = if valid_news.is_empty() {
        0.
    } else {
        valid_news.iter().map(|n| n.sentiment).sum::<f64>() / valid_news.len() as f64
    };
    let x = vec![
        ret(5),
        ret(15),
        ret(30),
        ret(60),
        closes[last] / fast[last] - 1.,
        closes[last] / slow[last] - 1.,
        macd[last] / closes[last],
        signal[last] / closes[last],
        rsi,
        closes[last] / vwap - 1.,
        atr,
        relative,
        volatility,
        ret(5) - ret(30) / 6.,
        spread,
        liquidity,
        context("SPY", 5)?,
        context("SPY", 30)?,
        context("XLK", 5)?,
        context("XLK", 30)?,
        five.map_or(0., |r| r.close / r.open - 1.),
        sentiment,
        valid_news.len() as f64,
        if valid_news.is_empty() { 1. } else { 0. },
        (at - session.open).num_minutes() as f64,
        if five.is_none() { 1. } else { 0. },
        if tod_missing { 1. } else { 0. },
    ];
    if x.iter().any(|v| !v.is_finite()) {
        return Err(invalid("Nonfinite intraday features"));
    }
    Ok(FeatureRow {
        feed: tail[0].feed.clone(),
        symbol: symbol.into(),
        at,
        x,
        hypothetical_history: historical,
        quote_available: quote.is_some(),
    })
}
