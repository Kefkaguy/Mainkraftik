pub use crate::intraday::features::{Candle as IntradayCandle, FeatureRow as IntradayFeatures};
use crate::{Result, calendar::Calendar, invalid};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Bar {
    pub session_date: NaiveDate,
    pub close: f64,
    pub volume: f64,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Features {
    pub x: Vec<f64>,
    pub volatility: f64,
}

fn series(rows: &[Bar], days: &[NaiveDate], volume: bool) -> Result<Vec<f64>> {
    let indexed: BTreeMap<_, _> = rows
        .iter()
        .map(|r| (r.session_date, if volume { r.volume } else { r.close }))
        .collect();
    days.iter()
        .map(|d| {
            indexed
                .get(d)
                .copied()
                .filter(|v| v.is_finite())
                .ok_or_else(|| invalid("missing prior SIP session"))
        })
        .collect()
}
fn average(x: &[f64]) -> f64 {
    x.iter().sum::<f64>() / x.len() as f64
}
fn std(x: &[f64]) -> f64 {
    let mean = average(x);
    (x.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
}
pub fn features(
    calendar: &Calendar,
    session: NaiveDate,
    own: &[Bar],
    spy: &[Bar],
    tech: &[Bar],
) -> Result<Features> {
    let days = calendar.prior_sessions(session, 45, 21);
    if days.len() != 21 {
        return Err(invalid("incomplete feature calendar"));
    }
    let price = series(own, &days, false)?;
    let market = series(spy, &days, false)?;
    let technology = series(tech, &days, false)?;
    let volume = series(own, &days, true)?;
    if price
        .iter()
        .chain(&market)
        .chain(&technology)
        .any(|v| *v <= 0.0)
        || average(&volume[..20]) <= 0.0
    {
        return Err(invalid("invalid prices or volume"));
    }
    let returns = |p: &[f64]| -> [f64; 4] { [1, 5, 10, 20].map(|n| p[20] / p[20 - n] - 1.0) };
    let own_returns = returns(&price);
    let market_returns = returns(&market);
    let tech_returns = returns(&technology);
    let changes: Vec<_> = price.windows(2).map(|r| r[1] / r[0] - 1.0).collect();
    let mut x = own_returns.to_vec();
    x.extend([
        std(&changes[10..]),
        std(&changes),
        volume[20] / average(&volume[..20]),
        market_returns[1],
        market_returns[3],
        tech_returns[1] - market_returns[1],
        own_returns[1] - market_returns[1],
    ]);
    if x.iter().any(|v| !v.is_finite()) {
        return Err(invalid("non-finite features"));
    }
    Ok(Features {
        volatility: x[5] * 252f64.sqrt(),
        x,
    })
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Quote {
    pub bid: f64,
    pub ask: f64,
    pub source_time: DateTime<Utc>,
    pub acquired_at: DateTime<Utc>,
    pub feed: String,
}
impl Quote {
    pub fn validate(&self, instant: DateTime<Utc>) -> Result<()> {
        if !self.bid.is_finite()
            || !self.ask.is_finite()
            || self.bid <= 0.0
            || self.ask < self.bid
            || self.source_time > instant
            || instant - self.source_time > Duration::seconds(15)
        {
            return Err(invalid("fresh valid execution quote unavailable"));
        }
        Ok(())
    }
    pub fn midpoint(&self) -> f64 {
        (self.bid + self.ask) / 2.0
    }
}
