//! A single serializable hourly artifact is used by research and engine planning.
use super::features::{FeatureRow, VERSION};
use crate::{Result, invalid, models::sigmoid, research_training::Heads};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Family {
    Ridge,
    Logistic,
    HistogramGradientBoosting,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TreeNode {
    pub feature: usize,
    pub threshold: f64,
    pub left: usize,
    pub right: usize,
    pub value: f64,
    pub leaf: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Boost {
    pub base: f64,
    pub trees: Vec<Vec<TreeNode>>,
    pub logistic: bool,
}
impl Boost {
    pub fn predict(&self, x: &[f64]) -> Result<f64> {
        let mut value = self.base;
        for tree in &self.trees {
            let mut index = 0;
            for depth in 0..=tree.len() {
                let node = tree
                    .get(index)
                    .ok_or_else(|| invalid("Invalid hourly tree reference"))?;
                if node.leaf {
                    value += node.value;
                    break;
                }
                if depth == tree.len() {
                    return Err(invalid("Cyclic hourly tree"));
                }
                let input = x
                    .get(node.feature)
                    .ok_or_else(|| invalid("Hourly tree feature mismatch"))?;
                index = if *input <= node.threshold {
                    node.left
                } else {
                    node.right
                };
            }
        }
        Ok(if self.logistic { sigmoid(value) } else { value })
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Calibration {
    pub return_intercept: f64,
    pub return_slope: f64,
    pub probability_intercept: f64,
    pub probability_slope: f64,
    pub residual_low: f64,
    pub residual_high: f64,
    pub sigma: f64,
    pub samples: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HourlyModel {
    pub feed: String,
    pub feature_sha256: String,
    pub version: u32,
    pub feature_version: String,
    pub horizon_minutes: u16,
    pub family: Family,
    pub columns: Vec<usize>,
    pub trained_through: DateTime<Utc>,
    pub calibrated_through: DateTime<Utc>,
    pub linear: Option<Heads>,
    pub return_boost: Option<Boost>,
    pub direction_boost: Option<Boost>,
    pub positive_mean: f64,
    pub negative_mean: f64,
    pub calibration: Calibration,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Forecast {
    pub issued_at: DateTime<Utc>,
    pub target_at: DateTime<Utc>,
    pub expected_return: f64,
    pub probability_up: f64,
    pub lower: f64,
    pub upper: f64,
    pub uncertainty: f64,
}
impl HourlyModel {
    pub fn raw(&self, x: &[f64]) -> Result<(f64, f64)> {
        let x: Vec<_> = self
            .columns
            .iter()
            .map(|&j| {
                x.get(j)
                    .copied()
                    .filter(|v| v.is_finite())
                    .ok_or_else(|| invalid("Hourly feature shape/value"))
            })
            .collect::<Result<_>>()?;
        match self.family {
            Family::Ridge | Family::Logistic => {
                let (r, p) = self
                    .linear
                    .as_ref()
                    .ok_or_else(|| invalid("Missing hourly linear heads"))?
                    .predict(&[x])?;
                Ok((
                    if self.family == Family::Logistic {
                        p[0] * self.positive_mean + (1. - p[0]) * self.negative_mean
                    } else {
                        r[0]
                    },
                    p[0],
                ))
            }
            Family::HistogramGradientBoosting => Ok((
                self.return_boost
                    .as_ref()
                    .ok_or_else(|| invalid("Missing return head"))?
                    .predict(&x)?,
                self.direction_boost
                    .as_ref()
                    .ok_or_else(|| invalid("Missing directional head"))?
                    .predict(&x)?,
            )),
        }
    }
    pub fn predict(&self, row: &FeatureRow) -> Result<Forecast> {
        if self.version != 1
            || self.feed != row.feed
            || self.feature_sha256 != super::features::implementation_hash()
            || self.feature_version != VERSION
            || self.horizon_minutes != 60
            || self.trained_through >= row.at
            || self.calibrated_through >= row.at
            || self.calibration.samples < 30
            || self.columns.contains(&14) && !row.quote_available
        {
            return Err(invalid(
                "Hourly artifact horizon, causal cutoff or quote coverage invalid",
            ));
        }
        let (r, p) = self.raw(&row.x)?;
        let c = &self.calibration;
        let p = p.clamp(1e-6, 1. - 1e-6);
        let expected = c.return_intercept + c.return_slope * r;
        let probability =
            sigmoid(c.probability_intercept + c.probability_slope * (p / (1. - p)).ln());
        let forecast = Forecast {
            issued_at: row.at,
            target_at: row.at + Duration::minutes(60),
            expected_return: expected,
            probability_up: probability,
            lower: expected + c.residual_low,
            upper: expected + c.residual_high,
            uncertainty: c.sigma,
        };
        if [
            forecast.expected_return,
            forecast.probability_up,
            forecast.lower,
            forecast.upper,
            forecast.uncertainty,
        ]
        .iter()
        .any(|v| !v.is_finite())
            || forecast.uncertainty <= 0.
            || forecast.lower > forecast.upper
        {
            return Err(invalid("Invalid hourly prediction/calibration"));
        }
        Ok(forecast)
    }
}
