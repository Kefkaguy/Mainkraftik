//! The existing scaled residual Ridge objective, not a replacement learner.
use crate::{Result, invalid};
use nalgebra::{DMatrix, DVector};
use serde::{Deserialize, Serialize};

pub const WIDTH: usize = 15;
pub const ALPHA: f64 = 100.0;
pub use crate::intraday::learning::{Adaptive, Validation as AdaptiveValidation};
pub const SCALES: [f64; WIDTH] = [
    1.0, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.005,
];

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Outcome {
    pub x: Vec<f64>,
    pub prior_return: f64,
    pub actual_return: f64,
}

pub fn fit(rows: &[Outcome]) -> Result<Vec<f64>> {
    let mut fit = ResidualFit::default();
    for row in rows {
        fit.push(row)?;
    }
    fit.finish()
}

/// Streaming normal equations preserve the all-history Ridge objective while
/// bounding training memory. This is not a rolling-window or strategy change.
pub struct ResidualFit {
    gram: DMatrix<f64>,
    rhs: DVector<f64>,
    observations: usize,
}
impl Default for ResidualFit {
    fn default() -> Self {
        Self {
            gram: DMatrix::identity(WIDTH, WIDTH) * ALPHA,
            rhs: DVector::zeros(WIDTH),
            observations: 0,
        }
    }
}
impl ResidualFit {
    pub fn push(&mut self, row: &Outcome) -> Result<()> {
        if row.x.len() != WIDTH
            || row
                .x
                .iter()
                .chain([&row.prior_return, &row.actual_return])
                .any(|v| !v.is_finite())
        {
            return Err(invalid("residual outcome shape/value"));
        }
        let z = DVector::from_iterator(WIDTH, row.x.iter().zip(SCALES).map(|(x, s)| x / s));
        self.gram += &z * z.transpose();
        self.rhs += z * (row.actual_return - row.prior_return);
        self.observations += 1;
        Ok(())
    }
    pub fn finish(self) -> Result<Vec<f64>> {
        if self.observations == 0 {
            return Err(invalid("no resolved outcomes"));
        }
        let beta = self
            .gram
            .lu()
            .solve(&self.rhs)
            .ok_or_else(|| invalid("singular residual solve"))?;
        let result: Vec<_> = beta.iter().zip(SCALES).map(|(b, s)| b / s).collect();
        if result.iter().any(|v| !v.is_finite()) {
            return Err(invalid("non-finite learner update"));
        }
        Ok(result)
    }
}
