//! Bounded strategy experiment; never promotes models or submits orders.
use super::{
    execution::{self, Book, Filter},
    learning::{Outcome, Selector},
    quotes::{self, Plan},
    regime,
    signals::{self, STRATEGIES, Signal},
};
use crate::{
    Result,
    calendar::Calendar,
    intraday::{data::Dataset, features::Candle, research},
    invalid,
};
use chrono::{Duration, NaiveDate};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub fn run(plan: &Plan, quote_directory: &Path, datasets: &[Dataset]) -> Result<Value> {
    run_rows(plan, quotes::load(quote_directory, plan)?, datasets)
}
pub fn run_joined(
    plans: &[Plan],
    directories: &[std::path::PathBuf],
    datasets: &[Dataset],
) -> Result<Value> {
    if plans.is_empty() || plans.len() != directories.len() {
        return Err(invalid("Explicit quote plans/directories must match"));
    }
    let mut effective = plans[0].clone();
    let mut observations = Vec::new();
    let mut seen = BTreeSet::new();
    for (plan, directory) in plans.iter().zip(directories) {
        plan.validate()?;
        if plan.feed != effective.feed
            || plan.symbols != effective.symbols
            || plan.monitoring_minutes != effective.monitoring_minutes
        {
            return Err(invalid("Incompatible quote plans"));
        }
        effective.start = effective.start.min(plan.start);
        effective.end = effective.end.max(plan.end);
        for row in quotes::load(directory, plan)? {
            if !seen.insert((row.at, row.symbol.clone())) {
                return Err(invalid(
                    "Overlapping quote plans must be reconciled explicitly",
                ));
            }
            observations.push(row);
        }
    }
    effective.validate()?;
    observations.sort_by_key(|r| (r.at, r.symbol.clone()));
    let mut report = run_rows(&effective, observations, datasets)?;
    report["collection_plans"] = serde_json::to_value(plans)?;
    report["combined_window_is_not_new_independent_evidence"] = json!(true);
    Ok(report)
}
fn run_rows(
    plan: &Plan,
    observations: Vec<quotes::Observation>,
    datasets: &[Dataset],
) -> Result<Value> {
    plan.validate()?;
    let calendar = Calendar::embedded()?;
    let mut candles = BTreeMap::<String, BTreeMap<_, Candle>>::new();
    for dataset in datasets {
        if dataset.candle_feed != plan.feed {
            return Err(invalid("Strategy candle/quote feeds must match explicitly"));
        }
        for c in dataset.candles.iter().filter(|c| c.minutes == 1) {
            if c.start.date_naive()
                >= NaiveDate::from_ymd_opt(2025, 4, 3).ok_or_else(|| invalid("Protected date"))?
            {
                return Err(invalid(
                    "Protected audit period is not strategy selection data",
                ));
            }
            let rows = candles.entry(c.symbol.clone()).or_default();
            if let Some(p) = rows.get(&c.start) {
                if p.open != c.open
                    || p.high != c.high
                    || p.low != c.low
                    || p.close != c.close
                    || p.volume != c.volume
                    || p.feed != c.feed
                {
                    return Err(invalid("Conflicting strategy candles"));
                }
            } else {
                rows.insert(c.start, c.clone());
            }
        }
    }
    let indexed: BTreeMap<_, Vec<_>> = candles
        .into_iter()
        .map(|(k, v)| (k, v.into_values().collect()))
        .collect();
    let mut signals = BTreeMap::<_, Vec<Signal>>::new();
    let mut reasons = BTreeMap::<String, usize>::new();
    let mut regimes = BTreeMap::<String, usize>::new();
    let mut decision_records = Vec::new();
    for row in &observations {
        let Some(candles) = indexed.get(&row.symbol) else {
            *reasons.entry("MISSING_CANDLES".into()).or_default() += 1;
            continue;
        };
        let start = candles.partition_point(|c| c.start < row.at - Duration::days(21));
        let end = candles.partition_point(|c| c.end() <= row.at);
        match regime::detect(&calendar, &row.symbol, row.at, &candles[start..end], true) {
            Err(e) => {
                *reasons.entry(e.to_string()).or_default() += 1;
            }
            Ok(context) => {
                *regimes.entry(format!("{:?}", context.regime)).or_default() += 1;
                let q = row.before.as_ref().map(|s| {
                    let mut q = s.quote.clone();
                    q.acquired_at = q.source_time + Duration::seconds(1);
                    q
                });
                let session = calendar
                    .session(row.at.with_timezone(&New_York).date_naive())
                    .ok_or_else(|| invalid("Strategy calendar"))?;
                for decision in signals::generate(&context, q.as_ref(), session.close)? {
                    decision_records.push(json!({"at":row.at,"symbol":row.symbol,"strategy":decision.strategy,"reason":decision.reason,"signal_id":decision.signal.as_ref().map(|s|&s.id)}));
                    *reasons
                        .entry(format!("{:?}:{}", decision.strategy, decision.reason))
                        .or_default() += 1;
                    if let Some(s) = decision.signal {
                        signals
                            .entry((row.at, row.symbol.clone()))
                            .or_default()
                            .push(s);
                    }
                }
            }
        }
    }
    let mut labels = Vec::<Outcome>::new();
    let mut unresolved = 0;
    let by_symbol: BTreeMap<_, Vec<_>> = plan
        .symbols
        .iter()
        .map(|s| {
            (
                s.clone(),
                observations
                    .iter()
                    .filter(|r| r.symbol == *s)
                    .cloned()
                    .collect(),
            )
        })
        .collect();
    for signal in signals.values().flatten() {
        if let Some(outcome) = execution::label(signal, &by_symbol[&signal.symbol])? {
            labels.push(outcome);
        } else {
            unresolved += 1;
        }
    }
    labels.sort_by_key(|r| r.signal.issued_at);
    let days = calendar.sessions_between(plan.start, plan.end)?;
    let mut models = BTreeMap::new();
    let mut fitting = Vec::new();
    for day in days.iter().skip(2) {
        let test = calendar
            .session(*day)
            .ok_or_else(|| invalid("Test session"))?
            .open;
        let (train, calibration) = super::learning::chronological_split(&labels, test);
        match Selector::fit(&train,&calibration){Ok(model)=>{fitting.push(json!({"test_day":day,"train":train.len(),"calibration":calibration.len(),"status":"fitted"}));models.insert(*day,model);},Err(e)=>fitting.push(json!({"test_day":day,"train":train.len(),"calibration":calibration.len(),"status":"not_fitted","reason":e.to_string()}))}
    }
    let first_test = days.get(2).copied();
    let mut rule = Book::new(1000.)?;
    let mut ml = Book::new(1000.)?;
    let mut per_rule: BTreeMap<_, _> = STRATEGIES
        .into_iter()
        .map(|s| Book::new(1000.).map(|b| (s, b)))
        .collect::<Result<_>>()?;
    let mut per_ml = per_rule.clone();
    let mut scored = BTreeMap::new();
    let mut score_rejections = BTreeMap::<String, usize>::new();
    for (index, row) in observations.iter().enumerate() {
        let day = row.at.with_timezone(&New_York).date_naive();
        if first_test.is_none_or(|d| day < d) {
            continue;
        }
        if index == 0 || observations[index - 1].at != row.at {
            let end = observations.partition_point(|r| r.at <= row.at);
            let batch = &observations[index..end];
            rule.prepare(batch)?;
            ml.prepare(batch)?;
            for b in per_rule.values_mut().chain(per_ml.values_mut()) {
                b.prepare(batch)?;
            }
        }
        let candidates = signals
            .get(&(row.at, row.symbol.clone()))
            .cloned()
            .unwrap_or_default();
        let filter = models
            .get(&day)
            .map_or(Filter::Unavailable, Filter::MachineLearning);
        rule.monitor(row, &candidates, Filter::RulesOnly)?;
        ml.monitor(row, &candidates, filter)?;
        for strategy in STRATEGIES {
            let own: Vec<_> = candidates
                .iter()
                .filter(|s| s.strategy == strategy)
                .cloned()
                .collect();
            per_rule
                .get_mut(&strategy)
                .ok_or_else(|| invalid("Strategy book"))?
                .monitor(row, &own, Filter::RulesOnly)?;
            per_ml
                .get_mut(&strategy)
                .ok_or_else(|| invalid("ML strategy book"))?
                .monitor(row, &own, filter)?;
        }
        if let Some(model) = models.get(&day) {
            for s in &candidates {
                match model.score(s) {
                    Ok(score) => {
                        scored.insert(s.id.clone(), score);
                    }
                    Err(e) => {
                        *score_rejections.entry(e.to_string()).or_default() += 1;
                    }
                }
            }
        }
    }
    let paired = observations
        .iter()
        .filter(|r| r.before.is_some() && r.after.is_some())
        .count();
    let expected: usize = days
        .iter()
        .filter_map(|d| calendar.session(*d))
        .map(|s| {
            let mut t = s.open + Duration::minutes(5) + Duration::seconds(16);
            let mut n = 0;
            while t < s.close - Duration::seconds(16) {
                n += 1;
                t += Duration::minutes(5);
            }
            n * plan.symbols.len()
        })
        .sum();
    let coverage = paired as f64 / expected.max(1) as f64;
    let per_strategy:Vec<_>=STRATEGIES.into_iter().map(|s|{
        let outcomes:Vec<_>=labels.iter().filter(|r|r.signal.strategy==s&&first_test.is_some_and(|d|r.signal.issued_at.with_timezone(&New_York).date_naive()>=d)).filter_map(|o|scored.get(&o.signal.id).map(|p|(p,o))).collect();
        let brier=if outcomes.is_empty(){None}else{Some(outcomes.iter().map(|(p,o)|(p.probability_net_success-if o.net_return>0.{1.}else{0.}).powi(2)).sum::<f64>()/outcomes.len() as f64)};
        let distinct_dates=labels.iter().filter(|r|r.signal.strategy==s).map(|r|r.signal.issued_at.with_timezone(&New_York).date_naive()).collect::<BTreeSet<_>>().len();
            let reliability:Vec<_>=(0..5).filter_map(|bin|{let rows:Vec<_>=outcomes.iter().filter(|(p,_)|((p.probability_net_success*5.).floor() as usize).min(4)==bin).collect();if rows.is_empty(){None}else{Some(json!({"count":rows.len(),"predicted_success":rows.iter().map(|(p,_)|p.probability_net_success).sum::<f64>()/rows.len() as f64,"observed_success":rows.iter().filter(|(_,o)|o.net_return>0.).count() as f64/rows.len() as f64}))}}).collect();
            json!({"strategy":s,"rule_only":per_rule[&s].metrics[&s],"ml_filtered":per_ml[&s].metrics[&s],"quote_backed_labels":labels.iter().filter(|r|r.signal.strategy==s).count(),"label_sessions":distinct_dates,"test_scored_outcomes":outcomes.len(),"success_brier":brier,"success_calibration":reliability,"validated":false})
    }).collect();
    let mut result = json!({"version":1,"plan":plan,"source_sha256":super::learning::source_hash(),"status":if coverage<0.95{"INSUFFICIENT_QUOTE_COVERAGE"}else if models.is_empty(){"INSUFFICIENT_STRATEGY_LABELS"}else{"DEVELOPMENT_ONLY_CONFIRMATION_REQUIRED"},"expected_quote_points":expected,"observed_quote_points":observations.len(),"paired_quote_points":paired,"paired_coverage":coverage,"monitoring_minutes":5,"signal_count":signals.values().map(Vec::len).sum::<usize>(),"unresolved_signal_outcomes":unresolved,"matured_quote_backed_labels":labels.len(),"regime_counts":regimes,"decision_reasons":reasons,"training":fitting,"selectors":models,"comparison_first_day":first_test,"combined_rules":rule,"combined_ml":ml,"per_strategy":per_strategy,"signal_records":signals.values().flatten().collect::<Vec<_>>(),"outcomes":labels,"strategy_scores":scored,"capital":1000.,"gross_limit_fraction":0.4,"symbol_limit_fraction":0.2,"independent_confirmation":false,"profitable_advantage_validated":false,"strategy_activated":false,"actual_alpaca_paper_fills":0,
        "limitations":["Five-minute monitoring: stop crossing and intrabar execution cannot be inferred between observed quotes","Displayed quote participation is hypothetical, not a guaranteed fill","Historical publication latency is reconstructed, not prospective","Each standalone strategy uses identical capital; standalone results must not be added together","Combined portfolio shares the same capital/exposure limits","A short development pilot cannot establish sustainable performance","No historical borrow evidence; long entries only, bearish regimes hold cash","Unknown/missing quote outcomes are excluded from ML labels, not treated as losses or wins","No automatic model promotion or brokerage strategy activation"]});
    result["decision_records"] = json!(decision_records);
    result["ml_score_rejection_reasons"] = json!(score_rejections);
    Ok(result)
}
pub fn load_data(paths: &[std::path::PathBuf]) -> Result<Vec<Dataset>> {
    paths.iter().map(|p| research::load(p)).collect()
}
