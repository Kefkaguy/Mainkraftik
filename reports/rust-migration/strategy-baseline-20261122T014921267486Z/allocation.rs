//! Preserve portfolio policy and Decimal share sizing separately from research improvements.
use crate::{Result, canonical, invalid, numpy_rng::Pcg64};
use bigdecimal::{BigDecimal, RoundingMode, Signed, Zero};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeMap, num::NonZeroU64, str::FromStr};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Observation {
    pub symbol: String,
    pub x: Vec<f64>,
    pub prior_return: f64,
    pub uncertainty: f64,
    pub price: f64,
    pub midpoint: f64,
    pub shortable: bool,
    pub asset: Value,
    pub source_id: String,
    #[serde(default)]
    pub cost_allowance: f64,
    #[serde(default)]
    pub quote_time: Option<chrono::DateTime<chrono::Utc>>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Target {
    pub target_value: f64,
    pub forecast_return: f64,
    pub source_id: String,
    pub x: Vec<f64>,
    pub prior_return: f64,
    pub price: f64,
    pub midpoint: f64,
    pub asset: Value,
    pub cost_allowance: f64,
}

pub fn allocate(
    rows: &[Observation],
    capacity: f64,
    adaptive: bool,
    slot: &str,
    correction: Option<&[f64]>,
) -> Result<BTreeMap<String, Target>> {
    if !capacity.is_finite() || capacity < 0.0 {
        return Err(invalid("invalid broker capacity"));
    }
    let mut eligible = Vec::new();
    let mut total = 0.;
    for row in rows {
        let mut mean = row.prior_return;
        if adaptive && let Some(beta) = correction {
            if beta.len() != row.x.len() {
                return Err(invalid("residual coefficient shape"));
            }
            mean += row.x.iter().zip(beta).map(|(x, b)| x * b).sum::<f64>();
        }
        let sigma = row.uncertainty.max(0.0001);
        if !mean.is_finite() || !sigma.is_finite() || !row.cost_allowance.is_finite() {
            return Err(invalid("invalid allocation forecast"));
        }
        let score = (mean.abs() - row.cost_allowance).max(0.0).copysign(mean) / sigma;
        let direction = if mean >= 0.0 { 1.0 } else { -1.0 };
        let exploration = if adaptive {
            let key = canonical::digest(&json!([slot, row.symbol]))?;
            let seed =
                u32::from_str_radix(&key[..8], 16).map_err(|_| invalid("exploration seed"))?;
            Pcg64::new(seed).normal() * 0.10
        } else {
            0.0
        };
        if direction < 0.0 && !row.shortable {
            continue;
        }
        let strength = (score.abs().min(10.0) + exploration).exp();
        total += strength;
        eligible.push((row, mean, direction, strength));
    }
    let mut targets = BTreeMap::new();
    if total == 0.0 {
        return Ok(targets);
    }
    for (row, mean, direction, strength) in eligible {
        targets.insert(
            row.symbol.clone(),
            Target {
                target_value: direction * capacity * strength / total,
                forecast_return: mean,
                source_id: row.source_id.clone(),
                x: row.x.clone(),
                prior_return: row.prior_return,
                price: row.price,
                midpoint: row.midpoint,
                asset: row.asset.clone(),
                cost_allowance: row.cost_allowance,
            },
        );
    }
    Ok(targets)
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct QuantityChange {
    pub side: String,
    pub qty: String,
    pub phase: String,
}

fn decimal(text: &str) -> Result<BigDecimal> {
    BigDecimal::from_str(text).map_err(|_| invalid("invalid decimal quantity"))
}
fn context(value: BigDecimal) -> Result<BigDecimal> {
    Ok(value.with_precision_round(
        NonZeroU64::new(28).ok_or_else(|| invalid("decimal precision"))?,
        RoundingMode::HalfEven,
    ))
}

pub fn within_buying_power(qty: &str, price: f64, buying_power: &str) -> Result<bool> {
    if !price.is_finite() || price <= 0.0 {
        return Err(invalid("invalid execution price"));
    }
    let gross = context(decimal(qty)? * decimal(&canonical::canonical(&json!(price))?)?)?;
    Ok(gross <= decimal(buying_power)?)
}
pub fn quantity_change(
    current: Option<&str>,
    target: f64,
    price: f64,
    fractionable: bool,
    shortable: bool,
) -> Result<Option<QuantityChange>> {
    if !target.is_finite() || !price.is_finite() || price <= 0.0 {
        return Err(invalid("invalid sizing target/price"));
    }
    let current = decimal(current.unwrap_or("0"))?;
    let mut desired = context(
        decimal(&canonical::canonical(&json!(target))?)?
            / decimal(&canonical::canonical(&json!(price))?)?,
    )?;
    if desired.is_negative() {
        if !shortable {
            return Ok(None);
        }
        desired = desired.with_scale_round(0, RoundingMode::Down);
    } else if !fractionable {
        desired = desired.with_scale_round(0, RoundingMode::Down);
    }
    if current.is_negative() != desired.is_negative() && !current.is_zero() && !desired.is_zero() {
        desired = BigDecimal::zero();
    }
    let delta = context(desired - &current)?;
    let qty = delta.abs().with_scale_round(9, RoundingMode::Down);
    if qty.is_zero() {
        return Ok(None);
    }
    if qty.digits() > 28 {
        return Err(invalid("share quantity exceeds Python Decimal context"));
    }
    let reduce = (!current.is_negative() && !current.is_zero() && delta.is_negative())
        || (current.is_negative() && delta.is_positive());
    Ok(Some(QuantityChange {
        side: if delta.is_positive() { "buy" } else { "sell" }.into(),
        qty: format!("{qty:.9}"),
        phase: if reduce { "reduce" } else { "increase" }.into(),
    }))
}
