//! Portfolio replay with delayed, quote-backed execution and persistent partial
//! reservations. Bar-only forecast accuracy cannot become an executable P/L claim.
use super::{
    allocation::{self, Costs, Opportunity, Position, Risk},
    models::Forecast,
};
use crate::{Result, calendar::Calendar, features::Quote, invalid};
use chrono::{DateTime, Datelike, Duration, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Tick {
    pub at: DateTime<Utc>,
    pub symbol: String,
    pub group: String,
    pub quote: Option<Quote>,
    pub forecast: Option<Forecast>,
    pub executable_volume: f64,
    pub shortable: bool,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Execution {
    pub latency_seconds: i64,
    pub order_ttl_seconds: i64,
    pub participation: f64,
    pub fill_fraction: f64,
}
impl Default for Execution {
    fn default() -> Self {
        Self {
            latency_seconds: 1,
            order_ttl_seconds: 60,
            participation: 0.01,
            fill_fraction: 0.5,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Point {
    pub at: DateTime<Utc>,
    pub equity: f64,
    pub cash: f64,
    pub gross: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Metrics {
    pub initial_capital: f64,
    pub net_pnl: f64,
    pub realized_trade_pnl: f64,
    pub unrealized_pnl: f64,
    pub weekly_pnl: BTreeMap<String, f64>,
    pub daily_pnl: BTreeMap<NaiveDate, f64>,
    pub maximum_drawdown: f64,
    pub sharpe: Option<f64>,
    pub sortino: Option<f64>,
    pub profit_factor: Option<f64>,
    pub win_rate: Option<f64>,
    pub closed_trades: usize,
    pub turnover: f64,
    pub execution_costs: f64,
    pub spread_cost: f64,
    pub slippage_cost: f64,
    pub fee_cost: f64,
    pub borrow_cost: f64,
    pub submitted_orders: usize,
    pub rejected_orders: usize,
    pub expired_orders: usize,
    pub partial_fills: usize,
    pub missing_quote_events: usize,
    pub executable_quote_coverage: f64,
    pub forecast_mae: Option<f64>,
    pub brier: Option<f64>,
    pub directional_accuracy: Option<f64>,
    pub calibration: Vec<serde_json::Value>,
    pub open_positions: usize,
    pub pending_orders: usize,
    pub hypothetical_execution: bool,
    pub daily_risk_unobserved: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Simulation {
    pub metrics: Metrics,
    pub curve: Vec<Point>,
    pub reasons: BTreeMap<String, usize>,
}
struct Held {
    shares: f64,
    entry: f64,
    fees: f64,
    borrow: f64,
    pnl: f64,
    opened: DateTime<Utc>,
    mark: f64,
}
struct Pending {
    symbol: String,
    shares: f64,
    issued: DateTime<Utc>,
    filled_shares: f64,
    filled_notional: f64,
    fees_paid: f64,
}
fn average(x: &[f64]) -> f64 {
    x.iter().sum::<f64>() / x.len() as f64
}
fn deviation(x: &[f64]) -> f64 {
    let m = average(x);
    (x.iter().map(|v| (v - m).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
}

pub fn simulate(
    calendar: &Calendar,
    ticks: &[Tick],
    capital: f64,
    rebalance_minutes: i64,
    risk: Risk,
    costs: Costs,
    execution: Execution,
) -> Result<Simulation> {
    if !matches!(rebalance_minutes, 5 | 15 | 30 | 60)
        || !capital.is_finite()
        || capital <= 0.
        || execution.latency_seconds <= 0
        || execution.order_ttl_seconds <= execution.latency_seconds
        || !(0. ..=1.).contains(&execution.participation)
        || execution.participation == 0.
        || !(0. ..=1.).contains(&execution.fill_fraction)
        || execution.fill_fraction == 0.
    {
        return Err(invalid("Invalid intraday replay protocol"));
    }
    let mut times = BTreeMap::<DateTime<Utc>, Vec<&Tick>>::new();
    let mut identities = BTreeSet::new();
    for t in ticks {
        if !t.executable_volume.is_finite()
            || t.executable_volume < 0.
            || !identities.insert((t.at, t.symbol.clone()))
        {
            return Err(invalid("Duplicate or invalid replay tick"));
        }
        if t.forecast.as_ref().is_some_and(|f| f.issued_at > t.at) {
            return Err(invalid("Future forecast in replay"));
        }
        times.entry(t.at).or_default().push(t);
    }
    let mut cash = capital;
    let mut held = BTreeMap::<String, Held>::new();
    let mut pending = Vec::<Pending>::new();
    let mut latest = BTreeMap::<String, Tick>::new();
    let mut curve = Vec::new();
    let mut closed = Vec::new();
    let mut reasons = BTreeMap::new();
    let (mut spread_cost, mut slippage_cost, mut fee_cost, mut borrow_cost, mut turnover) =
        (0., 0., 0., 0., 0.);
    let (
        mut submitted,
        mut rejected,
        mut expired,
        mut partials,
        mut missing,
        mut observed,
        mut unobserved,
    ) = (0usize, 0usize, 0usize, 0usize, 0usize, 0usize, 0usize);
    let mut last: Option<DateTime<Utc>> = None;
    let mut last_slot = None;
    for (at, events) in times {
        let day = at.with_timezone(&New_York).date_naive();
        let session = calendar
            .session(day)
            .ok_or_else(|| invalid("Replay timestamp outside exchange calendar"))?;
        if at < session.open || at > session.close {
            continue;
        }
        if let Some(previous) = last {
            let elapsed = (at - previous).num_seconds().max(0) as f64;
            for p in held.values_mut().filter(|p| p.shares < 0.) {
                let cost = p.shares.abs() * p.mark * costs.annual_short_borrow * elapsed
                    / (365.25 * 86400.);
                cash -= cost;
                p.borrow += cost;
                borrow_cost += cost;
            }
        }
        last = Some(at);
        let mut available = BTreeMap::new();
        for event in events {
            let quote = event
                .quote
                .as_ref()
                .filter(|q| q.validate(at).is_ok() && q.acquired_at <= at);
            if let Some(q) = quote {
                observed += 1;
                if let Some(p) = held.get_mut(&event.symbol) {
                    p.mark = q.midpoint();
                }
                available.insert(
                    event.symbol.clone(),
                    event.executable_volume * execution.participation,
                );
            } else {
                missing += 1;
            }
            latest.insert(event.symbol.clone(), event.clone());
        }
        // Existing reservations fill only on a later observable quote. No future
        // quote is substituted, and remaining shares survive partial execution.
        let mut keep = Vec::new();
        for mut order in pending.drain(..) {
            if at - order.issued > Duration::seconds(execution.order_ttl_seconds) {
                expired += 1;
                continue;
            }
            if at - order.issued < Duration::seconds(execution.latency_seconds) {
                keep.push(order);
                continue;
            }
            let Some(tick) = latest.get(&order.symbol).filter(|t| t.at == at) else {
                keep.push(order);
                continue;
            };
            let Some(q) = tick
                .quote
                .as_ref()
                .filter(|q| q.validate(at).is_ok() && q.acquired_at <= at)
            else {
                keep.push(order);
                continue;
            };
            if q.source_time < order.issued + Duration::seconds(execution.latency_seconds) {
                keep.push(order);
                continue;
            }
            let volume = available
                .get_mut(&order.symbol)
                .ok_or_else(|| invalid("Missing executable volume"))?;
            let amount = order.shares.abs().min(*volume) * execution.fill_fraction;
            if amount < 1e-9 {
                keep.push(order);
                continue;
            }
            let signed = amount.copysign(order.shares);
            let side_price = if signed > 0. { q.ask } else { q.bid };
            let slip = (costs.slippage_bps + costs.execution_uncertainty_bps) / 10000.;
            let price = side_price * (1. + signed.signum() * slip);
            let cumulative_fee = costs.order_fee(
                order.filled_shares + amount,
                order.filled_notional + amount * price,
                signed < 0.,
            );
            let fee = (cumulative_fee - order.fees_paid).max(0.);
            let current = held.get(&order.symbol).map_or(0., |p| p.shares);
            let reducing = current * signed < 0.;
            let equity = cash + held.values().map(|p| p.shares * p.mark).sum::<f64>();
            let gross = held
                .values()
                .map(|p| (p.shares * p.mark).abs())
                .sum::<f64>();
            if !reducing
                && (gross + amount * price > equity * risk.gross_fraction
                    || signed < 0. && !tick.shortable
                    || signed > 0. && amount * price + fee > cash)
            {
                rejected += 1;
                continue;
            }
            order.filled_shares += amount;
            order.filled_notional += amount * price;
            order.fees_paid = cumulative_fee;
            cash -= signed * price + fee;
            *volume -= amount;
            turnover += amount * price;
            spread_cost += amount * (side_price - q.midpoint()).abs();
            slippage_cost += amount * (price - side_price).abs();
            fee_cost += fee;
            if reducing {
                let p = held
                    .get_mut(&order.symbol)
                    .ok_or_else(|| invalid("Position disappeared"))?;
                let amount = amount.min(p.shares.abs());
                let fraction = amount / p.shares.abs();
                p.pnl += p.shares.signum() * amount * (price - p.entry)
                    - fraction * (p.fees + p.borrow)
                    - fee;
                p.fees *= 1. - fraction;
                p.borrow *= 1. - fraction;
                p.shares += signed;
                if p.shares.abs() < 1e-8 {
                    let p = held
                        .remove(&order.symbol)
                        .ok_or_else(|| invalid("Closed position missing"))?;
                    closed.push(p.pnl);
                }
            } else {
                let p = held.entry(order.symbol.clone()).or_insert(Held {
                    shares: 0.,
                    entry: price,
                    fees: 0.,
                    borrow: 0.,
                    pnl: 0.,
                    opened: at,
                    mark: q.midpoint(),
                });
                p.entry = (p.entry * p.shares.abs() + price * amount) / (p.shares.abs() + amount);
                p.shares += signed;
                p.fees += fee;
            }
            order.shares -= signed;
            if order.shares.abs() > 1e-8 {
                partials += 1;
                keep.push(order);
            }
        }
        pending = keep;
        let equity = cash + held.values().map(|p| p.shares * p.mark).sum::<f64>();
        if equity <= 0. {
            return Err(invalid("Replay insolvent; candidate rejected"));
        }
        let slot = super::engine::cadence_slot(calendar, at, rebalance_minutes)
            .ok_or_else(|| invalid("Replay cadence outside session"))?;
        let due = last_slot != Some(slot);
        if due {
            last_slot = Some(slot);
        }
        let rows: Vec<_> = latest
            .values()
            .map(|t| Opportunity {
                symbol: t.symbol.clone(),
                group: t.group.clone(),
                forecast: t.forecast.clone(),
                quote: t.quote.clone(),
                shortable: t.shortable,
                position: held.get(&t.symbol).map(|p| Position {
                    shares: p.shares,
                    entry_price: p.entry,
                    mark_price: p.mark,
                    opened_at: p.opened,
                }),
            })
            .collect();
        let plan = allocation::plan(
            &rows,
            equity,
            equity * risk.gross_fraction,
            at,
            due,
            risk,
            costs,
        )?;
        unobserved += plan.risk_unobserved.len();
        for decision in plan.decisions {
            *reasons.entry(format!("{:?}", decision.reason)).or_insert(0) += 1;
            if !decision.executable {
                continue;
            }
            if matches!(
                decision.reason,
                allocation::Reason::ForcedExit | allocation::Reason::RiskReduction
            ) {
                pending.retain(|p| p.symbol != decision.symbol);
            }
            if pending.iter().any(|p| p.symbol == decision.symbol) {
                continue;
            }
            let tick = latest
                .get(&decision.symbol)
                .ok_or_else(|| invalid("Strategy tick missing"))?;
            let q = tick
                .quote
                .as_ref()
                .ok_or_else(|| invalid("Quote missing for target"))?;
            let position = held.get(&decision.symbol).map(|p| p.shares.to_string());
            let price = if decision.target_value >= 0. {
                q.ask
            } else {
                q.bid
            };
            if let Some(change) = crate::allocation::quantity_change(
                position.as_deref(),
                decision.target_value,
                price,
                true,
                tick.shortable,
            )? {
                let quantity: f64 = change
                    .qty
                    .parse()
                    .map_err(|_| invalid("Invalid simulated quantity"))?;
                // A hold has no rebalance order, including differences caused only by marking.
                if decision.target_value == decision.current_value {
                    continue;
                }
                pending.push(Pending {
                    symbol: decision.symbol,
                    shares: quantity * if change.side == "buy" { 1. } else { -1. },
                    issued: at,
                    filled_shares: 0.,
                    filled_notional: 0.,
                    fees_paid: 0.,
                });
                submitted += 1;
            }
        }
        curve.push(Point {
            at,
            equity,
            cash,
            gross: held.values().map(|p| (p.shares * p.mark).abs()).sum(),
        });
    }
    let mut ends = BTreeMap::new();
    for p in &curve {
        ends.insert(p.at.with_timezone(&New_York).date_naive(), p.equity);
    }
    let mut daily_pnl = BTreeMap::new();
    let mut weekly_pnl = BTreeMap::new();
    let mut previous = capital;
    let mut returns = Vec::new();
    for (day, equity) in ends {
        let pnl = equity - previous;
        daily_pnl.insert(day, pnl);
        *weekly_pnl
            .entry(format!(
                "{}-W{:02}",
                day.iso_week().year(),
                day.iso_week().week()
            ))
            .or_insert(0.) += pnl;
        returns.push(equity / previous - 1.);
        previous = equity;
    }
    let mut peak = capital;
    let mut maximum_drawdown = 0f64;
    for p in &curve {
        peak = peak.max(p.equity);
        maximum_drawdown = maximum_drawdown.min(p.equity / peak - 1.);
    }
    let sigma = if returns.len() > 1 {
        deviation(&returns)
    } else {
        0.
    };
    let downside = if returns.is_empty() {
        0.
    } else {
        (returns.iter().map(|v| v.min(0.).powi(2)).sum::<f64>() / returns.len() as f64).sqrt()
    };
    let realized = closed.iter().sum::<f64>();
    let net_pnl = previous - capital;
    let gains = closed.iter().filter(|p| **p > 0.).sum::<f64>();
    let losses = -closed.iter().filter(|p| **p < 0.).sum::<f64>();
    let metrics = Metrics {
        initial_capital: capital,
        net_pnl,
        realized_trade_pnl: realized,
        unrealized_pnl: net_pnl - realized,
        weekly_pnl,
        daily_pnl,
        maximum_drawdown,
        sharpe: if sigma > 0. {
            Some(average(&returns) / sigma * 252f64.sqrt())
        } else {
            None
        },
        sortino: if downside > 0. {
            Some(average(&returns) / downside * 252f64.sqrt())
        } else {
            None
        },
        profit_factor: if losses > 0. {
            Some(gains / losses)
        } else {
            None
        },
        win_rate: if closed.is_empty() {
            None
        } else {
            Some(closed.iter().filter(|p| **p > 0.).count() as f64 / closed.len() as f64)
        },
        closed_trades: closed.len(),
        turnover: turnover / capital,
        execution_costs: spread_cost + slippage_cost + fee_cost + borrow_cost,
        spread_cost,
        slippage_cost,
        fee_cost,
        borrow_cost,
        submitted_orders: submitted,
        rejected_orders: rejected,
        expired_orders: expired,
        partial_fills: partials,
        missing_quote_events: missing,
        executable_quote_coverage: if observed + missing > 0 {
            observed as f64 / (observed + missing) as f64
        } else {
            0.
        },
        forecast_mae: None,
        brier: None,
        directional_accuracy: None,
        calibration: Vec::new(),
        open_positions: held.len(),
        pending_orders: pending.len(),
        hypothetical_execution: true,
        daily_risk_unobserved: unobserved,
    };
    Ok(Simulation {
        metrics,
        curve,
        reasons,
    })
}
