//! Inference of existing portable numeric artifacts; no retraining or algorithm substitution.
use crate::{Result, invalid};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Calibration {
    pub intercept: f64,
    pub coefficient: f64,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Node {
    pub value: f64,
    pub feature_idx: usize,
    pub num_threshold: f64,
    pub missing_go_to_left: u8,
    pub left: usize,
    pub right: usize,
    pub is_leaf: u8,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TreeHead {
    pub base: f64,
    pub trees: Vec<Vec<Node>>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct NumericModel {
    pub family: String,
    pub columns: Vec<usize>,
    pub imputation: Vec<f64>,
    #[serde(default)]
    pub mean: Vec<f64>,
    #[serde(default)]
    pub scale: Vec<f64>,
    #[serde(default)]
    pub return_coef: Vec<f64>,
    #[serde(default)]
    pub direction_coef: Vec<f64>,
    #[serde(default)]
    pub return_intercept: f64,
    #[serde(default)]
    pub direction_intercept: f64,
    pub return_head: Option<TreeHead>,
    pub direction_head: Option<TreeHead>,
    pub calibration: Calibration,
    pub volatility_floor: f64,
    pub horizon: f64,
    pub interval_offsets: [f64; 2],
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub struct Forecast {
    pub expected_net_return: f64,
    pub probability_positive: f64,
    pub lower_return: f64,
    pub upper_return: f64,
}

pub fn sigmoid(x: f64) -> f64 {
    1.0 / (1.0 + (-x.clamp(-35.0, 35.0)).exp())
}

impl TreeHead {
    fn predict(&self, x: &[f64]) -> Result<f64> {
        let mut out = self.base;
        for tree in &self.trees {
            let mut index = 0;
            let mut leaf = None;
            for _ in 0..=tree.len() {
                let node = tree.get(index).ok_or_else(|| invalid("tree child index"))?;
                if node.is_leaf != 0 {
                    leaf = Some(node.value);
                    break;
                }
                let value = *x
                    .get(node.feature_idx)
                    .ok_or_else(|| invalid("tree feature index"))?;
                let left = if value.is_nan() {
                    node.missing_go_to_left != 0
                } else {
                    value <= node.num_threshold
                };
                index = if left { node.left } else { node.right };
            }
            out += leaf.ok_or_else(|| invalid("cyclic tree"))?;
        }
        Ok(out)
    }
}

impl NumericModel {
    pub fn predict(&self, raw: &[Option<f64>]) -> Result<Forecast> {
        if self.columns.is_empty()
            || self.columns.len() != self.imputation.len()
            || self.horizon <= 0.0
        {
            return Err(invalid("model metadata/shape"));
        }
        let mut x = Vec::with_capacity(self.columns.len() * 2);
        let mut missing = Vec::with_capacity(self.columns.len());
        for (i, col) in self.columns.iter().enumerate() {
            let value = raw
                .get(*col)
                .ok_or_else(|| invalid("input feature index"))?
                .filter(|v| v.is_finite());
            x.push(value.unwrap_or(self.imputation[i]));
            missing.push(if value.is_some() { 0.0 } else { 1.0 });
        }
        x.extend(missing);
        let (mean, probability) = match self.family.as_str() {
            "linear" => {
                if [
                    self.mean.len(),
                    self.scale.len(),
                    self.return_coef.len(),
                    self.direction_coef.len(),
                ]
                .iter()
                .any(|n| *n != x.len())
                {
                    return Err(invalid("linear model shape"));
                }
                let mut reg = self.return_intercept;
                let mut cls = self.direction_intercept;
                for (i, value) in x.iter().enumerate() {
                    if !self.scale[i].is_finite() || self.scale[i] <= 0.0 {
                        return Err(invalid("model scale"));
                    }
                    let z = (value - self.mean[i]) / self.scale[i];
                    reg += z * self.return_coef[i];
                    cls += z * self.direction_coef[i];
                }
                (reg, sigmoid(cls))
            }
            "histboost" => (
                self.return_head
                    .as_ref()
                    .ok_or_else(|| invalid("return tree missing"))?
                    .predict(&x)?,
                sigmoid(
                    self.direction_head
                        .as_ref()
                        .ok_or_else(|| invalid("direction tree missing"))?
                        .predict(&x)?,
                ),
            ),
            _ => return Err(invalid("unknown model family; no substitution permitted")),
        };
        let p = probability.clamp(1e-6, 1.0 - 1e-6);
        let calibrated = sigmoid(
            self.calibration.intercept + self.calibration.coefficient * (p / (1.0 - p)).ln(),
        );
        let volatility = raw
            .get(4)
            .copied()
            .flatten()
            .filter(|v| v.is_finite())
            .ok_or_else(|| invalid("forecast volatility feature unavailable"))?
            .max(self.volatility_floor)
            * self.horizon.sqrt();
        let forecast = Forecast {
            expected_net_return: mean,
            probability_positive: calibrated,
            lower_return: mean + self.interval_offsets[0] * volatility,
            upper_return: mean + self.interval_offsets[1] * volatility,
        };
        if [
            mean,
            calibrated,
            forecast.lower_return,
            forecast.upper_return,
        ]
        .iter()
        .any(|v| !v.is_finite())
        {
            return Err(invalid("non-finite forecast"));
        }
        Ok(forecast)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Network {
    pub weights: Vec<Vec<Vec<f64>>>,
    pub biases: Vec<Vec<f64>>,
}
impl Network {
    pub fn forward(&self, features: &[f64], logistic: bool) -> Result<Vec<f64>> {
        if self.weights.is_empty() || self.weights.len() != self.biases.len() {
            return Err(invalid("network shape"));
        }
        let mut out = features.to_vec();
        for (layer, (weights, bias)) in self.weights.iter().zip(&self.biases).enumerate() {
            if weights.len() != out.len() || weights.iter().any(|w| w.len() != bias.len()) {
                return Err(invalid("network weights shape"));
            }
            let mut next = bias.clone();
            for (input, row) in out.iter().zip(weights) {
                for (value, weight) in next.iter_mut().zip(row) {
                    *value += input * weight;
                }
            }
            if layer + 1 < self.weights.len() {
                for value in &mut next {
                    *value = value.max(0.0);
                }
            }
            out = next;
        }
        if logistic {
            for value in &mut out {
                *value = sigmoid(*value);
            }
        }
        if out.iter().any(|v| !v.is_finite()) {
            return Err(invalid("non-finite network output"));
        }
        Ok(out)
    }
}
