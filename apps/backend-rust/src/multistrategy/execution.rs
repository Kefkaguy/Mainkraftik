//! Strategy-specific, quote-backed outcomes and portfolio attribution.
use super::{
    learning::{Outcome, Selector},
    quotes::Observation,
    signals::{STRATEGIES, Signal, Strategy},
};
use crate::{Result, intraday::allocation::Costs, invalid};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Performance {
    pub net_pnl: f64,
    pub realized_pnl: f64,
    pub unrealized_pnl: f64,
    pub execution_costs: f64,
    pub closed_trades: usize,
    pub open_positions: usize,
    pub win_rate: Option<f64>,
    pub maximum_drawdown: f64,
    pub turnover: f64,
    pub daily_pnl: BTreeMap<String, f64>,
    pub partial_fills: usize,
    pub missing_quote_events: usize,
    pub rejected_signals: usize,
    pub profit_factor: Option<f64>,
    pub sharpe: Option<f64>,
    pub net_weekly_average: Option<f64>,
}
impl Default for Performance {
    fn default() -> Self {
        Self {
            net_pnl: 0.,
            realized_pnl: 0.,
            unrealized_pnl: 0.,
            execution_costs: 0.,
            closed_trades: 0,
            open_positions: 0,
            win_rate: None,
            maximum_drawdown: 0.,
            turnover: 0.,
            daily_pnl: BTreeMap::new(),
            partial_fills: 0,
            missing_quote_events: 0,
            rejected_signals: 0,
            profit_factor: None,
            sharpe: None,
            net_weekly_average: None,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Fill {
    pub id: String,
    pub strategy: Strategy,
    pub symbol: String,
    pub signal_id: String,
    pub at: DateTime<Utc>,
    pub shares: f64,
    pub price: f64,
    pub fees: f64,
    pub side: String,
    pub origin: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Held {
    pub signal: Signal,
    pub shares: f64,
    pub entry: f64,
    pub remaining_entry_fees: f64,
    pub realized: f64,
    pub last_bid: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Book {
    pub version: u32,
    pub initial_capital: f64,
    pub cash: f64,
    pub positions: BTreeMap<String, Held>,
    pub metrics: BTreeMap<Strategy, Performance>,
    pub fills: Vec<Fill>,
    pub completed: Vec<Outcome>,
    pub processed: BTreeSet<String>,
    pub last_event: Option<DateTime<Utc>>,
    curves: BTreeMap<Strategy, Vec<f64>>,
    closed: BTreeMap<Strategy, Vec<f64>>,
    decision_time: Option<DateTime<Utc>>,
    decision_equity: f64,
    reserved_cash: f64,
    reserved_gross: f64,
    entry_data_ready: bool,
}
#[derive(Clone, Copy)]
pub enum Filter<'a> {
    RulesOnly,
    MachineLearning(&'a Selector),
    Unavailable,
}
fn before(row: &Observation) -> Option<crate::features::Quote> {
    let mut q = row.before.as_ref()?.quote.clone();
    q.acquired_at = q.source_time + Duration::seconds(1);
    q.validate(row.at).ok()?;
    Some(q)
}
fn after(row: &Observation) -> Option<crate::features::Quote> {
    let mut q = row.after.as_ref()?.quote.clone();
    q.acquired_at = q.source_time + Duration::seconds(1);
    if q.source_time < row.at + Duration::seconds(1)
        || q.acquired_at > row.at + Duration::seconds(15)
    {
        return None;
    }
    q.validate(q.acquired_at).ok()?;
    Some(q)
}
impl Book {
    pub fn new(capital: f64) -> Result<Self> {
        if !capital.is_finite() || capital <= 0. {
            return Err(invalid("Invalid strategy capital"));
        }
        Ok(Self {
            version: 1,
            initial_capital: capital,
            cash: capital,
            positions: BTreeMap::new(),
            metrics: STRATEGIES
                .into_iter()
                .map(|s| (s, Performance::default()))
                .collect(),
            fills: Vec::new(),
            completed: Vec::new(),
            processed: BTreeSet::new(),
            last_event: None,
            curves: BTreeMap::new(),
            closed: BTreeMap::new(),
            decision_time: None,
            decision_equity: capital,
            reserved_cash: capital,
            reserved_gross: 0.,
            entry_data_ready: true,
        })
    }
    pub fn equity(&self) -> f64 {
        self.cash
            + self
                .positions
                .values()
                .map(|p| p.shares * p.last_bid)
                .sum::<f64>()
    }
    /// Freeze the whole decision batch before consuming any future fill quote.
    pub fn prepare(&mut self, rows: &[Observation]) -> Result<()> {
        let at = rows
            .first()
            .ok_or_else(|| invalid("Empty market batch"))?
            .at;
        if rows.iter().any(|r| r.at != at) || self.last_event.is_some_and(|t| t > at) {
            return Err(invalid("Nonchronological market batch"));
        }
        if self.decision_time == Some(at) {
            return Ok(());
        }
        self.entry_data_ready = true;
        for (symbol, p) in &mut self.positions {
            if let Some(q) = rows.iter().find(|r| &r.symbol == symbol).and_then(before) {
                p.last_bid = q.bid;
            } else {
                self.entry_data_ready = false;
            }
        }
        self.decision_time = Some(at);
        self.decision_equity = self.equity();
        self.reserved_cash = self.cash;
        self.reserved_gross = self.positions.values().map(|p| p.shares * p.last_bid).sum();
        Ok(())
    }
    fn enter(
        &mut self,
        s: &Signal,
        row: &Observation,
        q: Option<&crate::features::Quote>,
    ) -> Result<()> {
        let costs = Costs::default();
        let Some(q) = q else {
            self.metrics
                .get_mut(&s.strategy)
                .ok_or_else(|| invalid("Strategy metrics"))?
                .missing_quote_events += 1;
            return Ok(());
        };
        let budget = (0.2 * self.decision_equity)
            .min((0.4 * self.decision_equity - self.reserved_gross).max(0.))
            .min(self.reserved_cash);
        let planned_price =
            q.ask * (1. + (costs.slippage_bps + costs.execution_uncertainty_bps) / 10000.);
        let edge = s.target / planned_price - 1. - 2. * costs.one_way(q)?;
        if edge <= 0.
            || q.bid <= s.stop
            || q.bid <= s.invalidation
            || planned_price >= s.target
            || budget < 1.
        {
            self.metrics
                .get_mut(&s.strategy)
                .ok_or_else(|| invalid("Strategy metrics"))?
                .rejected_signals += 1;
            return Ok(());
        }
        // Reserve from decision-time inputs even when the future execution quote
        // is missing, invalidates the entry, or allows only a partial fill.
        self.reserved_cash -= budget;
        self.reserved_gross += budget;
        let (Some(eq), Some(sample)) = (after(row), row.after.as_ref()) else {
            self.metrics
                .get_mut(&s.strategy)
                .ok_or_else(|| invalid("Strategy metrics"))?
                .missing_quote_events += 1;
            return Ok(());
        };
        let price = eq.ask * (1. + (costs.slippage_bps + costs.execution_uncertainty_bps) / 10000.);
        let qty = (budget / (planned_price * (1. + costs.fee_bps / 10000.)))
            .min(budget / (price * (1. + costs.fee_bps / 10000.)))
            .min(self.cash / (price * (1. + costs.fee_bps / 10000.)))
            .min(sample.ask_shares * 0.01 * 0.5);
        if eq.bid <= s.stop || eq.bid <= s.invalidation || price >= s.target || qty * price < 1. {
            self.metrics
                .get_mut(&s.strategy)
                .ok_or_else(|| invalid("Strategy metrics"))?
                .rejected_signals += 1;
            return Ok(());
        }
        let fees = costs.order_fee(qty, qty * price, false);
        self.cash -= qty * price + fees;
        let m = self
            .metrics
            .get_mut(&s.strategy)
            .ok_or_else(|| invalid("Strategy metrics"))?;
        m.execution_costs += fees + qty * (price - (eq.ask + eq.bid) / 2.);
        m.turnover += qty * price / self.initial_capital;
        if qty * price + 0.01 < budget {
            m.partial_fills += 1;
        }
        self.positions.insert(
            row.symbol.clone(),
            Held {
                signal: s.clone(),
                shares: qty,
                entry: price,
                remaining_entry_fees: fees,
                realized: 0.,
                last_bid: eq.bid,
            },
        );
        let key = crate::canonical::digest(&serde_json::json!([row.plan_id, row.at, row.symbol]))?;
        self.fills.push(Fill {
            id: format!("{key}:entry"),
            strategy: s.strategy,
            symbol: row.symbol.clone(),
            signal_id: s.id.clone(),
            at: eq.acquired_at,
            shares: qty,
            price,
            fees,
            side: "buy".into(),
            origin: "historical_simulation".into(),
        });
        Ok(())
    }
    /// Monitoring/risk exits run even with no candidate or unavailable ML model.
    /// An observation is once-only across a serialized restart. Partial remainders
    /// stay owned and are checked again; no unknown quote is turned into a fill.
    pub fn monitor(
        &mut self,
        row: &Observation,
        signals: &[Signal],
        filter: Filter<'_>,
    ) -> Result<()> {
        if self.version != 1 || self.last_event.is_some_and(|t| t > row.at) {
            return Err(invalid("Nonchronological strategy recovery"));
        }
        let key = crate::canonical::digest(&serde_json::json!([row.plan_id, row.at, row.symbol]))?;
        if self.processed.contains(&key) {
            return Ok(());
        }
        self.prepare(std::slice::from_ref(row))?;
        let owned_at_start = self.positions.contains_key(&row.symbol);
        self.processed.insert(key.clone());
        self.last_event = Some(row.at);
        let q = before(row);
        let execution = after(row);
        let costs = Costs::default();
        if let Some(p) = self.positions.get_mut(&row.symbol) {
            if let Some(q) = &q {
                p.last_bid = q.bid;
            } else {
                self.metrics
                    .get_mut(&p.signal.strategy)
                    .ok_or_else(|| invalid("Strategy metrics"))?
                    .missing_quote_events += 1;
            }
            let reason = q.as_ref().and_then(|q| p.signal.exit_reason(row.at, q.bid));
            if let (Some(reason), Some(eq), Some(sample)) =
                (reason, execution.as_ref(), row.after.as_ref())
            {
                let qty = p.shares.min(sample.bid_shares * 0.01 * 0.5);
                if qty > 0. {
                    let price = eq.bid
                        * (1. - (costs.slippage_bps + costs.execution_uncertainty_bps) / 10000.);
                    let fees = costs.order_fee(qty, qty * price, true);
                    let entryfees = p.remaining_entry_fees * qty / p.shares;
                    let pnl = (price - p.entry) * qty - fees - entryfees;
                    p.realized += pnl;
                    p.remaining_entry_fees -= entryfees;
                    p.shares -= qty;
                    self.cash += qty * price - fees;
                    let m = self
                        .metrics
                        .get_mut(&p.signal.strategy)
                        .ok_or_else(|| invalid("Strategy metrics"))?;
                    m.realized_pnl += pnl;
                    m.turnover += qty * price / self.initial_capital;
                    m.execution_costs += fees + qty * ((eq.ask + eq.bid) / 2. - price);
                    if p.shares > 1e-9 {
                        m.partial_fills += 1;
                    }
                    self.fills.push(Fill {
                        id: format!("{key}:exit"),
                        strategy: p.signal.strategy,
                        symbol: row.symbol.clone(),
                        signal_id: p.signal.id.clone(),
                        at: eq.acquired_at,
                        shares: qty,
                        price,
                        fees,
                        side: "sell".into(),
                        origin: "historical_simulation".into(),
                    });
                    if p.shares <= 1e-9 {
                        self.closed
                            .entry(p.signal.strategy)
                            .or_default()
                            .push(p.realized);
                        let entry_notional = self
                            .fills
                            .iter()
                            .filter(|f| f.signal_id == p.signal.id && f.side == "buy")
                            .map(|f| f.shares * f.price)
                            .sum::<f64>();
                        self.completed.push(Outcome {
                            signal: p.signal.clone(),
                            known_at: eq.acquired_at,
                            net_return: p.realized / entry_notional,
                            exit_reason: reason.into(),
                        });
                        self.positions.remove(&row.symbol);
                    }
                }
            }
        }
        if !owned_at_start && !self.positions.contains_key(&row.symbol) && self.entry_data_ready {
            let mut candidates = Vec::new();
            for s in signals
                .iter()
                .filter(|s| s.symbol == row.symbol && s.issued_at == row.at)
            {
                let score = match filter {
                    Filter::RulesOnly => Some((s.target - s.stop) / s.stop),
                    Filter::MachineLearning(m) => m
                        .score(s)
                        .ok()
                        .filter(|v| v.accepted)
                        .map(|v| v.expected_net_return),
                    Filter::Unavailable => None,
                };
                if let Some(score) = score {
                    candidates.push((s, score));
                } else {
                    self.metrics
                        .get_mut(&s.strategy)
                        .ok_or_else(|| invalid("Strategy metrics"))?
                        .rejected_signals += 1;
                }
            }
            candidates.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.strategy.cmp(&b.0.strategy)));
            if let Some((s, _)) = candidates.first() {
                self.enter(s, row, q.as_ref())?;
            }
        }
        for strategy in STRATEGIES {
            let m = self
                .metrics
                .get_mut(&strategy)
                .ok_or_else(|| invalid("Strategy metrics"))?;
            let held: Vec<_> = self
                .positions
                .values()
                .filter(|p| p.signal.strategy == strategy)
                .collect();
            m.open_positions = held.len();
            m.unrealized_pnl = held
                .iter()
                .map(|p| (p.last_bid - p.entry) * p.shares - p.remaining_entry_fees)
                .sum();
            m.net_pnl = m.realized_pnl + m.unrealized_pnl;
            let day = row
                .at
                .with_timezone(&chrono_tz::America::New_York)
                .date_naive()
                .to_string();
            let previous = m
                .daily_pnl
                .iter()
                .filter(|(d, _)| **d != day)
                .map(|(_, v)| *v)
                .sum::<f64>();
            m.daily_pnl.insert(day, m.net_pnl - previous);
            let curve = self.curves.entry(strategy).or_default();
            curve.push(m.net_pnl);
            let mut peak = 0f64;
            for value in curve {
                peak = peak.max(*value);
                m.maximum_drawdown = m
                    .maximum_drawdown
                    .min((*value - peak) / self.initial_capital);
            }
            if let Some(closed) = self.closed.get(&strategy) {
                m.closed_trades = closed.len();
                m.win_rate =
                    Some(closed.iter().filter(|p| **p > 0.).count() as f64 / closed.len() as f64);
                let wins = closed.iter().filter(|p| **p > 0.).sum::<f64>();
                let losses = -closed.iter().filter(|p| **p < 0.).sum::<f64>();
                m.profit_factor = if losses > 0. {
                    Some(wins / losses)
                } else {
                    None
                };
            }
            if m.daily_pnl.len() >= 10 && m.open_positions == 0 && m.missing_quote_events == 0 {
                let n = m.daily_pnl.len() as f64;
                let mean = m.net_pnl / n;
                let variance = m
                    .daily_pnl
                    .values()
                    .map(|p| (p - mean).powi(2))
                    .sum::<f64>()
                    / n;
                m.net_weekly_average = Some(5. * mean);
                if variance > 0. {
                    m.sharpe = Some(mean / variance.sqrt() * 252f64.sqrt());
                }
            }
        }
        Ok(())
    }
}
/// Labels use each strategy alone under identical capital/risk/execution rules;
/// simultaneous opportunities are not falsely counted as simultaneously funded.
pub fn label(signal: &Signal, rows: &[Observation]) -> Result<Option<Outcome>> {
    let mut b = Book::new(1000.)?;
    let mut previous = None;
    for row in rows.iter().filter(|r| {
        r.symbol == signal.symbol
            && r.at >= signal.issued_at
            && r.at <= signal.issued_at + Duration::minutes(signal.maximum_hold_minutes + 10)
    }) {
        if previous.is_some_and(|p| row.at - p > Duration::seconds(305))
            || before(row).is_none()
            || after(row).is_none()
        {
            return Ok(None);
        }
        previous = Some(row.at);
        b.monitor(
            row,
            if row.at == signal.issued_at {
                std::slice::from_ref(signal)
            } else {
                &[]
            },
            Filter::RulesOnly,
        )?;
        if let Some(outcome) = b.completed.first() {
            return Ok(Some(outcome.clone()));
        }
    }
    Ok(None)
}
