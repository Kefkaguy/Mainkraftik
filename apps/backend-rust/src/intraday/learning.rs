//! Incremental rolling/recency residual normal equations. No automatic adoption.
use crate::{Result, invalid};
use chrono::{DateTime, Utc};
use nalgebra::{DMatrix, DVector};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, VecDeque};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Matured {
    pub prediction_id: String,
    pub issued_at: DateTime<Utc>,
    pub target_at: DateTime<Utc>,
    pub known_at: DateTime<Utc>,
    pub x: Vec<f64>,
    pub prior_return: f64,
    pub actual_return: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Adaptive {
    width: usize,
    alpha: f64,
    window: usize,
    half_life_minutes: Option<f64>,
    gram: Vec<f64>,
    rhs: Vec<f64>,
    rows: VecDeque<Matured>,
    seen: BTreeSet<String>,
    at: Option<DateTime<Utc>>,
}
impl Adaptive {
    pub fn new(
        width: usize,
        alpha: f64,
        window: usize,
        half_life_minutes: Option<f64>,
    ) -> Result<Self> {
        if width == 0
            || width > 64
            || !alpha.is_finite()
            || alpha <= 0.
            || window == 0
            || window > 20000
            || half_life_minutes.is_some_and(|h| !h.is_finite() || h <= 0.)
        {
            return Err(invalid("Adaptive learner resource/regularization boundary"));
        }
        Ok(Self {
            width,
            alpha,
            window,
            half_life_minutes,
            gram: vec![0.; width * width],
            rhs: vec![0.; width],
            rows: VecDeque::new(),
            seen: BTreeSet::new(),
            at: None,
        })
    }
    fn weight(&self, minutes: f64) -> f64 {
        self.half_life_minutes
            .map_or(1., |h| (-std::f64::consts::LN_2 * minutes / h).exp())
    }
    pub fn push(&mut self, row: Matured, asof: DateTime<Utc>) -> Result<bool> {
        if row.prediction_id.is_empty()
            || row.x.len() != self.width
            || row
                .x
                .iter()
                .chain([&row.prior_return, &row.actual_return])
                .any(|v| !v.is_finite())
            || row.issued_at >= row.target_at
            || row.known_at < row.target_at
            || row.known_at > asof
            || self.at.is_some_and(|t| t > asof)
            || row.known_at - row.target_at > chrono::Duration::minutes(5)
        {
            return Err(invalid(
                "Only completed valid timestamped outcomes may train",
            ));
        }
        if self.seen.contains(&row.prediction_id) {
            return Ok(false);
        }
        if self.seen.len() >= 20000 {
            return Err(invalid(
                "Adaptive identity ledger full; checkpoint required",
            ));
        }
        let decay = self
            .at
            .map_or(1., |t| self.weight((asof - t).num_seconds() as f64 / 60.));
        let weight = self.weight((asof - row.known_at).num_seconds() as f64 / 60.);
        let mut gram: Vec<_> = self.gram.iter().map(|g| g * decay).collect();
        let mut rhs: Vec<_> = self.rhs.iter().map(|r| r * decay).collect();
        for i in 0..self.width {
            rhs[i] += weight * row.x[i] * (row.actual_return - row.prior_return);
            for j in 0..self.width {
                gram[i * self.width + j] += weight * row.x[i] * row.x[j];
            }
        }
        if self.rows.len() == self.window {
            let old = self
                .rows
                .front()
                .ok_or_else(|| invalid("Adaptive window state"))?;
            let weight = self.weight((asof - old.known_at).num_seconds() as f64 / 60.);
            for i in 0..self.width {
                rhs[i] -= weight * old.x[i] * (old.actual_return - old.prior_return);
                for j in 0..self.width {
                    gram[i * self.width + j] -= weight * old.x[i] * old.x[j];
                }
            }
        }
        if gram.iter().chain(&rhs).any(|v| !v.is_finite()) {
            return Err(invalid("Unstable adaptive update rejected"));
        }
        self.gram = gram;
        self.rhs = rhs;
        self.at = Some(asof);
        if self.rows.len() == self.window {
            self.rows.pop_front();
        }
        self.seen.insert(row.prediction_id.clone());
        self.rows.push_back(row);
        Ok(true)
    }
    pub fn predict_correction(&self, x: &[f64], maximum_abs: f64) -> Result<f64> {
        if x.len() != self.width
            || x.iter().any(|v| !v.is_finite())
            || !maximum_abs.is_finite()
            || maximum_abs <= 0.
        {
            return Err(invalid("Invalid adaptive inference"));
        }
        if self.rows.len() < 50 {
            return Ok(0.);
        }
        let mut gram = DMatrix::from_row_slice(self.width, self.width, &self.gram);
        for j in 0..self.width {
            gram[(j, j)] += self.alpha;
        }
        let beta = gram
            .cholesky()
            .ok_or_else(|| invalid("Unstable adaptive Gram matrix; prior retained"))?
            .solve(&DVector::from_column_slice(&self.rhs));
        let correction = beta.iter().zip(x).map(|(b, x)| b * x).sum::<f64>();
        if !correction.is_finite() || correction.abs() > maximum_abs {
            return Err(invalid("Unstable adaptive correction; prior retained"));
        }
        Ok(correction)
    }
    pub fn observations(&self) -> usize {
        self.rows.len()
    }
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Validation {
    pub samples: usize,
    pub baseline_mae: f64,
    pub candidate_mae: f64,
    pub baseline_brier: f64,
    pub candidate_brier: f64,
    pub baseline_net_pnl: f64,
    pub candidate_net_pnl: f64,
    pub paired_net_lower_bound: f64,
}
impl Validation {
    pub fn acceptable(&self) -> bool {
        let values = [
            self.baseline_mae,
            self.candidate_mae,
            self.baseline_brier,
            self.candidate_brier,
            self.baseline_net_pnl,
            self.candidate_net_pnl,
            self.paired_net_lower_bound,
        ];
        self.samples >= 100
            && values.iter().all(|v| v.is_finite())
            && self.candidate_mae < self.baseline_mae
            && self.candidate_brier <= self.baseline_brier
            && self.candidate_net_pnl > self.baseline_net_pnl
            && self.paired_net_lower_bound > 0.
    }
}
