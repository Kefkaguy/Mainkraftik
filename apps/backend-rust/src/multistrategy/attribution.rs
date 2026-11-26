//! Reconciled PAPER fill attribution, independent of historical simulations.
//! No broker transport. Existing untagged orders are never retroactively assigned.
use super::signals::Strategy;
use crate::{Result, invalid};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct PaperFill {
    pub execution_id: String,
    pub order_id: String,
    pub account_id: String,
    pub symbol: String,
    pub at: DateTime<Utc>,
    pub buy: bool,
    pub shares: f64,
    pub price: f64,
    pub actual_fees: f64,
    pub reference_mid: Option<f64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PaperLedger {
    accounts: BTreeSet<String>,
    orders: BTreeMap<String, (String, Strategy, String)>,
    fills: BTreeMap<String, PaperFill>,
    #[serde(default)]
    marked_history: BTreeMap<String, Vec<(DateTime<Utc>, f64)>>,
}
#[derive(Default, Clone, Debug, Serialize)]
pub struct PaperPerformance {
    pub realized_net_pnl: f64,
    pub unrealized_net_pnl: Option<f64>,
    pub actual_fees: f64,
    pub observed_execution_cost: Option<f64>,
    pub open_shares: f64,
    pub completed_trades: usize,
    pub win_rate: Option<f64>,
    pub realized_maximum_drawdown_dollars: f64,
    pub marked_maximum_drawdown_dollars: Option<f64>,
    pub source: &'static str,
}
impl PaperLedger {
    pub fn checkpoint(&self, directory: &std::path::Path) -> Result<std::path::PathBuf> {
        crate::intraday::research::save(directory, "dataset", &serde_json::to_value(self)?)
    }
    pub fn restore(path: &std::path::Path, existing_accounts: [String; 2]) -> Result<Self> {
        let value: Self = crate::intraday::research::load(path)?;
        let expected = Self::new(existing_accounts)?;
        if value.accounts != expected.accounts {
            return Err(invalid("PAPER ledger restore account mismatch"));
        }
        Ok(value)
    }
    pub fn new(existing_accounts: [String; 2]) -> Result<Self> {
        let accounts: BTreeSet<_> = existing_accounts.into_iter().collect();
        if accounts.len() != 2 || accounts.contains("") {
            return Err(invalid("Exactly two existing PAPER identities required"));
        }
        Ok(Self {
            accounts,
            orders: BTreeMap::new(),
            fills: BTreeMap::new(),
            marked_history: BTreeMap::new(),
        })
    }
    pub fn track_order(
        &mut self,
        account: &str,
        order: &str,
        strategy: Strategy,
        symbol: &str,
    ) -> Result<()> {
        if !self.accounts.contains(account) || order.is_empty() || symbol.is_empty() {
            return Err(invalid("Unknown PAPER order identity"));
        }
        let value = (account.into(), strategy, symbol.into());
        if let Some(prior) = self.orders.get(order) {
            if prior != &value {
                return Err(invalid("PAPER strategy attribution is immutable"));
            }
        } else {
            self.orders.insert(order.into(), value);
        }
        Ok(())
    }
    pub fn reconcile(&mut self, fill: PaperFill) -> Result<bool> {
        if !self.accounts.contains(&fill.account_id)
            || fill.execution_id.is_empty()
            || !fill.shares.is_finite()
            || fill.shares <= 0.
            || !fill.price.is_finite()
            || fill.price <= 0.
            || !fill.actual_fees.is_finite()
            || fill.actual_fees < 0.
            || fill
                .reference_mid
                .is_some_and(|p| !p.is_finite() || p <= 0.)
        {
            return Err(invalid("Invalid reconciled PAPER execution"));
        }
        let (account, _, symbol) = self
            .orders
            .get(&fill.order_id)
            .ok_or_else(|| invalid("Untagged PAPER order is not new-strategy evidence"))?;
        if account != &fill.account_id || symbol != &fill.symbol {
            return Err(invalid("PAPER fill/order account isolation mismatch"));
        }
        let key = format!("{}:{}", fill.account_id, fill.execution_id);
        if let Some(old) = self.fills.get(&key) {
            if old != &fill {
                return Err(invalid("Conflicting immutable PAPER execution"));
            }
            return Ok(false);
        }
        self.fills.insert(key, fill);
        Ok(true)
    }
    /// Marks must come from a separately observed broker/account snapshot.
    /// Execution costs stay unknown when a contemporaneous mid was not recorded.
    pub fn performance(
        &self,
        account: &str,
        strategy: Strategy,
        marks: &BTreeMap<String, f64>,
    ) -> Result<PaperPerformance> {
        if !self.accounts.contains(account) {
            return Err(invalid("Unknown PAPER account"));
        }
        let mut rows: Vec<_> = self
            .fills
            .values()
            .filter(|f| {
                f.account_id == account
                    && self
                        .orders
                        .get(&f.order_id)
                        .is_some_and(|o| o.1 == strategy)
            })
            .collect();
        rows.sort_by_key(|f| (f.at, f.execution_id.clone()));
        let mut result = PaperPerformance {
            source: "actual_alpaca_paper_reconciled_fills",
            ..Default::default()
        };
        let mut held = BTreeMap::<String, (f64, f64, f64)>::new();
        let mut wins = 0;
        let mut peak = 0f64;
        let mut costs = 0.;
        let mut costs_known = true;
        for f in rows {
            result.actual_fees += f.actual_fees;
            if let Some(mid) = f.reference_mid {
                costs +=
                    (if f.buy { f.price - mid } else { mid - f.price }) * f.shares + f.actual_fees;
            } else {
                costs_known = false;
            }
            let p = held.entry(f.symbol.clone()).or_default();
            if f.buy {
                p.0 += f.shares;
                p.1 += f.shares * f.price + f.actual_fees;
            } else {
                if f.shares > p.0 + 1e-9 {
                    return Err(invalid(
                        "PAPER exit exceeds strategy-owned shares; attribution/reconciliation incomplete",
                    ));
                }
                let basis = p.1 * f.shares / p.0;
                let pnl = f.shares * f.price - f.actual_fees - basis;
                p.0 -= f.shares;
                p.1 -= basis;
                p.2 += pnl;
                result.realized_net_pnl += pnl;
                if p.0 <= 1e-9 {
                    result.completed_trades += 1;
                    if p.2 > 0. {
                        wins += 1;
                    }
                    p.2 = 0.;
                }
                peak = peak.max(result.realized_net_pnl);
                result.realized_maximum_drawdown_dollars = result
                    .realized_maximum_drawdown_dollars
                    .min(result.realized_net_pnl - peak);
            }
        }
        result.open_shares = held.values().map(|p| p.0).sum();
        let mut unrealized = Some(0.);
        for (symbol, p) in held {
            if p.0 > 1e-9 {
                if let Some(mark) = marks.get(&symbol).filter(|p| p.is_finite() && **p > 0.) {
                    unrealized = unrealized.map(|u| u + p.0 * mark - p.1);
                } else {
                    unrealized = None;
                }
            }
        }
        result.unrealized_net_pnl = unrealized;
        if result.completed_trades > 0 {
            result.win_rate = Some(wins as f64 / result.completed_trades as f64);
        }
        result.observed_execution_cost = if costs_known { Some(costs) } else { None };
        let key = format!("{account}:{strategy:?}");
        if let Some(history) = self.marked_history.get(&key).filter(|h| h.len() >= 2) {
            let mut peak = 0f64;
            let mut drawdown = 0f64;
            for (_, value) in history {
                peak = peak.max(*value);
                drawdown = drawdown.min(value - peak);
            }
            result.marked_maximum_drawdown_dollars = Some(drawdown);
        }
        Ok(result)
    }
    /// Append an observed broker-mark snapshot, never a fabricated historical mark.
    pub fn observe_marks(
        &mut self,
        account: &str,
        strategy: Strategy,
        at: DateTime<Utc>,
        marks: &BTreeMap<String, (f64, DateTime<Utc>)>,
    ) -> Result<()> {
        if marks.values().any(|(p, t)| {
            !p.is_finite() || *p <= 0. || *t > at || at - *t > chrono::Duration::seconds(15)
        }) {
            return Err(invalid("Fresh observed PAPER marks required"));
        }
        if self
            .fills
            .values()
            .any(|f| f.account_id == account && f.at > at)
        {
            return Err(invalid(
                "Cannot backdate PAPER equity snapshots after outcomes",
            ));
        }
        let values = marks.iter().map(|(s, (p, _))| (s.clone(), *p)).collect();
        let p = self.performance(account, strategy, &values)?;
        let net = p.realized_net_pnl
            + p.unrealized_net_pnl
                .ok_or_else(|| invalid("Missing owned PAPER position marks"))?;
        let key = format!("{account}:{strategy:?}");
        let history = self.marked_history.entry(key).or_default();
        if let Some((time, value)) = history.last() {
            if at < *time || at == *time && net != *value {
                return Err(invalid("PAPER marked history is immutable"));
            }
            if at == *time {
                return Ok(());
            }
        }
        history.push((at, net));
        Ok(())
    }
}
