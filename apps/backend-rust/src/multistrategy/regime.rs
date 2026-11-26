use crate::{Result, calendar::Calendar, intraday::features::Candle, invalid};
use chrono::{DateTime, Duration, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Regime {
    Bullish,
    Bearish,
    Sideways,
    HighVolatility,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Frame {
    pub minutes: i64,
    pub close: f64,
    pub previous_close: f64,
    pub ema_fast: f64,
    pub ema_slow: f64,
    pub rsi: f64,
    pub macd: f64,
    pub macd_signal: f64,
    pub atr: f64,
    pub range_high: f64,
    pub range_low: f64,
    pub previous_high: f64,
    pub previous_low: f64,
    pub higher_structure: bool,
    pub lower_structure: bool,
    pub relative_volume: Option<f64>,
    pub momentum: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Context {
    pub symbol: String,
    pub at: DateTime<Utc>,
    pub feed: String,
    pub regime: Regime,
    pub directional_regime: Regime,
    pub frames: Vec<Frame>,
    pub session_vwap: f64,
    pub reconstructed_history: bool,
}
#[derive(Clone)]
struct Bar {
    start: DateTime<Utc>,
    close: f64,
    high: f64,
    low: f64,
    volume: f64,
}
fn ema(x: &[f64], period: usize) -> Vec<f64> {
    let mut prior = x[0];
    x.iter()
        .map(|v| {
            prior += 2. / (period as f64 + 1.) * (v - prior);
            prior
        })
        .collect()
}
fn frame(bars: &[Bar], minutes: i64) -> Result<Frame> {
    if bars.len() < 35 {
        return Err(invalid("Insufficient complete multi-timeframe history"));
    }
    let n = bars.len();
    let b = &bars[n - 1];
    let p = &bars[n - 2];
    let prices: Vec<_> = bars.iter().map(|b| b.close).collect();
    let fast = ema(&prices, 8);
    let slow = ema(&prices, 20);
    let a = ema(&prices, 12);
    let z = ema(&prices, 26);
    let macd: Vec<_> = a.iter().zip(z).map(|(a, b)| a - b).collect();
    let sig = ema(&macd, 9);
    let mut gain = 0.;
    let mut loss = 0.;
    let mut atr = 0.;
    for i in n - 14..n {
        let delta = bars[i].close - bars[i - 1].close;
        gain += delta.max(0.);
        loss += (-delta).max(0.);
        atr += (bars[i].high - bars[i].low)
            .max((bars[i].high - bars[i - 1].close).abs())
            .max((bars[i].low - bars[i - 1].close).abs());
    }
    let rsi = if gain + loss == 0. {
        50.
    } else {
        100. * gain / (gain + loss)
    };
    let prior = &bars[n - 21..n - 1];
    let minute = b.start.with_timezone(&New_York).format("%H:%M").to_string();
    let vols: Vec<_> = bars[..n - 1]
        .iter()
        .rev()
        .filter(|v| v.start.with_timezone(&New_York).format("%H:%M").to_string() == minute)
        .take(20)
        .map(|v| v.volume)
        .collect();
    let mean = vols.iter().sum::<f64>() / (vols.len().max(1) as f64);
    Ok(Frame {
        minutes,
        close: b.close,
        previous_close: p.close,
        ema_fast: fast[n - 1],
        ema_slow: slow[n - 1],
        rsi,
        macd: macd[n - 1],
        macd_signal: sig[n - 1],
        atr: atr / 14.,
        range_high: prior
            .iter()
            .map(|b| b.high)
            .fold(f64::NEG_INFINITY, f64::max),
        range_low: prior.iter().map(|b| b.low).fold(f64::INFINITY, f64::min),
        previous_high: p.high,
        previous_low: p.low,
        higher_structure: b.high > p.high && b.low > p.low,
        lower_structure: b.high < p.high && b.low < p.low,
        relative_volume: if vols.len() >= 5 && mean > 0. {
            Some(b.volume / mean)
        } else {
            None
        },
        momentum: b.close / bars[n - 6].close - 1.,
    })
}
pub fn detect(
    calendar: &Calendar,
    symbol: &str,
    at: DateTime<Utc>,
    candles: &[Candle],
    historical: bool,
) -> Result<Context> {
    let day = at.with_timezone(&New_York).date_naive();
    let session = calendar
        .session(day)
        .ok_or_else(|| invalid("No regular session"))?;
    if at < session.open || at >= session.close {
        return Err(invalid("Outside regular session"));
    }
    let mut visible = BTreeMap::new();
    let mut feed = None;
    let mut assumed = false;
    for c in candles.iter().filter(|c| {
        c.symbol == symbol
            && c.minutes == 1
            && c.end() <= at
            && c.available_at <= at
            && (c.acquired_at <= at || (historical && c.historical_availability_assumed))
    }) {
        c.validate()?;
        if feed.as_ref().is_some_and(|f| f != &c.feed) {
            return Err(invalid("Mixed regime feeds"));
        }
        feed = Some(c.feed.clone());
        assumed |= c.historical_availability_assumed;
        let Some(s) = calendar.session(c.start.with_timezone(&New_York).date_naive()) else {
            continue;
        };
        if c.start >= s.open && c.end() <= s.close && visible.insert(c.start, c).is_some() {
            return Err(invalid("Duplicate regime candle"));
        }
    }
    let last = visible
        .values()
        .next_back()
        .ok_or_else(|| invalid("Missing regime candles"))?;
    if at - last.end() > Duration::seconds(75) {
        return Err(invalid("Stale regime candles"));
    }
    // Current one-minute structure must be continuous. Higher frames are never
    // built by filling missing constituent candles or using forming bars.
    for n in 0..35 {
        if !visible.contains_key(&(last.start - Duration::minutes(n))) {
            return Err(invalid("Incomplete current regime history"));
        }
    }
    let mut frames = Vec::new();
    for minutes in [1, 5, 15, 60] {
        let mut buckets = BTreeMap::<DateTime<Utc>, Vec<&Candle>>::new();
        for c in visible.values() {
            let Some(s) = calendar.session(c.start.with_timezone(&New_York).date_naive()) else {
                continue;
            };
            let start =
                s.open + Duration::minutes((c.start - s.open).num_minutes() / minutes * minutes);
            if start + Duration::minutes(minutes) <= s.close
                && start + Duration::minutes(minutes) <= at
            {
                buckets.entry(start).or_default().push(c);
            }
        }
        let bars: Vec<_> = buckets
            .into_iter()
            .filter_map(|(start, v)| {
                if v.len() != minutes as usize
                    || v.iter()
                        .enumerate()
                        .any(|(i, c)| c.start != start + Duration::minutes(i as i64))
                {
                    return None;
                }
                Some(Bar {
                    start,
                    close: v.last()?.close,
                    high: v.iter().map(|c| c.high).fold(f64::NEG_INFINITY, f64::max),
                    low: v.iter().map(|c| c.low).fold(f64::INFINITY, f64::min),
                    volume: v.iter().map(|c| c.volume).sum(),
                })
            })
            .collect();
        frames.push(frame(&bars, minutes)?);
    }
    let bull = frames
        .iter()
        .filter(|f| f.ema_fast > f.ema_slow && f.macd > f.macd_signal && f.rsi > 50.)
        .count();
    let bear = frames
        .iter()
        .filter(|f| f.ema_fast < f.ema_slow && f.macd < f.macd_signal && f.rsi < 50.)
        .count();
    let direction = if bull >= 3 {
        Regime::Bullish
    } else if bear >= 3 {
        Regime::Bearish
    } else {
        Regime::Sideways
    };
    let high = frames[0].atr / frames[0].close > 0.005 || frames[3].atr / frames[3].close > 0.04;
    let (value, volume) =
        visible
            .values()
            .filter(|c| c.start >= session.open)
            .fold((0., 0.), |(v, n), c| {
                (
                    v + c.vwap.unwrap_or((c.high + c.low + c.close) / 3.) * c.volume,
                    n + c.volume,
                )
            });
    if volume <= 0. {
        return Err(invalid("Missing session VWAP volume"));
    }
    Ok(Context {
        symbol: symbol.into(),
        at,
        feed: feed.ok_or_else(|| invalid("Missing feed"))?,
        regime: if high {
            Regime::HighVolatility
        } else {
            direction
        },
        directional_regime: direction,
        frames,
        session_vwap: value / volume,
        reconstructed_history: assumed,
    })
}
