//! Cost-aware target changes with cash, explicit holds and independent risk exits.
use super::models::Forecast;
use crate::{Result, features::Quote, invalid};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Position {
    pub shares: f64,
    pub entry_price: f64,
    pub mark_price: f64,
    pub opened_at: DateTime<Utc>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Opportunity {
    pub symbol: String,
    pub group: String,
    pub forecast: Option<Forecast>,
    pub quote: Option<Quote>,
    pub position: Option<Position>,
    pub shortable: bool,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Costs {
    pub slippage_bps: f64,
    pub fee_bps: f64,
    pub execution_uncertainty_bps: f64,
    pub annual_short_borrow: f64,
    pub sell_sec_per_dollar: f64,
    pub sell_taf_per_share: f64,
    pub sell_taf_cap: f64,
}
impl Default for Costs {
    fn default() -> Self {
        Self {
            slippage_bps: 2.,
            fee_bps: 0.1,
            execution_uncertainty_bps: 1.,
            annual_short_borrow: 0.03,
            sell_sec_per_dollar: 0.00002060,
            sell_taf_per_share: 0.000195,
            sell_taf_cap: 9.79,
        }
    }
}
impl Costs {
    /// Prospective planning schedule, not a claim about historical invoices.
    /// Cumulative values let replay round regulatory fees once per order.
    pub fn order_fee(&self, shares: f64, notional: f64, sell: bool) -> f64 {
        let extra = if sell && shares > 0. {
            (notional * self.sell_sec_per_dollar * 100.).ceil() / 100.
                + (shares * self.sell_taf_per_share * 100.)
                    .ceil()
                    .min(self.sell_taf_cap * 100.)
                    / 100.
        } else {
            0.
        };
        notional * self.fee_bps / 10000. + extra
    }
    fn incremental_cost(&self, current: f64, target: f64, q: &Quote) -> Result<f64> {
        let delta = target - current;
        let price = if delta >= 0. { q.ask } else { q.bid };
        let variable = self.one_way(q)? - self.fee_bps / 10000.;
        let entry =
            delta.abs() * variable + self.order_fee(delta.abs() / price, delta.abs(), delta < 0.);
        let additional = if current * target < 0. {
            target.abs()
        } else {
            (target.abs() - current.abs()).max(0.)
        };
        let exit_price = if target >= 0. { q.bid } else { q.ask };
        Ok(entry
            + additional * variable
            + self.order_fee(additional / exit_price, additional, target > 0.))
    }
    pub fn one_way(&self, quote: &Quote) -> Result<f64> {
        if [
            self.slippage_bps,
            self.fee_bps,
            self.execution_uncertainty_bps,
            self.annual_short_borrow,
            self.sell_sec_per_dollar,
            self.sell_taf_per_share,
            self.sell_taf_cap,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v < 0.)
        {
            return Err(invalid("Invalid explicit execution costs"));
        }
        Ok((quote.ask - quote.bid) / (quote.ask + quote.bid)
            + (self.slippage_bps + self.fee_bps + self.execution_uncertainty_bps) / 10000.)
    }
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Risk {
    pub minimum_incremental_benefit: f64,
    pub gross_fraction: f64,
    pub symbol_fraction: f64,
    pub group_fraction: f64,
    pub risk_aversion: f64,
    pub stop_loss: f64,
    pub take_profit: f64,
    pub maximum_hold_minutes: i64,
    pub probability_weighting: bool,
}
impl Default for Risk {
    fn default() -> Self {
        Self {
            minimum_incremental_benefit: 0.01,
            gross_fraction: 1.,
            symbol_fraction: 0.2,
            group_fraction: 0.4,
            risk_aversion: 10.,
            stop_loss: 0.02,
            take_profit: 0.03,
            maximum_hold_minutes: 60,
            probability_weighting: true,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Reason {
    NoNetEdge,
    Hold,
    IncrementalBenefitInsufficient,
    TargetChange,
    ForcedExit,
    RiskReduction,
    QuoteUnavailable,
    ForecastUnavailable,
    RebalanceNotDue,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Decision {
    pub symbol: String,
    pub current_value: f64,
    pub target_value: f64,
    pub executable: bool,
    pub reason: Reason,
    pub expected_incremental_net: f64,
    pub estimated_change_cost: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    pub decisions: Vec<Decision>,
    pub unallocated_cash: f64,
    pub gross_target: f64,
    pub risk_unobserved: Vec<String>,
}

pub fn plan(
    rows: &[Opportunity],
    equity: f64,
    broker_capacity: f64,
    at: DateTime<Utc>,
    rebalance_due: bool,
    risk: Risk,
    costs: Costs,
) -> Result<Plan> {
    if !risk.minimum_incremental_benefit.is_finite()
        || risk.minimum_incremental_benefit < 0.
        || !equity.is_finite()
        || equity <= 0.
        || !broker_capacity.is_finite()
        || broker_capacity < 0.
        || [
            risk.gross_fraction,
            risk.symbol_fraction,
            risk.group_fraction,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v <= 0. || *v > 1.)
        || !risk.risk_aversion.is_finite()
        || risk.risk_aversion <= 0.
        || risk.maximum_hold_minutes <= 0
        || !risk.stop_loss.is_finite()
        || risk.stop_loss <= 0.
        || !risk.take_profit.is_finite()
        || risk.take_profit <= 0.
    {
        return Err(invalid("Invalid fixed capital/risk assumptions"));
    }
    let budget = (equity * risk.gross_fraction).min(broker_capacity);
    let per_symbol = equity * risk.symbol_fraction;
    let mut decisions = Vec::new();
    let mut proposals = Vec::new();
    let mut unobserved = Vec::new();
    let mut symbols = BTreeSet::new();
    for row in rows {
        if row.symbol.is_empty() || !symbols.insert(row.symbol.clone()) {
            return Err(invalid("Unique strategy symbols required"));
        }
        if let Some(p) = &row.position
            && ([p.shares, p.entry_price, p.mark_price]
                .iter()
                .any(|v| !v.is_finite())
                || p.entry_price <= 0.
                || p.mark_price <= 0.
                || p.opened_at > at)
        {
            return Err(invalid("Invalid owned position"));
        }
        let quote = row
            .quote
            .as_ref()
            .filter(|q| q.validate(at).is_ok() && q.acquired_at <= at);
        let current = row.position.as_ref().map_or(0., |p| {
            p.shares * quote.map_or(p.mark_price, Quote::midpoint)
        });
        let mut decision = Decision {
            symbol: row.symbol.clone(),
            current_value: current,
            target_value: current,
            executable: false,
            reason: Reason::Hold,
            expected_incremental_net: 0.,
            estimated_change_cost: 0.,
        };
        let group = if row.group.is_empty() {
            "unknown"
        } else {
            row.group.as_str()
        };
        if let Some(q) = quote {
            costs.one_way(q)?;
            decision.executable = true;
            let forced = row.position.as_ref().is_some_and(|p| {
                let pnl = (q.midpoint() / p.entry_price - 1.) * p.shares.signum();
                pnl <= -risk.stop_loss
                    || pnl >= risk.take_profit
                    || at - p.opened_at >= Duration::minutes(risk.maximum_hold_minutes)
            });
            if forced {
                decision.target_value = 0.;
                decision.reason = Reason::ForcedExit;
                decision.estimated_change_cost = costs.incremental_cost(current, 0., q)?;
            } else if current.abs() > per_symbol {
                decision.target_value = current.signum() * per_symbol;
                decision.reason = Reason::RiskReduction;
                decision.estimated_change_cost =
                    costs.incremental_cost(current, decision.target_value, q)?;
            } else if !rebalance_due {
                decision.reason = Reason::RebalanceNotDue;
            } else if let Some(f) = row.forecast.as_ref().filter(|f| {
                f.issued_at <= at
                    && at - f.issued_at <= Duration::minutes(1)
                    && f.target_at - f.issued_at == Duration::minutes(60)
            }) {
                if [f.expected_return, f.uncertainty, f.probability_up]
                    .iter()
                    .any(|v| !v.is_finite())
                    || f.uncertainty <= 0.
                    || !(0. ..=1.).contains(&f.probability_up)
                {
                    return Err(invalid("Invalid calibrated hourly forecast"));
                }
                let sign = f.expected_return.signum();
                let p = if sign >= 0. {
                    f.probability_up
                } else {
                    1. - f.probability_up
                };
                let borrow = if sign < 0. {
                    costs.annual_short_borrow / 8766.
                } else {
                    0.
                };
                let net = f.expected_return.abs()
                    - costs.incremental_cost(0., sign * per_symbol, q)? / per_symbol
                    - borrow;
                let eligible = net > 0. && p > 0.5 && (sign > 0. || row.shortable);
                let mut target = if eligible {
                    let probability = if risk.probability_weighting {
                        (2. * p - 1.).max(0.)
                    } else {
                        1.
                    };
                    sign * (equity * net / (risk.risk_aversion * f.uncertainty.powi(2))
                        * probability)
                        .min(per_symbol)
                } else {
                    current
                };
                // Entry rejection does not orphan a held position. Close an adverse
                // holding when avoiding its expected loss pays the incremental exit cost.
                if !eligible
                    && current * f.expected_return < -costs.incremental_cost(current, 0., q)?
                {
                    target = 0.;
                }
                let utility = |v: f64| {
                    v * f.expected_return
                        - 0.5 * risk.risk_aversion * f.uncertainty.powi(2) * v.powi(2) / equity
                        - if v < 0. {
                            v.abs() * costs.annual_short_borrow / 8766.
                        } else {
                            0.
                        }
                };
                let change_cost = costs.incremental_cost(current, target, q)?;
                let benefit = utility(target) - utility(current) - change_cost;
                if target != current && benefit > 0. {
                    decision.target_value = target;
                    decision.reason = Reason::TargetChange;
                    decision.expected_incremental_net = benefit;
                    decision.estimated_change_cost = change_cost;
                    proposals.push((decisions.len(), group.to_owned()));
                } else {
                    decision.reason = if !eligible {
                        Reason::NoNetEdge
                    } else {
                        Reason::IncrementalBenefitInsufficient
                    };
                }
            } else {
                decision.reason = Reason::ForecastUnavailable;
            }
        } else {
            decision.reason = Reason::QuoteUnavailable;
            if current != 0. {
                unobserved.push(row.symbol.clone());
            }
        }
        decisions.push(decision);
    }
    // Reserve existing holdings first. New opportunities may use remaining risk
    // capacity; they never force sales merely to fund another attractive entry.
    let desired: Vec<_> = decisions.iter().map(|d| d.target_value).collect();
    for d in &mut decisions {
        if d.reason == Reason::TargetChange
            && d.target_value.abs() > d.current_value.abs()
            && d.target_value.signum() == d.current_value.signum()
        {
            d.target_value = d.current_value;
        } else if d.reason == Reason::TargetChange
            && (d.current_value == 0. || d.target_value * d.current_value < 0.)
        {
            d.target_value = 0.;
        }
    }
    let group_of = |i: usize| {
        if rows[i].group.is_empty() {
            "unknown"
        } else {
            rows[i].group.as_str()
        }
    };
    let groups: BTreeSet<_> = (0..rows.len()).map(group_of).collect();
    for group in groups {
        let total: f64 = decisions
            .iter()
            .enumerate()
            .filter(|(i, _)| group_of(*i) == group)
            .map(|(_, d)| d.target_value.abs())
            .sum();
        let locked: f64 = decisions
            .iter()
            .enumerate()
            .filter(|(i, d)| group_of(*i) == group && !d.executable)
            .map(|(_, d)| d.target_value.abs())
            .sum();
        if total > equity * risk.group_fraction {
            let scale = ((equity * risk.group_fraction - locked).max(0.)
                / (total - locked).max(1e-9))
            .min(1.);
            for (i, d) in decisions
                .iter_mut()
                .enumerate()
                .filter(|(i, d)| group_of(*i) == group && d.executable)
            {
                let _ = i;
                d.target_value *= scale;
                d.reason = Reason::RiskReduction;
            }
        }
    }
    let total: f64 = decisions.iter().map(|d| d.target_value.abs()).sum();
    let locked: f64 = decisions
        .iter()
        .filter(|d| !d.executable)
        .map(|d| d.target_value.abs())
        .sum();
    if total > budget {
        let scale = ((budget - locked).max(0.) / (total - locked).max(1e-9)).min(1.);
        for d in decisions.iter_mut().filter(|d| d.executable) {
            d.target_value *= scale;
            d.reason = Reason::RiskReduction;
        }
    }
    proposals.sort_by(|a, b| {
        decisions[b.0]
            .expected_incremental_net
            .total_cmp(&decisions[a.0].expected_incremental_net)
            .then(a.0.cmp(&b.0))
    });
    for (index, group) in proposals {
        if decisions[index].reason == Reason::RiskReduction {
            continue;
        }
        let old = decisions[index].target_value;
        if desired[index].abs() > old.abs() {
            let gross: f64 = decisions.iter().map(|d| d.target_value.abs()).sum();
            let cluster: f64 = decisions
                .iter()
                .enumerate()
                .filter(|(i, _)| group_of(*i) == group)
                .map(|(_, d)| d.target_value.abs())
                .sum();
            let room = (budget - gross)
                .min(equity * risk.group_fraction - cluster)
                .max(0.);
            let amount = desired[index].abs().min(old.abs() + room).min(per_symbol);
            let proposed = desired[index].signum() * amount;
            let f = rows[index]
                .forecast
                .as_ref()
                .ok_or_else(|| invalid("Forecast missing after sizing"))?;
            let d = &mut decisions[index];
            let delta = proposed - d.current_value;
            let benefit = delta * f.expected_return
                - 0.5
                    * risk.risk_aversion
                    * f.uncertainty.powi(2)
                    * (proposed.powi(2) - d.current_value.powi(2))
                    / equity
                - costs.incremental_cost(
                    d.current_value,
                    proposed,
                    rows[index]
                        .quote
                        .as_ref()
                        .ok_or_else(|| invalid("Quote missing"))?,
                )?
                - if proposed < 0. {
                    proposed.abs() * costs.annual_short_borrow / 8766.
                } else {
                    0.
                };
            if benefit > risk.minimum_incremental_benefit {
                d.target_value = proposed;
                d.expected_incremental_net = benefit;
            } else {
                d.reason = Reason::IncrementalBenefitInsufficient;
            }
        }
    }
    for (row, d) in rows.iter().zip(&mut decisions) {
        if d.executable {
            let q = row
                .quote
                .as_ref()
                .ok_or_else(|| invalid("Executable quote missing"))?;
            d.estimated_change_cost = costs.incremental_cost(d.current_value, d.target_value, q)?;
        }
    }
    let gross_target = decisions.iter().map(|d| d.target_value.abs()).sum::<f64>();
    Ok(Plan {
        decisions,
        unallocated_cash: (equity - gross_target).max(0.),
        gross_target,
        risk_unobserved: unobserved,
    })
}
