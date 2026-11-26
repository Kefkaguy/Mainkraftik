//! Original NumPy contiguous-double reductions and paired date-block bootstrap.
use crate::{Result, invalid, numpy_rng::Pcg64};
use chrono::NaiveDate;
use serde_json::{Value, json};
use std::collections::BTreeMap;
pub(crate) fn sum(values: &[f64]) -> f64 {
    if values.len() < 8 {
        return values.iter().fold(-0.0, |s, v| s + v);
    }
    if values.len() > 128 {
        let split = (values.len() / 2) / 8 * 8;
        return sum(&values[..split]) + sum(&values[split..]);
    }
    let mut r: [f64; 8] = values[..8].try_into().expect("eight values");
    let mut i = 8;
    while i + 8 <= values.len() {
        for j in 0..8 {
            r[j] += values[i + j];
        }
        i += 8;
    }
    let mut result = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for value in &values[i..] {
        result += value;
    }
    result
}
pub(crate) fn mean(values: &[f64]) -> f64 {
    sum(values) / values.len() as f64
}
pub(crate) fn std(values: &[f64]) -> f64 {
    let m = mean(values);
    let squared: Vec<_> = values.iter().map(|v| (v - m) * (v - m)).collect();
    (sum(&squared) / values.len() as f64).sqrt()
}
pub(crate) fn quantile(sorted: &[f64], q: f64) -> f64 {
    let position = (sorted.len() - 1) as f64 * q;
    let index = position.floor() as usize;
    let t = position - index as f64;
    let a = sorted[index];
    let b = sorted[(index + 1).min(sorted.len() - 1)];
    if t >= 0.5 {
        b - (b - a) * (1.0 - t)
    } else {
        a + (b - a) * t
    }
}
pub fn paired_block_interval(
    dates: &[NaiveDate],
    differences: &[f64],
    samples: usize,
    block: usize,
    confidence: f64,
) -> Result<Option<Value>> {
    if dates.len() != differences.len() {
        return Err(invalid("Paired date/difference lengths differ"));
    }
    let mut grouped: BTreeMap<NaiveDate, Vec<f64>> = BTreeMap::new();
    for (&day, &v) in dates.iter().zip(differences) {
        if !v.is_finite() {
            return Err(invalid("Nonfinite bootstrap difference"));
        }
        grouped.entry(day).or_default().push(v);
    }
    let series: Vec<_> = grouped.values().map(|v| mean(v)).collect();
    let n = series.len();
    if n < 2 {
        return Ok(None);
    }
    if samples == 0
        || block == 0
        || !confidence.is_finite()
        || !(0.0..1.0).contains(&confidence)
        || samples.checked_mul(n).is_none_or(|v| v > 64_000_000)
    {
        return Err(invalid("Bootstrap resource/parameter boundary"));
    }
    let block = block.min(n);
    let count = n.div_ceil(block);
    let mut rng = Pcg64::new(20261112);
    let mut means = Vec::with_capacity(samples);
    let mut row = Vec::with_capacity(n);
    for _ in 0..samples {
        row.clear();
        for _ in 0..count {
            let start = rng.bounded_u64((n - block + 1) as u64)? as usize;
            let end = (n - row.len()).min(block);
            row.extend_from_slice(&series[start..start + end]);
        }
        means.push(mean(&row));
    }
    means.sort_by(f64::total_cmp);
    let tail = (1.0 - confidence) / 2.0;
    Ok(Some(
        json!({"mean_improvement":mean(&series),"lower":quantile(&means,tail),"upper":quantile(&means,1.0-tail),"confidence":confidence,"samples":samples,"block_sessions":block,"distinct_dates":n}),
    ))
}
