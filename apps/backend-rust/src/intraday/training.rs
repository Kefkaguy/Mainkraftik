//! Bounded native hourly heads; histogram splits and calibration use training/
//! calibration partitions only. No inference or fitting calls Python/sklearn.
use super::{
    features::{FeatureRow, VERSION},
    models::{Boost, Calibration, Family, HourlyModel, TreeNode},
};
use crate::{
    Result, invalid,
    models::sigmoid,
    research_training::{Limits, fit_heads_regularized},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Sample {
    pub features: FeatureRow,
    pub target_at: DateTime<Utc>,
    pub known_at: DateTime<Utc>,
    pub actual_return: f64,
}
fn validate(rows: &[Sample], columns: &[usize]) -> Result<Vec<Vec<f64>>> {
    rows.iter()
        .map(|r| {
            if !r.actual_return.is_finite()
                || r.known_at < r.target_at
                || r.target_at - r.features.at != chrono::Duration::minutes(60)
                || columns.contains(&14) && !r.features.quote_available
            {
                return Err(invalid(
                    "Invalid matured hourly label or unavailable quote features",
                ));
            }
            columns
                .iter()
                .map(|&j| {
                    r.features
                        .x
                        .get(j)
                        .copied()
                        .filter(|v| v.is_finite())
                        .ok_or_else(|| invalid("Hourly feature shape/value"))
                })
                .collect()
        })
        .collect()
}
fn quantile(values: &[f64], q: f64) -> f64 {
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    let index = (values.len() - 1) as f64 * q;
    let left = index.floor() as usize;
    let right = index.ceil() as usize;
    values[left] + (values[right] - values[left]) * (index - left as f64)
}
struct Histograms {
    cuts: Vec<Vec<f64>>,
    bins: Vec<Vec<usize>>,
}
impl Histograms {
    fn new(x: &[Vec<f64>]) -> Self {
        let cuts: Vec<_> = (0..x[0].len())
            .map(|j| {
                let mut sorted: Vec<_> = x.iter().map(|r| r[j]).collect();
                sorted.sort_by(f64::total_cmp);
                let mut cuts: Vec<_> = (1..32)
                    .map(|k| sorted[k * (sorted.len() - 1) / 32])
                    .collect();
                cuts.dedup();
                cuts
            })
            .collect();
        let bins = x
            .iter()
            .map(|r| {
                r.iter()
                    .enumerate()
                    .map(|(j, v)| cuts[j].partition_point(|t| v > t))
                    .collect()
            })
            .collect();
        Self { cuts, bins }
    }
    fn tree(
        &self,
        indices: &[usize],
        g: &[f64],
        h: &[f64],
        alpha: f64,
        depth: usize,
        nodes: &mut Vec<TreeNode>,
    ) -> usize {
        let gradient = indices.iter().map(|&i| g[i]).sum::<f64>();
        let hessian = indices.iter().map(|&i| h[i]).sum::<f64>();
        let value = 0.05 * gradient / (hessian + alpha);
        let index = nodes.len();
        nodes.push(TreeNode {
            feature: 0,
            threshold: 0.,
            left: 0,
            right: 0,
            value,
            leaf: true,
        });
        if depth == 2 || indices.len() < 60 {
            return index;
        }
        let mut best_gain = 1e-12;
        let mut best = None;
        for (feature, cuts) in self.cuts.iter().enumerate() {
            let mut bg = vec![0.; cuts.len() + 1];
            let mut bh = vec![0.; cuts.len() + 1];
            let mut count = vec![0usize; cuts.len() + 1];
            for &i in indices {
                let bin = self.bins[i][feature];
                bg[bin] += g[i];
                bh[bin] += h[i];
                count[bin] += 1;
            }
            let (mut left_g, mut left_h, mut left_n) = (0., 0., 0usize);
            for (bin, &threshold) in cuts.iter().enumerate() {
                left_g += bg[bin];
                left_h += bh[bin];
                left_n += count[bin];
                if left_n < 30 || indices.len() - left_n < 30 {
                    continue;
                }
                let gain = left_g.powi(2) / (left_h + alpha)
                    + (gradient - left_g).powi(2) / (hessian - left_h + alpha)
                    - gradient.powi(2) / (hessian + alpha);
                if gain > best_gain {
                    best_gain = gain;
                    best = Some((feature, bin, threshold));
                }
            }
        }
        if let Some((feature, bin, threshold)) = best {
            let (left, right): (Vec<_>, Vec<_>) = indices
                .iter()
                .copied()
                .partition(|&i| self.bins[i][feature] <= bin);
            let l = self.tree(&left, g, h, alpha, depth + 1, nodes);
            let r = self.tree(&right, g, h, alpha, depth + 1, nodes);
            nodes[index] = TreeNode {
                feature,
                threshold,
                left: l,
                right: r,
                value,
                leaf: false,
            };
        }
        index
    }
}
fn boost(x: &[Vec<f64>], y: &[f64], alpha: f64, logistic: bool) -> Result<Boost> {
    let labels: Vec<_> = y
        .iter()
        .map(|v| {
            if logistic {
                if *v > 0. { 1. } else { 0. }
            } else {
                *v
            }
        })
        .collect();
    let average = labels.iter().sum::<f64>() / labels.len() as f64;
    let base = if logistic {
        let p = average.clamp(1e-6, 1. - 1e-6);
        (p / (1. - p)).ln()
    } else {
        average
    };
    let hist = Histograms::new(x);
    let mut raw = vec![base; y.len()];
    let mut trees = Vec::new();
    for _ in 0..32 {
        let probabilities: Vec<_> = raw
            .iter()
            .map(|&v| if logistic { sigmoid(v) } else { v })
            .collect();
        let gradient: Vec<_> = labels
            .iter()
            .zip(&probabilities)
            .map(|(y, p)| y - p)
            .collect();
        let hessian: Vec<_> = probabilities
            .iter()
            .map(|p| {
                if logistic {
                    (p * (1. - p)).max(1e-5)
                } else {
                    1.
                }
            })
            .collect();
        let mut nodes = Vec::new();
        hist.tree(
            &(0..y.len()).collect::<Vec<_>>(),
            &gradient,
            &hessian,
            alpha,
            0,
            &mut nodes,
        );
        let temporary = Boost {
            base: 0.,
            trees: vec![nodes.clone()],
            logistic: false,
        };
        for (i, row) in x.iter().enumerate() {
            raw[i] += temporary.predict(row)?;
        }
        trees.push(nodes);
    }
    Ok(Boost {
        base,
        trees,
        logistic,
    })
}
pub fn fit(
    train: &[Sample],
    calibration: &[Sample],
    family: Family,
    columns: Vec<usize>,
    alpha: f64,
) -> Result<HourlyModel> {
    if train.len() < 100
        || calibration.len() < 30
        || train.len() > 20000
        || calibration.len() > 20000
        || columns.is_empty()
        || columns.len() > 32
        || !alpha.is_finite()
        || alpha <= 0.
        || columns.iter().any(|j| *j >= super::features::NAMES.len())
    {
        return Err(invalid(
            "Hourly training/calibration sample or resource requirement",
        ));
    }
    let feed = train[0].features.feed.clone();
    if !matches!(feed.as_str(), "sip" | "iex")
        || train
            .iter()
            .chain(calibration)
            .any(|r| r.features.feed != feed)
    {
        return Err(invalid("Hourly training and calibration feeds must match"));
    }
    let trained = train
        .iter()
        .map(|r| r.known_at)
        .max()
        .ok_or_else(|| invalid("Training missing"))?;
    let cal_start = calibration
        .iter()
        .map(|r| r.features.at)
        .min()
        .ok_or_else(|| invalid("Calibration missing"))?;
    if trained >= cal_start {
        return Err(invalid("Hourly labels overlap calibration; purge required"));
    }
    let cal_end = calibration
        .iter()
        .map(|r| r.known_at)
        .max()
        .ok_or_else(|| invalid("Calibration missing"))?;
    let x = validate(train, &columns)?;
    let cx = validate(calibration, &columns)?;
    let y: Vec<_> = train.iter().map(|r| r.actual_return).collect();
    let pos: Vec<_> = y.iter().filter(|v| **v > 0.).copied().collect();
    let neg: Vec<_> = y.iter().filter(|v| **v <= 0.).copied().collect();
    if pos.is_empty() || neg.is_empty() {
        return Err(invalid("Both hourly directional classes required"));
    }
    let mut model = HourlyModel {
        feed,
        feature_sha256: super::features::implementation_hash().into(),
        version: 1,
        feature_version: VERSION.into(),
        horizon_minutes: 60,
        family,
        columns,
        trained_through: trained,
        calibrated_through: cal_end,
        linear: None,
        return_boost: None,
        direction_boost: None,
        positive_mean: pos.iter().sum::<f64>() / pos.len() as f64,
        negative_mean: neg.iter().sum::<f64>() / neg.len() as f64,
        calibration: Calibration {
            return_intercept: 0.,
            return_slope: 1.,
            probability_intercept: 0.,
            probability_slope: 1.,
            residual_low: 0.,
            residual_high: 0.,
            sigma: 1.,
            samples: 0,
        },
    };
    match family {
        Family::Ridge | Family::Logistic => {
            model.linear = Some(fit_heads_regularized(&x, &y, Limits::default(), alpha)?)
        }
        Family::HistogramGradientBoosting => {
            model.return_boost = Some(boost(&x, &y, alpha, false)?);
            model.direction_boost = Some(boost(&x, &y, alpha, true)?);
        }
    }
    let raw: Vec<_> = calibration
        .iter()
        .map(|r| model.raw(&r.features.x))
        .collect::<Result<_>>()?;
    let target: Vec<_> = calibration.iter().map(|r| r.actual_return).collect();
    let n = raw.len() as f64;
    let mx = raw.iter().map(|r| r.0).sum::<f64>() / n;
    let my = target.iter().sum::<f64>() / n;
    let variance = raw.iter().map(|r| (r.0 - mx).powi(2)).sum::<f64>();
    let covariance = raw
        .iter()
        .zip(&target)
        .map(|(r, y)| (r.0 - mx) * (y - my))
        .sum::<f64>();
    let slope = if variance > 1e-16 {
        (covariance / variance).clamp(0., 2.)
    } else {
        0.
    };
    let intercept = my - slope * mx;
    let logits: Vec<_> = raw
        .iter()
        .map(|r| {
            let p = r.1.clamp(1e-6, 1. - 1e-6);
            vec![(p / (1. - p)).ln()]
        })
        .collect();
    let platt = fit_heads_regularized(&logits, &target, Limits::default(), 10.)?;
    let probability_slope = platt.logistic[1] / platt.scale[0];
    let probability_intercept = platt.logistic[0] - probability_slope * platt.mean[0];
    let residuals: Vec<_> = raw
        .iter()
        .zip(&target)
        .map(|(r, y)| y - (intercept + slope * r.0))
        .collect();
    let mean = residuals.iter().sum::<f64>() / n;
    let sigma = (residuals.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n)
        .sqrt()
        .max(1e-6);
    model.calibration = Calibration {
        return_intercept: intercept,
        return_slope: slope,
        probability_intercept,
        probability_slope,
        residual_low: quantile(&residuals, 0.05),
        residual_high: quantile(&residuals, 0.95),
        sigma,
        samples: cx.len(),
    };
    Ok(model)
}
