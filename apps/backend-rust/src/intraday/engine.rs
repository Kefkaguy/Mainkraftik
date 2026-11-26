//! The same hourly artifact and economic planner used in portfolio research.
//! No broker transport or automatic strategy activation is created here.
use super::{
    allocation::{self, Costs, Opportunity, Plan, Position, Risk},
    features::FeatureRow,
    models::HourlyModel,
};
use crate::{Result, calendar::Calendar, features::Quote, invalid};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use chrono_tz::America::New_York;
use std::collections::{BTreeMap, BTreeSet};

pub struct MonitorInputs<'a> {
    pub at: DateTime<Utc>,
    pub equity: f64,
    pub capacity: f64,
    pub features: &'a BTreeMap<String, FeatureRow>,
    pub positions: &'a BTreeMap<String, Position>,
    pub quotes: &'a BTreeMap<String, Quote>,
    pub shortable: &'a BTreeSet<String>,
    pub groups: &'a BTreeMap<String, String>,
}

pub struct HourlyPlanner {
    calendar: Calendar,
    model: HourlyModel,
    accounts: BTreeSet<String>,
    interval: i64,
    slots: BTreeMap<String, (NaiveDate, i64)>,
    risk: Risk,
    costs: Costs,
}
impl HourlyPlanner {
    pub fn new(
        model: HourlyModel,
        accounts: [String; 2],
        rebalance_minutes: i64,
        risk: Risk,
        costs: Costs,
    ) -> Result<Self> {
        let accounts: BTreeSet<_> = accounts.into_iter().collect();
        if accounts.len() != 2
            || accounts.contains("")
            || !matches!(rebalance_minutes, 5 | 15 | 30 | 60)
        {
            return Err(invalid(
                "Exactly two pinned accounts and a declared rebalance interval required",
            ));
        }
        Ok(Self {
            calendar: Calendar::embedded()?,
            model,
            accounts,
            interval: rebalance_minutes,
            slots: BTreeMap::new(),
            risk,
            costs,
        })
    }
    /// Called on every monitoring tick. Entry cadence never suppresses independent
    /// stop/holding-period checks or removes a held symbol with no entry features.
    pub fn monitor(&mut self, account: &str, input: MonitorInputs<'_>) -> Result<Plan> {
        let MonitorInputs {
            at,
            equity,
            capacity,
            features,
            positions,
            quotes,
            shortable,
            groups,
        } = input;
        if !self.accounts.contains(account) {
            return Err(invalid("Unknown hourly paper account"));
        }
        let slot = cadence_slot(&self.calendar, at, self.interval);
        let due = slot.is_some_and(|s| self.slots.get(account) != Some(&s));
        let symbols: BTreeSet<_> = features.keys().chain(positions.keys()).cloned().collect();
        let mut rows = Vec::new();
        for symbol in symbols {
            let forecast = features
                .get(&symbol)
                .filter(|f| f.at <= at && at - f.at <= Duration::minutes(1))
                .and_then(|f| self.model.predict(f).ok());
            rows.push(Opportunity {
                symbol: symbol.clone(),
                group: groups.get(&symbol).cloned().unwrap_or("unknown".into()),
                forecast,
                quote: if slot.is_some() {
                    quotes.get(&symbol).cloned()
                } else {
                    None
                },
                position: positions.get(&symbol).cloned(),
                shortable: shortable.contains(&symbol),
            });
        }
        let plan = allocation::plan(&rows, equity, capacity, at, due, self.risk, self.costs)?;
        if let Some(slot) = slot {
            self.slots.insert(account.to_owned(), slot);
        }
        Ok(plan)
    }
}
pub fn cadence_slot(
    calendar: &Calendar,
    at: DateTime<Utc>,
    minutes: i64,
) -> Option<(NaiveDate, i64)> {
    if !matches!(minutes, 5 | 15 | 30 | 60) {
        return None;
    }
    let day = at.with_timezone(&New_York).date_naive();
    let session = calendar.session(day)?;
    if at < session.open || at > session.close {
        return None;
    }
    Some((day, (at - session.open).num_minutes() / minutes))
}
