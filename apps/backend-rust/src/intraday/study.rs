//! Bounded development study. No confirmation data, promotion or broker tools.
use super::{
    allocation::Costs,
    data::Dataset,
    evaluation::{self, Simulation, Tick},
    learning::{Adaptive, Matured},
    models::{Family, Forecast},
    research::{Protocol, QuoteIndex, forecast_scores, panel, replay_quote},
    training::{self, Sample},
};
use crate::{Result, calendar::Calendar, canonical, invalid};
use chrono::{DateTime, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
type ForecastIndex = BTreeMap<(DateTime<Utc>, String), Forecast>;
fn passive_benchmark(
    calendar: &Calendar,
    data: &Dataset,
    first: NaiveDate,
    protocol: &Protocol,
) -> Result<Value> {
    let mut rows: Vec<_> = data
        .candles
        .iter()
        .filter(|r| {
            r.symbol == "SPY"
                && r.minutes == 1
                && r.start.with_timezone(&New_York).date_naive() >= first
                && r.start.with_timezone(&New_York).date_naive() <= protocol.development_end
                && calendar
                    .session(r.start.with_timezone(&New_York).date_naive())
                    .is_some_and(|s| r.start >= s.open && r.end() <= s.close)
        })
        .collect();
    rows.sort_by_key(|r| r.start);
    rows.dedup_by_key(|r| r.start);
    let Some(first) = rows.first() else {
        return Ok(json!({"status":"unavailable"}));
    };
    let last = rows
        .last()
        .ok_or_else(|| invalid("Passive benchmark missing"))?;
    let gross = last.close / first.open - 1.;
    Ok(
        json!({"status":"price_context_only_execution_not_established","start":first.start,"end":last.end(),"gross_price_return":gross,
        "gross_price_pnl_at_same_capital":protocol.capital*gross,"net_executable_pnl":null,"dividends_included":false,"selection_or_acceptance_use":false}),
    )
}
fn regimes(predictions: &ForecastIndex, panel: &[Sample], threshold: f64) -> Value {
    let mut groups = BTreeMap::<String, Vec<(Forecast, f64)>>::new();
    for row in panel {
        if let Some(p) = predictions.get(&(row.features.at, row.features.symbol.clone())) {
            groups
                .entry(format!(
                    "{}_volatility_{}",
                    if row.features.x[17] >= 0. {
                        "market_up"
                    } else {
                        "market_down"
                    },
                    if row.features.x[12] > threshold {
                        "high"
                    } else {
                        "low"
                    }
                ))
                .or_default()
                .push((p.clone(), row.actual_return));
            groups
                .entry(format!("stock_{}", row.features.symbol))
                .or_default()
                .push((p.clone(), row.actual_return));
        }
    }
    json!({"training_only_volatility_threshold":threshold,"forecasts":groups.into_iter().map(|(k,v)|(k,forecast_scores(&v))).collect::<BTreeMap<_,_>>(),
        "execution_pnl_by_regime":"Not inferred from forecast labels; insufficient quote-backed trade attribution remains a limitation"})
}
fn replay(
    context: (&Calendar, &QuoteIndex),
    predictions: &ForecastIndex,
    protocol: &Protocol,
    first: NaiveDate,
    interval: i64,
    weighted: bool,
    costs: Costs,
) -> Result<Simulation> {
    let (calendar, quotes) = context;
    let mut ticks = Vec::new();
    let mut quoted = 0usize;
    for ((at, symbol), quote) in quotes {
        if !protocol.symbols.contains(symbol) || at.with_timezone(&New_York).date_naive() < first {
            continue;
        }
        let q = replay_quote(quote);
        if q.validate(*at).is_ok() {
            quoted += 1;
        }
        ticks.push(Tick {
            at: *at,
            symbol: symbol.clone(),
            group: "market-correlated-conservative".into(),
            quote: Some(q),
            forecast: predictions.get(&(*at, symbol.clone())).cloned(),
            executable_volume: quote.bid_shares.min(quote.ask_shares),
            shortable: false,
        });
    }
    for ((at, symbol), forecast) in predictions {
        if !quotes.contains_key(&(*at, symbol.clone())) {
            ticks.push(Tick {
                at: *at,
                symbol: symbol.clone(),
                group: "market-correlated-conservative".into(),
                quote: None,
                forecast: Some(forecast.clone()),
                executable_volume: 0.,
                shortable: false,
            });
        }
    }
    let mut risk = protocol.risk;
    risk.probability_weighting = weighted;
    let mut simulation = evaluation::simulate(
        calendar,
        &ticks,
        protocol.capital,
        interval,
        risk,
        costs,
        evaluation::Execution::default(),
    )?;
    let minutes: i64 = calendar
        .sessions_between(first, protocol.development_end)?
        .iter()
        .filter_map(|d| calendar.session(*d))
        .map(|s| (s.close - s.open).num_minutes())
        .sum();
    simulation.metrics.executable_quote_coverage =
        (quoted as f64 / (minutes as f64 * protocol.symbols.len() as f64).max(1.)).min(1.);
    Ok(simulation)
}
fn uncertainty(simulation: &Simulation) -> Result<Value> {
    let days: Vec<_> = simulation.metrics.daily_pnl.values().copied().collect();
    if days.len() < 10 {
        return Ok(
            json!({"status":"insufficient_blocks","sessions":days.len(),"weekly_lower_bound":null}),
        );
    }
    let mut rng = crate::numpy_rng::Pcg64::new(20261121);
    let mut samples = Vec::with_capacity(10000);
    for _ in 0..10000 {
        let mut sum = 0.;
        let mut n = 0;
        while n < days.len() {
            let start = rng.bounded_u64(days.len() as u64)? as usize;
            for j in 0..5 {
                if n == days.len() {
                    break;
                }
                sum += days[(start + j) % days.len()];
                n += 1;
            }
        }
        samples.push(5. * sum / days.len() as f64);
    }
    samples.sort_by(f64::total_cmp);
    let tail = (10000. * 0.05 / 128.) as usize;
    Ok(
        json!({"status":"development_only_not_independent","sessions":days.len(),"five_session_blocks":days.len()/5,"bootstrap_samples":10000,"familywise_comparisons":128,
        "estimated_weekly_mean":5.*days.iter().sum::<f64>()/days.len() as f64,"weekly_lower_bound":samples[tail],"weekly_upper_bound":samples[9999-tail],
        "execution_coverage_sufficient":simulation.metrics.executable_quote_coverage>=0.95,"inference_permitted":false}),
    )
}
fn scores_for(predictions: &ForecastIndex, panel: &[Sample]) -> Value {
    let rows: Vec<_> = panel
        .iter()
        .filter_map(|s| {
            predictions
                .get(&(s.features.at, s.features.symbol.clone()))
                .map(|p| (p.clone(), s.actual_return))
        })
        .collect();
    forecast_scores(&rows)
}
fn adapt(
    predictions: &ForecastIndex,
    panel: &[Sample],
    mode: &str,
    alpha: f64,
    regime_threshold: f64,
) -> Result<(ForecastIndex, usize)> {
    let (window, half) = match mode {
        "all_history" => (20000, None),
        "rolling_256" | "regime_256" => (256, None),
        "rolling_1024" => (1024, None),
        "recency_60m" => (1024, Some(60.)),
        "recency_240m" => (1024, Some(240.)),
        _ => return Err(invalid("Unknown preregistered adaptation")),
    };
    let mut learners = [
        Adaptive::new(5, alpha, window, half)?,
        Adaptive::new(5, alpha, window, half)?,
    ];
    let observed: BTreeMap<_, _> = panel
        .iter()
        .map(|s| ((s.features.at, s.features.symbol.clone()), s))
        .collect();
    let mut pending = VecDeque::<(usize, Matured)>::new();
    let mut result = BTreeMap::new();
    let mut unstable = 0;
    for ((at, symbol), base) in predictions {
        while pending.front().is_some_and(|(_, r)| r.known_at <= *at) {
            let (regime, row) = pending
                .pop_front()
                .ok_or_else(|| invalid("Matured queue"))?;
            learners[regime].push(row, *at)?;
        }
        let sample = observed
            .get(&(*at, symbol.clone()))
            .ok_or_else(|| invalid("Missing prequential sample"))?;
        let regime = usize::from(mode == "regime_256" && sample.features.x[12] > regime_threshold);
        let x = vec![
            1.,
            sample.features.x[0] / 0.01,
            sample.features.x[1] / 0.01,
            sample.features.x[2] / 0.01,
            sample.features.x[3] / 0.01,
        ];
        let correction = match learners[regime].predict_correction(&x, 2. * base.uncertainty) {
            Ok(c) => c,
            Err(_) => {
                unstable += 1;
                0.
            }
        };
        let mut forecast = base.clone();
        forecast.expected_return += correction;
        forecast.lower += correction;
        forecast.upper += correction;
        result.insert((*at, symbol.clone()), forecast);
        pending.push_back((
            regime,
            Matured {
                prediction_id: canonical::digest(&json!([at, symbol, base.expected_return]))?,
                issued_at: *at,
                target_at: sample.target_at,
                known_at: sample.known_at,
                x,
                prior_return: base.expected_return,
                actual_return: sample.actual_return,
            },
        ));
    }
    Ok((result, unstable))
}
pub fn run(calendar: &Calendar, data: &Dataset, protocol: &Protocol) -> Result<Value> {
    protocol.validate()?;
    let (panel, quotes, skipped) = panel(calendar, data, protocol)?;
    let observed_days = panel
        .iter()
        .map(|s| s.features.at.with_timezone(&New_York).date_naive())
        .collect::<BTreeSet<_>>();
    let days = calendar.sessions_between(protocol.development_start, protocol.development_end)?;
    let base = json!({"protocol":protocol,"dataset_id":canonical::digest(&serde_json::to_value(data)?)?,"samples":panel.len(),"sessions":observed_days.len(),"expected_sessions":days.len(),"skipped_features":skipped,"execution_quote_samples":quotes.len(),"data_failures":data.failures,
        "historical_development_only":true,"independent_confirmation":false,"strategy_activated":false,"profitable_advantage_validated":false,
        "protected_selection_period":"2025-04-03 through declaration excluded; frozen Phase7/8 artifacts untouched","news_features":"Excluded without timestamp-valid coverage"});
    if observed_days.len()
        < protocol.train_sessions + protocol.calibration_sessions + protocol.test_sessions
    {
        let mut report = base;
        report["status"] = json!("INSUFFICIENT_DEVELOPMENT_DATA");
        report["minimum_sessions"] =
            json!(protocol.train_sessions + protocol.calibration_sessions + protocol.test_sessions);
        return Ok(report);
    }
    let groups = [
        ("returns", vec![0, 1, 2, 3]),
        (
            "price_volume_market",
            (0..14).chain(16..21).chain([25, 26]).collect(),
        ),
    ];
    let mut configurations = Vec::new();
    let mut artifacts = Vec::new();
    let first_test = days[protocol.train_sessions + protocol.calibration_sessions];
    let mut best_pnl = f64::NEG_INFINITY;
    let mut best_index = 0usize;
    let mut best_predictions = ForecastIndex::new();
    let mut best_parameters = (5, false);
    let mut forecast_artifacts = Vec::new();

    for family in [
        Family::Ridge,
        Family::Logistic,
        Family::HistogramGradientBoosting,
    ] {
        for alpha in [1., 10.] {
            for (group, columns) in &groups {
                let mut predictions = BTreeMap::new();
                let mut scores = Vec::new();
                let mut failures = Vec::new();
                for test_start in (protocol.train_sessions + protocol.calibration_sessions
                    ..days.len())
                    .step_by(protocol.test_sessions)
                {
                    let cal_start = test_start - protocol.calibration_sessions;
                    let train_start = cal_start - protocol.train_sessions;
                    let boundary = |i: usize| -> Result<DateTime<Utc>> {
                        calendar
                            .session(days[i])
                            .map(|s| s.open)
                            .ok_or_else(|| invalid("Walk-forward session missing"))
                    };
                    let train_open = boundary(train_start)?;
                    let cal_open = boundary(cal_start)?;
                    let test_open = boundary(test_start)?;
                    let train = panel
                        .iter()
                        .filter(|r| r.features.at >= train_open && r.known_at < cal_open)
                        .cloned()
                        .collect::<Vec<_>>();
                    let cal = panel
                        .iter()
                        .filter(|r| r.features.at >= cal_open && r.known_at < test_open)
                        .cloned()
                        .collect::<Vec<_>>();
                    let model = match training::fit(&train, &cal, family, columns.clone(), alpha) {
                        Ok(m) => m,
                        Err(e) => {
                            failures.push(e.to_string());
                            continue;
                        }
                    };
                    let end = (test_start + protocol.test_sessions).min(days.len());
                    for row in panel.iter().filter(|r| {
                        r.features.at.with_timezone(&New_York).date_naive() >= days[test_start]
                            && r.features.at.with_timezone(&New_York).date_naive() <= days[end - 1]
                    }) {
                        let forecast = model.predict(&row.features)?;
                        scores.push((forecast.clone(), row.actual_return));
                        predictions
                            .insert((row.features.at, row.features.symbol.clone()), forecast);
                    }
                    artifacts.push(json!({"family":family,"alpha":alpha,"feature_group":group,"test_start":days[test_start],"model":model}));
                }
                forecast_artifacts.push(json!({"family":family,"alpha":alpha,"feature_group":group,"historical_counterfactual_not_prospective":true,"predictions":predictions.iter().map(|((at,symbol),forecast)|json!({"at":at,"symbol":symbol,"forecast":forecast})).collect::<Vec<_>>()}));
                for interval in [5, 15, 30, 60] {
                    for weighted in [false, true] {
                        let mut simulation = replay(
                            (calendar, &quotes),
                            &predictions,
                            protocol,
                            first_test,
                            interval,
                            weighted,
                            protocol.costs,
                        )?;
                        let scored = forecast_scores(&scores);
                        simulation.metrics.forecast_mae = scored["mae"].as_f64();
                        simulation.metrics.brier = scored["brier"].as_f64();
                        simulation.metrics.directional_accuracy =
                            scored["directional_accuracy"].as_f64();
                        simulation.metrics.calibration = scored["calibration"]
                            .as_array()
                            .cloned()
                            .unwrap_or_default();
                        let interval_uncertainty = uncertainty(&simulation)?;
                        if simulation.metrics.net_pnl > best_pnl {
                            best_pnl = simulation.metrics.net_pnl;
                            best_index = configurations.len();
                            best_predictions = predictions.clone();
                            best_parameters = (interval, weighted);
                        }
                        configurations.push(json!({"family":family,"alpha":alpha,"feature_group":group,"rebalance_minutes":interval,"probability_weighting":weighted,"forecast_scores":scored,"simulation":simulation,"uncertainty":interval_uncertainty,"training_failures":failures}));
                    }
                }
            }
        }
    }
    let mut vol: Vec<_> = panel
        .iter()
        .filter(|r| {
            r.features.at.with_timezone(&New_York).date_naive() < days[protocol.train_sessions]
        })
        .map(|r| r.features.x[12])
        .collect();
    vol.sort_by(f64::total_cmp);
    let threshold = vol.get(vol.len() / 2).copied().unwrap_or(0.);
    let baseline_sim = replay(
        (calendar, &quotes),
        &best_predictions,
        protocol,
        first_test,
        best_parameters.0,
        best_parameters.1,
        protocol.costs,
    )?;
    let baseline_scores = scores_for(&best_predictions, &panel);
    let mut adaptive_results = Vec::new();
    for mode in [
        "all_history",
        "rolling_256",
        "rolling_1024",
        "recency_60m",
        "recency_240m",
        "regime_256",
    ] {
        for alpha in [1., 10., 100.] {
            let (predictions, unstable) = adapt(&best_predictions, &panel, mode, alpha, threshold)?;
            let scored = scores_for(&predictions, &panel);
            let simulation = replay(
                (calendar, &quotes),
                &predictions,
                protocol,
                first_test,
                best_parameters.0,
                best_parameters.1,
                protocol.costs,
            )?;
            let mut paired = simulation.clone();
            for (day, pnl) in &mut paired.metrics.daily_pnl {
                *pnl -= baseline_sim
                    .metrics
                    .daily_pnl
                    .get(day)
                    .copied()
                    .unwrap_or(0.);
            }
            let interval = uncertainty(&paired)?;
            let guard = super::learning::Validation {
                samples: scored["n"].as_u64().unwrap_or(0) as usize,
                baseline_mae: baseline_scores["mae"].as_f64().unwrap_or(f64::INFINITY),
                candidate_mae: scored["mae"].as_f64().unwrap_or(f64::INFINITY),
                baseline_brier: baseline_scores["brier"].as_f64().unwrap_or(f64::INFINITY),
                candidate_brier: scored["brier"].as_f64().unwrap_or(f64::INFINITY),
                baseline_net_pnl: baseline_sim.metrics.net_pnl,
                candidate_net_pnl: simulation.metrics.net_pnl,
                paired_net_lower_bound: interval["weekly_lower_bound"]
                    .as_f64()
                    .unwrap_or(f64::NEG_INFINITY),
            };
            adaptive_results.push(json!({"mode":mode,"alpha":alpha,"forecast_scores":scored,"simulation":simulation,"paired_net_uncertainty":interval,"unstable_corrections_rejected":unstable,
                "passes_development_update_guard":guard.acceptable(),"adopted":false}));
        }
    }
    let mut cost_stress = Vec::new();
    for bps in [1., 5., 10.] {
        let mut costs = protocol.costs;
        costs.slippage_bps = bps;
        let simulation = replay(
            (calendar, &quotes),
            &best_predictions,
            protocol,
            first_test,
            best_parameters.0,
            best_parameters.1,
            costs,
        )?;
        cost_stress.push(json!({"slippage_bps":bps,"simulation":simulation}));
    }
    let mut mean_predictions = ForecastIndex::new();
    let mut means = BTreeMap::<usize, (f64, f64)>::new();
    let mut zero_predictions = ForecastIndex::new();
    for ((at, symbol), base) in &best_predictions {
        let day = at.with_timezone(&New_York).date_naive();
        let index = days
            .iter()
            .position(|d| *d == day)
            .ok_or_else(|| invalid("Baseline date"))?;
        let fold = protocol.train_sessions
            + protocol.calibration_sessions
            + ((index - protocol.train_sessions - protocol.calibration_sessions)
                / protocol.test_sessions)
                * protocol.test_sessions;
        let (mean, probability) = if let Some(value) = means.get(&fold) {
            *value
        } else {
            let start = calendar
                .session(days[fold - protocol.calibration_sessions])
                .ok_or_else(|| invalid("Baseline calibration"))?
                .open;
            let end = calendar
                .session(days[fold])
                .ok_or_else(|| invalid("Baseline fold"))?
                .open;
            let prior: Vec<_> = panel
                .iter()
                .filter(|r| r.features.at >= start && r.known_at < end)
                .map(|r| r.actual_return)
                .collect();
            if prior.is_empty() {
                continue;
            }
            let value = (
                prior.iter().sum::<f64>() / prior.len() as f64,
                (prior.iter().filter(|y| **y > 0.).count() as f64 + 1.) / (prior.len() as f64 + 2.),
            );
            means.insert(fold, value);
            value
        };
        let mut m = base.clone();
        m.expected_return = mean;
        m.probability_up = probability;
        mean_predictions.insert((*at, symbol.clone()), m);
        let mut z = base.clone();
        z.expected_return = 0.;
        z.probability_up = 0.5;
        zero_predictions.insert((*at, symbol.clone()), z);
    }
    let mean_benchmark = json!({"forecast_scores":scores_for(&mean_predictions,&panel),"simulation":replay((calendar,&quotes),&mean_predictions,protocol,first_test,15,true,protocol.costs)?});
    let zero_benchmark = json!({"forecast_scores":scores_for(&zero_predictions,&panel),"simulation":replay((calendar,&quotes),&zero_predictions,protocol,first_test,15,true,protocol.costs)?});
    if configurations.len() + adaptive_results.len() + cost_stress.len()
        > protocol.maximum_configurations
    {
        return Err(invalid("Preregistered configuration budget exceeded"));
    }
    let execution_qualified = baseline_sim.metrics.executable_quote_coverage >= 0.95
        && baseline_sim.metrics.closed_trades >= protocol.minimum_closed_trades
        && baseline_sim.metrics.open_positions == 0
        && baseline_sim.metrics.daily_risk_unobserved == 0;
    let mut report = base;
    report["status"] = json!(if execution_qualified {
        "DEVELOPMENT_COMPLETE_CONFIRMATION_PENDING"
    } else {
        "DEVELOPMENT_EXECUTION_EVIDENCE_INSUFFICIENT"
    });
    report["configurations"] = json!(configurations);
    report["development_best_index"] = json!(best_index);
    report["adaptive_comparisons"] = json!(adaptive_results);
    report["cost_sensitivity"] = json!(cost_stress);
    report["historical_hourly_mean_benchmark"] = mean_benchmark;
    report["zero_return_benchmark"] = zero_benchmark;
    report["forecast_artifacts"] = json!(forecast_artifacts);
    report["frozen_original_policy_benchmark"] = json!({"status":"not_causal_for_this_historical_period","reason":"Preserved deployed bundle was trained in November 2026; applying it to March 2025 would leak future information","prospective_comparison_required":true});
    report["passive_spy_benchmark"] = passive_benchmark(calendar, data, first_test, protocol)?;
    report["regime_breakdown"] = regimes(&best_predictions, &panel, threshold);
    report["minimum_screening_trade_count_passed"] =
        json!(baseline_sim.metrics.closed_trades >= protocol.minimum_closed_trades);
    report["execution_evidence_qualified"] = json!(execution_qualified);
    report["model_artifacts"] = json!(artifacts);
    report["cash_benchmark"] = json!({"net_pnl":0.,"maximum_drawdown":0.,"turnover":0.});
    report["limitations"] = json!([
        "Development data was previously exposed as development; this is not independent evidence",
        "60 genuinely prospective confirmation sessions and at least 30 closed trades are mandatory screening conditions, not proof",
        "All exposures conservatively share one correlation group; no diversification credit assumed",
        "Fees, borrow and slippage are planning assumptions, not broker invoice reconciliation",
        "No strategy activation or automatic model promotion",
        "Short entries disabled: historical borrow/locate availability is not supplied",
        "March 2025 prices use explicitly prospective 2026 planning fees, not reconstructed historical invoices"
    ]);
    Ok(report)
}
