use super::regime::{Context, Regime};
use crate::{Result, features::Quote, invalid};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Strategy {
    TrendFollowing,
    Breakout,
    MeanReversion,
    Pullback,
    Momentum,
}
pub const STRATEGIES: [Strategy; 5] = [
    Strategy::TrendFollowing,
    Strategy::Breakout,
    Strategy::MeanReversion,
    Strategy::Pullback,
    Strategy::Momentum,
];
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Signal {
    pub id: String,
    pub strategy: Strategy,
    pub symbol: String,
    pub issued_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub stop: f64,
    pub target: f64,
    pub invalidation: f64,
    pub maximum_hold_minutes: i64,
    pub x: Vec<f64>,
    pub regime: Regime,
    pub feed: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Decision {
    pub strategy: Strategy,
    pub signal: Option<Signal>,
    pub reason: String,
}
pub fn generate(
    context: &Context,
    quote: Option<&Quote>,
    session_close: DateTime<Utc>,
) -> Result<Vec<Decision>> {
    if context.frames.len() != 4
        || context
            .frames
            .iter()
            .zip([1, 5, 15, 60])
            .any(|(f, m)| f.minutes != m)
    {
        return Err(invalid("Strategy timeframe contract"));
    }
    let one = &context.frames[0];
    let five = &context.frames[1];
    let hour = &context.frames[3];
    let mut x = Vec::new();
    for f in &context.frames {
        x.extend([
            f.close / f.ema_fast - 1.,
            f.ema_fast / f.ema_slow - 1.,
            f.rsi / 100.,
            (f.macd - f.macd_signal) / f.close,
            f.atr / f.close,
            f.relative_volume.unwrap_or(0.),
            f.momentum,
            if f.higher_structure {
                1.
            } else if f.lower_structure {
                -1.
            } else {
                0.
            },
        ]);
    }
    x.extend([
        one.close / context.session_vwap - 1.,
        if one.relative_volume.is_some() {
            1.
        } else {
            0.
        },
    ]);
    for r in [
        Regime::Bullish,
        Regime::Bearish,
        Regime::Sideways,
        Regime::HighVolatility,
    ] {
        x.push(if context.regime == r { 1. } else { 0. });
    }
    let mut result = Vec::new();
    for (index, strategy) in STRATEGIES.into_iter().enumerate() {
        let reject = if context.regime == Regime::HighVolatility {
            Some("HIGH_VOLATILITY_NO_ENTRY")
        } else if context.at + Duration::minutes(65) >= session_close {
            Some("ENTRY_WINDOW_REQUIRES_EXIT_BEFORE_CLOSE")
        } else if quote.is_none_or(|q| q.validate(context.at).is_err()) {
            Some("MISSING_OR_STALE_EXECUTABLE_QUOTE")
        } else if quote.is_some_and(|q| (q.ask - q.bid) / ((q.ask + q.bid) / 2.) > 0.003) {
            Some("SPREAD_EXCEEDS_30_BPS")
        } else if context.regime == Regime::Bearish {
            Some("BEARISH_LONG_ENTRY_VETO_BORROW_UNVERIFIED")
        } else {
            None
        };
        if let Some(reason) = reject {
            result.push(Decision {
                strategy,
                signal: None,
                reason: reason.into(),
            });
            continue;
        }
        let (entry, reason, hold, invalidation) = match strategy {
            Strategy::TrendFollowing => (
                context.regime == Regime::Bullish
                    && five.ema_fast > five.ema_slow
                    && one.close > context.session_vwap
                    && (50. ..=70.).contains(&one.rsi)
                    && five.higher_structure,
                "TREND_ALIGNMENT_STRUCTURE_VWAP",
                60,
                context.session_vwap,
            ),
            Strategy::Breakout => (
                one.close > five.range_high
                    && five.relative_volume.is_some_and(|v| v >= 1.2)
                    && one.close > context.session_vwap,
                "PRIOR_RANGE_BREAK_RELATIVE_VOLUME",
                30,
                five.range_high,
            ),
            Strategy::MeanReversion => (
                context.regime == Regime::Sideways
                    && one.rsi < 35.
                    && one.close < context.session_vwap - one.atr
                    && one.close > one.previous_close,
                "SIDEWAYS_OVERSOLD_REBOUND",
                30,
                one.close - one.atr,
            ),
            Strategy::Pullback => (
                hour.ema_fast > hour.ema_slow
                    && five.ema_fast > five.ema_slow
                    && one.close >= five.ema_slow
                    && one.close <= five.ema_fast + 0.25 * five.atr
                    && (35. ..=55.).contains(&one.rsi)
                    && one.close > one.previous_close,
                "HIGHER_TREND_PULLBACK_REBOUND",
                60,
                five.ema_slow,
            ),
            Strategy::Momentum => (
                five.momentum > five.atr / five.close
                    && five.macd > five.macd_signal
                    && one.relative_volume.is_some_and(|v| v >= 1.2)
                    && (55. ..=75.).contains(&one.rsi),
                "MOMENTUM_MACD_VOLUME_CONFIRMATION",
                30,
                one.close - one.atr,
            ),
        };
        if !entry {
            result.push(Decision {
                strategy,
                signal: None,
                reason: format!("NOT_MET_{reason}"),
            });
            continue;
        }
        let price = quote.ok_or_else(|| invalid("Entry quote missing"))?.ask;
        let distance = (1.5 * five.atr).min(0.02 * price).max(0.001 * price);
        let target = if strategy == Strategy::MeanReversion {
            context.session_vwap.min(price + 0.03 * price)
        } else {
            price + (2. * distance).min(0.03 * price)
        };
        if target <= price || invalidation >= price {
            result.push(Decision {
                strategy,
                signal: None,
                reason: "INVALID_OR_ALREADY_CROSSED_ENTRY_GEOMETRY".into(),
            });
            continue;
        }
        let mut features = x.clone();
        for n in 0..5 {
            features.push(if n == index { 1. } else { 0. });
        }
        features.extend([
            distance / price,
            (target - price) / price,
            hold as f64 / 60.,
        ]);
        let id = crate::canonical::digest(&serde_json::json!([
            "multi-strategy-v1",
            context.symbol,
            context.at,
            strategy,
            price,
            context.feed
        ]))?;
        result.push(Decision {
            strategy,
            signal: Some(Signal {
                id,
                strategy,
                symbol: context.symbol.clone(),
                issued_at: context.at,
                expires_at: context.at + Duration::seconds(15),
                stop: price - distance,
                target,
                invalidation,
                maximum_hold_minutes: hold,
                x: features,
                regime: context.regime,
                feed: context.feed.clone(),
            }),
            reason: reason.into(),
        });
    }
    Ok(result)
}
impl Signal {
    /// Evaluated for every owned position independently of fresh entry admission.
    pub fn exit_reason(&self, at: DateTime<Utc>, bid: f64) -> Option<&'static str> {
        if bid <= self.stop {
            Some("STOP_LOSS")
        } else if bid >= self.target {
            Some("TARGET")
        } else if bid < self.invalidation {
            Some("STRUCTURE_INVALIDATED")
        } else if at >= self.issued_at + Duration::minutes(self.maximum_hold_minutes) {
            Some("MAXIMUM_HOLD")
        } else {
            None
        }
    }
}
