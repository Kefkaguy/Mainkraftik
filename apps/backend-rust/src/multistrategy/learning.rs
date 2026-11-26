use super::signals::{Signal, Strategy};
use crate::{
    Result, invalid,
    research_training::{Heads, Limits, fit_heads_regularized},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Outcome {
    pub signal: Signal,
    pub known_at: DateTime<Utc>,
    pub net_return: f64,
    pub exit_reason: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Selector {
    pub version: u32,
    pub feed: String,
    pub source_sha256: String,
    pub trained_through: DateTime<Utc>,
    pub calibrated_through: DateTime<Utc>,
    pub heads: Heads,
    pub platt: Heads,
    pub calibration_residual_lower: f64,
    pub counts: std::collections::BTreeMap<Strategy, usize>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Score {
    pub probability_net_success: f64,
    pub expected_net_return: f64,
    pub conservative_net_return: f64,
    pub accepted: bool,
}
pub fn source_hash() -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    for b in [
        include_bytes!("learning.rs").as_slice(),
        include_bytes!("signals.rs").as_slice(),
        include_bytes!("regime.rs").as_slice(),
        include_bytes!("execution.rs").as_slice(),
    ] {
        h.update(b);
    }
    hex::encode(h.finalize())
}
/// Fixed count-based calibration accommodates variable strategy activity.
/// Selection uses timestamps/counts only, never the values of held-out returns.
/// Overlapping training outcomes are purged at the first calibration issuance.
pub fn chronological_split(
    rows: &[Outcome],
    test_open: DateTime<Utc>,
) -> (Vec<Outcome>, Vec<Outcome>) {
    let mut available: Vec<_> = rows
        .iter()
        .filter(|r| r.known_at < test_open && r.signal.issued_at < test_open)
        .collect();
    available.sort_by_key(|r| r.signal.issued_at);
    let Some(first) = available.get(available.len().saturating_sub(30)) else {
        return (Vec::new(), Vec::new());
    };
    let cutoff = first.signal.issued_at;
    let train = available
        .iter()
        .filter(|r| r.known_at < cutoff)
        .map(|r| (*r).clone())
        .collect();
    let calibration = available
        .iter()
        .filter(|r| r.signal.issued_at >= cutoff)
        .map(|r| (*r).clone())
        .collect();
    (train, calibration)
}
impl Selector {
    pub fn fit(train: &[Outcome], cal: &[Outcome]) -> Result<Self> {
        if train.len() < 100 || cal.len() < 30 || train.len() > 20000 {
            return Err(invalid("Insufficient bounded quote-backed strategy labels"));
        }
        let end = train
            .iter()
            .map(|r| r.known_at)
            .max()
            .ok_or_else(|| invalid("No training labels"))?;
        let start = cal
            .iter()
            .map(|r| r.signal.issued_at)
            .min()
            .ok_or_else(|| invalid("No calibration labels"))?;
        let feed = train[0].signal.feed.clone();
        let mut seen = std::collections::BTreeSet::new();
        for r in train.iter().chain(cal) {
            if !seen.insert(&r.signal.id)
                || r.known_at <= r.signal.issued_at
                || r.signal.feed != feed
                || !r.net_return.is_finite()
            {
                return Err(invalid(
                    "Invalid, duplicate or mixed-feed strategy outcomes",
                ));
            }
        }
        if end >= start
            || !train.iter().any(|r| r.net_return > 0.)
            || !train.iter().any(|r| r.net_return <= 0.)
            || !cal.iter().any(|r| r.net_return > 0.)
            || !cal.iter().any(|r| r.net_return <= 0.)
        {
            return Err(invalid(
                "Purged chronological class/calibration boundaries required",
            ));
        }
        let x: Vec<_> = train.iter().map(|r| r.signal.x.clone()).collect();
        let y: Vec<_> = train.iter().map(|r| r.net_return).collect();
        let heads = fit_heads_regularized(&x, &y, Limits::default(), 10.)?;
        let cx: Vec<_> = cal.iter().map(|r| r.signal.x.clone()).collect();
        let cy: Vec<_> = cal.iter().map(|r| r.net_return).collect();
        let (raw, p) = heads.predict(&cx)?;
        let logits: Vec<_> = p
            .iter()
            .map(|p| {
                let p = p.clamp(1e-6, 1. - 1e-6);
                vec![(p / (1. - p)).ln()]
            })
            .collect();
        let platt = fit_heads_regularized(&logits, &cy, Limits::default(), 10.)?;
        let mut residuals: Vec<_> = raw.iter().zip(&cy).map(|(r, y)| y - r).collect();
        residuals.sort_by(f64::total_cmp);
        let mut counts = std::collections::BTreeMap::new();
        for r in train {
            *counts.entry(r.signal.strategy).or_default() += 1;
        }
        Ok(Self {
            version: 1,
            feed,
            source_sha256: source_hash(),
            trained_through: end,
            calibrated_through: cal
                .iter()
                .map(|r| r.known_at)
                .max()
                .ok_or_else(|| invalid("Calibration end"))?,
            heads,
            platt,
            calibration_residual_lower: residuals[residuals.len() / 10],
            counts,
        })
    }
    pub fn score(&self, s: &Signal) -> Result<Score> {
        if self.version != 1
            || self.source_sha256 != source_hash()
            || self.feed != s.feed
            || self.calibrated_through >= s.issued_at
            || self.counts.get(&s.strategy).copied().unwrap_or(0) < 20
        {
            return Err(invalid("Strategy selector not qualified for signal"));
        }
        let (r, p) = self.heads.predict(std::slice::from_ref(&s.x))?;
        let p = p[0].clamp(1e-6, 1. - 1e-6);
        let (_, p) = self.platt.predict(&[vec![(p / (1. - p)).ln()]])?;
        let low = r[0] + self.calibration_residual_lower;
        Ok(Score {
            probability_net_success: p[0],
            expected_net_return: r[0],
            conservative_net_return: low,
            accepted: r[0] > 0. && p[0] > 0.5 && low > 0.,
        })
    }
}
