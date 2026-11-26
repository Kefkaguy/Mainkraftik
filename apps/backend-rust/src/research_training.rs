//! Original research.py Ridge + Newton logistic objective. Offline training only.
//! Frozen model bundles are never read, replaced or promoted by this module.
use crate::{Result, invalid, models::sigmoid};
use nalgebra::{DMatrix, DVector};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::Semaphore;

#[derive(Clone, Copy)]
pub struct Limits {
    pub rows: usize,
    pub columns: usize,
    pub cells: usize,
    pub projected_work: u64,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            rows: 20_000,
            columns: 256,
            cells: 8_000_000,
            projected_work: 2_000_000_000,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Heads {
    pub mean: Vec<f64>,
    pub scale: Vec<f64>,
    pub ridge: Vec<f64>,
    pub logistic: Vec<f64>,
}
impl Heads {
    pub fn predict(&self, x: &[Vec<f64>]) -> Result<(Vec<f64>, Vec<f64>)> {
        let width = self.mean.len();
        if width == 0
            || self.scale.len() != width
            || self.ridge.len() != width + 1
            || self.logistic.len() != width + 1
            || self.scale.iter().any(|v| !v.is_finite() || *v <= 0.)
            || self
                .mean
                .iter()
                .chain(&self.ridge)
                .chain(&self.logistic)
                .any(|v| !v.is_finite())
        {
            return Err(invalid("Invalid fitted research heads"));
        }
        let mut returns = Vec::with_capacity(x.len());
        let mut probabilities = Vec::with_capacity(x.len());
        for row in x {
            if row.len() != width || row.iter().any(|v| !v.is_finite()) {
                return Err(invalid("Invalid research prediction shape/value"));
            }
            let mut value = self.ridge[0];
            let mut direction = self.logistic[0];
            for (j, v) in row.iter().enumerate() {
                let z = (v - self.mean[j]) / self.scale[j];
                value += z * self.ridge[j + 1];
                direction += z * self.logistic[j + 1];
            }
            if !value.is_finite() || !direction.is_finite() {
                return Err(invalid("Nonfinite research prediction"));
            }
            returns.push(value);
            probabilities.push(sigmoid(direction));
        }
        Ok((returns, probabilities))
    }
}
pub fn fit_heads(x: &[Vec<f64>], y: &[f64], limits: Limits) -> Result<Heads> {
    fit_heads_regularized(x, y, limits, 1.)
}
/// Separate experiments may vary shrinkage; the original wrapper remains alpha=1.
pub fn fit_heads_regularized(
    x: &[Vec<f64>],
    y: &[f64],
    limits: Limits,
    alpha: f64,
) -> Result<Heads> {
    if !alpha.is_finite() || alpha <= 0. {
        return Err(invalid("Positive finite regularization required"));
    }
    let n = x.len();
    let width = x.first().map_or(0, Vec::len);
    let p = width
        .checked_add(1)
        .ok_or_else(|| invalid("Training resource boundary"))?;
    let cells = n
        .checked_mul(p)
        .ok_or_else(|| invalid("Training resource boundary"))?;
    let work = (n as u64)
        .checked_mul(p as u64)
        .and_then(|v| v.checked_mul(p as u64))
        .and_then(|v| v.checked_mul(100));
    if n == 0
        || width == 0
        || n > limits.rows
        || width > limits.columns
        || cells > limits.cells
        || work.is_none_or(|v| v > limits.projected_work)
    {
        return Err(invalid("Training resource/shape boundary"));
    }
    if y.len() != n
        || y.iter().any(|v| !v.is_finite())
        || x.iter()
            .any(|r| r.len() != width || r.iter().any(|v| !v.is_finite()))
    {
        return Err(invalid("Nonfinite or inconsistent training data"));
    }
    if !y.iter().any(|v| *v > 0.) || !y.iter().any(|v| *v <= 0.) {
        return Err(invalid("Directional training labels require both classes"));
    }
    // C-contiguous NumPy axis=0 reductions accumulate each strided column in row order.
    let mut mean = vec![0.; width];
    for row in x {
        for (j, value) in row.iter().enumerate() {
            mean[j] += value;
        }
    }
    for value in &mut mean {
        *value /= n as f64;
    }
    let mut scale = vec![0.; width];
    for row in x {
        for (j, value) in row.iter().enumerate() {
            scale[j] += (value - mean[j]).powi(2);
        }
    }
    for value in &mut scale {
        *value = (*value / n as f64).sqrt();
        if *value < 1e-12 {
            *value = 1.;
        }
    }
    if mean.iter().chain(&scale).any(|v| !v.is_finite()) {
        return Err(invalid("Nonfinite training normalization"));
    }
    let z = DMatrix::from_fn(n, p, |i, j| {
        if j == 0 {
            1.
        } else {
            (x[i][j - 1] - mean[j - 1]) / scale[j - 1]
        }
    });
    let mut penalty = DMatrix::<f64>::identity(p, p) * alpha;
    penalty[(0, 0)] = 0.;
    let ridge = (z.transpose() * &z + &penalty)
        .lu()
        .solve(&(z.transpose() * DVector::from_column_slice(y)))
        .ok_or_else(|| invalid("Singular research Ridge fit"))?;
    let labels = DVector::from_iterator(n, y.iter().map(|v| if *v > 0. { 1. } else { 0. }));
    let mut coef = DVector::zeros(p);
    for _ in 0..100 {
        let probabilities = (&z * &coef).map(sigmoid);
        let weights = probabilities.map(|v| (v * (1. - v)).max(1e-8));
        let gradient = z.transpose() * (&probabilities - &labels) + &penalty * &coef;
        let weighted = DMatrix::from_fn(p, n, |j, i| z[(i, j)] * weights[i]);
        let mut hessian = weighted * &z + &penalty;
        for j in 0..p {
            hessian[(j, j)] += 1e-9;
        }
        let step = hessian
            .lu()
            .solve(&gradient)
            .ok_or_else(|| invalid("Singular research logistic fit"))?;
        if step.iter().any(|v| !v.is_finite()) {
            return Err(invalid("Nonfinite research logistic fit"));
        }
        coef -= &step;
        if step.iter().fold(0_f64, |v, a| v.max(a.abs())) < 1e-8 {
            break;
        }
    }
    let result = Heads {
        mean,
        scale,
        ridge: ridge.as_slice().to_vec(),
        logistic: coef.as_slice().to_vec(),
    };
    if result
        .ridge
        .iter()
        .chain(&result.logistic)
        .any(|v| !v.is_finite())
    {
        return Err(invalid("Nonfinite fitted research heads"));
    }
    Ok(result)
}
pub struct Trainer {
    limits: Limits,
    slots: Arc<Semaphore>,
}
impl Trainer {
    pub fn new(limits: Limits) -> Arc<Self> {
        Arc::new(Self {
            limits,
            slots: Arc::new(Semaphore::new(1)),
        })
    }
    /// Caller cancellation retains ownership of the CPU permit until the fit finishes.
    pub async fn fit(self: &Arc<Self>, x: Vec<Vec<f64>>, y: Vec<f64>) -> Result<Heads> {
        let limits = self.limits;
        self.compute(move || fit_heads(&x, &y, limits)).await
    }
    pub(crate) async fn compute<T: Send + 'static>(
        self: &Arc<Self>,
        work: impl FnOnce() -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let slot = self
            .slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| invalid("Research trainer busy"))?;
        tokio::task::spawn_blocking(move || {
            let _slot = slot;
            work()
        })
        .await
        .map_err(|_| invalid("Research training worker failed"))?
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn cancelled_training_request_retains_single_cpu_owner_until_work_finishes() {
        let worker = Trainer::new(Limits::default());
        let clone = worker.clone();
        let (entered, started) = tokio::sync::oneshot::channel();
        let (release, blocked) = std::sync::mpsc::channel();
        let request = tokio::spawn(async move {
            clone
                .compute(move || {
                    let _ = entered.send(());
                    blocked.recv().expect("release");
                    fit_heads(&[vec![1.], vec![2.]], &[-1., 1.], Limits::default())
                })
                .await
        });
        started.await.expect("CPU work started");
        request.abort();
        assert!(request.await.unwrap_err().is_cancelled());
        assert!(
            worker
                .fit(vec![vec![1.], vec![2.]], vec![-1., 1.])
                .await
                .is_err()
        );
        release.send(()).expect("release CPU");
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while worker.slots.available_permits() == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("permit released");
        worker
            .fit(vec![vec![1.], vec![2.]], vec![-1., 1.])
            .await
            .expect("subsequent training succeeds");
    }
}
