//! Focused new-strategy verification. Existing accepted migration suites are not rerun.
use chrono::{Duration, TimeZone, Utc};
use mainkraft::{
    calendar::Calendar,
    features::Quote,
    intraday::{
        allocation::{self, Costs, Opportunity, Position, Reason, Risk},
        evaluation::{self, Execution, Tick},
        features::{self, Candle, FeatureRow},
        learning::{Adaptive, Matured, Validation},
        models::{Family, Forecast, HourlyModel},
        training::{self, Sample},
    },
};

fn now() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2025, 3, 31, 15, 0, 15).unwrap()
}
fn quote(at: chrono::DateTime<Utc>, price: f64) -> Quote {
    Quote {
        bid: price - 0.01,
        ask: price + 0.01,
        source_time: at,
        acquired_at: at,
        feed: "iex".into(),
    }
}
fn forecast(at: chrono::DateTime<Utc>, mean: f64) -> Forecast {
    Forecast {
        issued_at: at,
        target_at: at + Duration::minutes(60),
        expected_return: mean,
        probability_up: if mean > 0. { 0.8 } else { 0.2 },
        lower: mean - 0.01,
        upper: mean + 0.01,
        uncertainty: 0.005,
    }
}
fn opportunity(mean: f64, position: Option<Position>) -> Opportunity {
    Opportunity {
        symbol: "AMD".into(),
        group: "technology".into(),
        forecast: Some(forecast(now(), mean)),
        quote: Some(quote(now(), 100.)),
        position,
        shortable: true,
    }
}

#[test]
fn cash_no_edge_cost_inertia_and_rejected_signals_still_manage_positions() {
    let risk = Risk::default();
    let costs = Costs::default();
    let p = allocation::plan(
        &[opportunity(0.0001, None)],
        1000.,
        2000.,
        now(),
        true,
        risk,
        costs,
    )
    .unwrap();
    assert_eq!(p.decisions[0].reason, Reason::NoNetEdge);
    assert_eq!(p.gross_target, 0.);
    assert_eq!(p.unallocated_cash, 1000.);
    let owned = Position {
        shares: 1.,
        entry_price: 100.,
        mark_price: 100.,
        opened_at: now() - Duration::minutes(10),
    };
    let p = allocation::plan(
        &[opportunity(0.0001, Some(owned.clone()))],
        1000.,
        2000.,
        now(),
        true,
        risk,
        costs,
    )
    .unwrap();
    assert_eq!(p.decisions[0].target_value, 100.);
    assert_eq!(p.decisions[0].estimated_change_cost, 0.);
    let mut stopped = opportunity(0.0001, Some(owned));
    stopped.forecast = None;
    stopped.quote = Some(quote(now(), 97.));
    let p = allocation::plan(&[stopped.clone()], 1000., 2000., now(), false, risk, costs).unwrap();
    assert_eq!(p.decisions[0].reason, Reason::ForcedExit);
    assert_eq!(p.decisions[0].target_value, 0.);
    stopped.quote = None;
    let p = allocation::plan(&[stopped], 1000., 2000., now(), false, risk, costs).unwrap();
    assert!(!p.decisions[0].executable);
    assert_eq!(p.risk_unobserved, vec!["AMD"]);
    let mut inputs = vec![opportunity(0.01, None); 6];
    for (i, r) in inputs.iter_mut().enumerate() {
        r.symbol = format!("T{i}");
    }
    let p = allocation::plan(&inputs, 1000., 2000., now(), true, risk, costs).unwrap();
    assert!(p.gross_target <= 400.0000001);
    assert!(p.unallocated_cash >= 599.999999);
}

#[test]
fn causal_minute_features_reject_missing_context_and_ignore_future_news_and_candles() {
    let calendar = Calendar::embedded().unwrap();
    let open = Utc.with_ymd_and_hms(2025, 3, 31, 13, 30, 0).unwrap();
    let at = open + Duration::minutes(90) + Duration::seconds(15);
    let mut rows = Vec::new();
    for name in ["AMD", "SPY", "XLK"] {
        for i in 0..100 {
            let t = open + Duration::minutes(i);
            let p = 100. + i as f64 * 0.01;
            rows.push(Candle {
                symbol: name.into(),
                start: t,
                minutes: 1,
                open: p,
                high: p + 0.2,
                low: p - 0.2,
                close: p + 0.1,
                volume: 1000.,
                vwap: Some(p),
                available_at: t + Duration::seconds(61),
                acquired_at: at + Duration::days(1),
                feed: "sip".into(),
                historical_availability_assumed: true,
            });
        }
    }
    let a = features::build(
        &calendar,
        &rows,
        &[],
        "AMD",
        at,
        Some(&quote(at, 101.)),
        true,
    )
    .unwrap();
    assert_eq!(a.x.len(), features::NAMES.len());
    assert!(a.x[0] > 0.);
    assert!(a.hypothetical_history);
    assert!(a.quote_available);
    for r in rows.iter_mut().filter(|r| r.start > at) {
        r.close *= 2.;
        r.high *= 2.;
    }
    let b = features::build(
        &calendar,
        &rows,
        &[],
        "AMD",
        at,
        Some(&quote(at, 101.)),
        true,
    )
    .unwrap();
    assert_eq!(a.x, b.x);
    let late = features::News {
        source_id: "synthetic-news".into(),
        model_revision: mainkraft::sentiment::REVISION.into(),
        symbol: "AMD".into(),
        published_at: at - Duration::hours(1),
        acquired_at: at - Duration::minutes(5),
        scored_at: at + Duration::seconds(1),
        sentiment: 0.9,
    };
    let with_late = features::build(
        &calendar,
        &rows,
        std::slice::from_ref(&late),
        "AMD",
        at,
        Some(&quote(at, 101.)),
        true,
    )
    .unwrap();
    assert_eq!(with_late.x[21], 0.);
    let mut valid = late;
    valid.scored_at = at - Duration::minutes(4);
    let with_valid = features::build(
        &calendar,
        &rows,
        &[valid],
        "AMD",
        at,
        Some(&quote(at, 101.)),
        true,
    )
    .unwrap();
    assert_eq!(with_valid.x[21], 0.9);
    rows.retain(|r| !(r.symbol == "SPY" && r.start == open + Duration::minutes(60)));
    assert!(
        features::build(
            &calendar,
            &rows,
            &[],
            "AMD",
            at,
            Some(&quote(at, 101.)),
            false
        )
        .is_err()
    );
    assert!(features::build(&calendar, &rows, &[], "AMD", at, None, false).is_err());
}

fn samples(count: usize, start: chrono::DateTime<Utc>) -> Vec<Sample> {
    (0..count)
        .map(|i| {
            let at = start + Duration::minutes(i as i64 * 5);
            let x = (i as f64 * 0.37).sin();
            let z = (i as f64 * 0.17).cos();
            let mut values = vec![0.; features::NAMES.len()];
            values[0] = x;
            values[1] = z;
            Sample {
                features: FeatureRow {
                    feed: "sip".into(),
                    symbol: "AMD".into(),
                    at,
                    x: values,
                    hypothetical_history: true,
                    quote_available: false,
                },
                target_at: at + Duration::minutes(60),
                known_at: at + Duration::minutes(60),
                actual_return: 0.002 * x + 0.001 * z + 0.0003 * (i as f64).sin(),
            }
        })
        .collect()
}
#[test]
fn native_hourly_three_families_calibrate_roundtrip_and_reject_overlapping_labels() {
    let train = samples(300, now());
    let cal = samples(90, now() + Duration::days(3));
    let test = samples(90, now() + Duration::days(4));
    for family in [
        Family::Ridge,
        Family::Logistic,
        Family::HistogramGradientBoosting,
    ] {
        let model = training::fit(&train, &cal, family, vec![0, 1], 10.).unwrap();
        let saved = serde_json::to_vec(&model).unwrap();
        let loaded: HourlyModel = serde_json::from_slice(&saved).unwrap();
        let mut mae = 0.;
        let mut brier = 0.;
        for row in &test {
            let p = loaded.predict(&row.features).unwrap();
            assert_eq!(p.target_at - row.features.at, Duration::minutes(60));
            assert!((0. ..=1.).contains(&p.probability_up));
            assert!(p.lower <= p.upper);
            mae += (p.expected_return - row.actual_return).abs();
            brier += (p.probability_up - if row.actual_return > 0. { 1. } else { 0. }).powi(2);
        }
        println!(
            "SYNTHETIC_ONLY family={family:?} test_n={} mae={} brier={}",
            test.len(),
            mae / test.len() as f64,
            brier / test.len() as f64
        );
        assert!(loaded.predict(&train[0].features).is_err());
        let at = test[0].features.at;
        let mut planner = mainkraft::engine::HourlyPlanner::new(
            loaded.clone(),
            ["first".into(), "second".into()],
            60,
            Risk::default(),
            Costs::default(),
        )
        .unwrap();
        let mut broken = test[0].features.clone();
        broken.x[0] = f64::NAN;
        let inputs = std::collections::BTreeMap::from([("AMD".into(), broken)]);
        let positions = std::collections::BTreeMap::from([(
            "AMD".into(),
            Position {
                shares: 1.,
                entry_price: 100.,
                mark_price: 97.,
                opened_at: at - Duration::minutes(5),
            },
        )]);
        let quotes = std::collections::BTreeMap::from([("AMD".into(), quote(at, 97.))]);
        let groups = std::collections::BTreeMap::new();
        let shortable = std::collections::BTreeSet::new();
        let plan = planner
            .monitor(
                "first",
                mainkraft::intraday::engine::MonitorInputs {
                    at,
                    equity: 1000.,
                    capacity: 1000.,
                    features: &inputs,
                    positions: &positions,
                    quotes: &quotes,
                    shortable: &shortable,
                    groups: &groups,
                },
            )
            .unwrap();
        assert_eq!(plan.decisions[0].reason, Reason::ForcedExit);
        assert_eq!(plan.decisions[0].target_value, 0.);
        assert_eq!(
            mainkraft::intraday::engine::cadence_slot(&Calendar::embedded().unwrap(), at, 60)
                .unwrap()
                .1,
            1
        );
    }
    assert!(training::fit(&train, &train[100..190], Family::Ridge, vec![0, 1], 1.).is_err());
}

#[test]
fn incremental_rolling_recency_matches_batch_and_rejects_immature_or_degrading_updates() {
    let mut state = Adaptive::new(2, 10., 64, Some(30.)).unwrap();
    let mut rows = Vec::new();
    for i in 0..100 {
        let issued = now() + Duration::minutes(i);
        let target = issued + Duration::minutes(60);
        let row = Matured {
            prediction_id: format!("p{i}"),
            issued_at: issued,
            target_at: target,
            known_at: target,
            x: vec![1., (i as f64).sin()],
            prior_return: 0.001,
            actual_return: 0.002 * (i as f64).cos(),
        };
        assert!(state.push(row.clone(), target).unwrap());
        rows.push(row);
    }
    let last = rows.last().unwrap().known_at;
    let mut gram = nalgebra::DMatrix::<f64>::identity(2, 2) * 10.;
    let mut rhs = nalgebra::DVector::<f64>::zeros(2);
    for row in &rows[36..] {
        let weight =
            (-std::f64::consts::LN_2 * (last - row.known_at).num_seconds() as f64 / 60. / 30.)
                .exp();
        let x = nalgebra::DVector::from_column_slice(&row.x);
        gram += weight * &x * x.transpose();
        rhs += weight * x * (row.actual_return - row.prior_return);
    }
    let batch = gram.lu().solve(&rhs).unwrap();
    let x = [1., 0.5];
    let expected = batch[0] + batch[1] * x[1];
    assert!((state.predict_correction(&x, 1.).unwrap() - expected).abs() < 1e-12);
    let mut restored: Adaptive =
        serde_json::from_slice(&serde_json::to_vec(&state).unwrap()).unwrap();
    assert_eq!(
        restored.predict_correction(&x, 1.).unwrap(),
        state.predict_correction(&x, 1.).unwrap()
    );
    assert!(!restored.push(rows[99].clone(), last).unwrap());
    let n = restored.observations();
    assert!(
        restored
            .push(rows[99].clone(), last - Duration::minutes(1))
            .is_err()
    );
    assert_eq!(restored.observations(), n);
    assert!(
        !Validation {
            samples: 200,
            baseline_mae: 0.01,
            candidate_mae: 0.005,
            baseline_brier: 0.2,
            candidate_brier: 0.19,
            baseline_net_pnl: 1.,
            candidate_net_pnl: -1.,
            paired_net_lower_bound: -2.
        }
        .acceptable()
    );
}

#[test]
fn portfolio_replay_charges_costs_uses_future_execution_quotes_and_keeps_partial_reservations() {
    let calendar = Calendar::embedded().unwrap();
    let at = now();
    let ticks: Vec<_> = (0..12)
        .map(|i| {
            let at = at + Duration::minutes(i);
            Tick {
                at,
                symbol: "AMD".into(),
                group: "market".into(),
                quote: Some(quote(at, 100.)),
                forecast: Some(forecast(at, 0.02)),
                executable_volume: 1000.,
                shortable: true,
            }
        })
        .collect();
    let a = evaluation::simulate(
        &calendar,
        &ticks,
        1000.,
        5,
        Risk::default(),
        Costs::default(),
        Execution {
            fill_fraction: 1.,
            participation: 1.,
            ..Execution::default()
        },
    )
    .unwrap();
    assert!(a.metrics.submitted_orders > 0);
    assert!(a.metrics.execution_costs > 0.);
    assert!(a.metrics.net_pnl < 0.);
    assert!(a.metrics.turnover > 0.);
    let b = evaluation::simulate(
        &calendar,
        &ticks,
        1000.,
        5,
        Risk::default(),
        Costs {
            slippage_bps: 10.,
            ..Costs::default()
        },
        Execution {
            fill_fraction: 1.,
            participation: 1.,
            ..Execution::default()
        },
    )
    .unwrap();
    assert!(b.metrics.net_pnl < a.metrics.net_pnl);
    let c = evaluation::simulate(
        &calendar,
        &ticks,
        1000.,
        5,
        Risk::default(),
        Costs::default(),
        Execution {
            fill_fraction: 0.25,
            participation: 1.,
            ..Execution::default()
        },
    )
    .unwrap();
    assert!(c.metrics.partial_fills > 0);
    assert!(c.metrics.expired_orders > 0);
    let mut no_quotes = ticks.clone();
    for t in &mut no_quotes {
        t.quote = None;
    }
    let c = evaluation::simulate(
        &calendar,
        &no_quotes,
        1000.,
        5,
        Risk::default(),
        Costs::default(),
        Execution::default(),
    )
    .unwrap();
    assert_eq!(c.metrics.net_pnl, 0.);
    assert_eq!(c.metrics.submitted_orders, 0);
    assert_eq!(c.metrics.executable_quote_coverage, 0.);
}
