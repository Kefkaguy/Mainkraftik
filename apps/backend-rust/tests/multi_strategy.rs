use chrono::{Duration, TimeZone, Utc};
use mainkraft::{
    Result,
    calendar::Calendar,
    features::Quote,
    intraday::{data::QuoteSample, features::Candle},
    multistrategy::{
        attribution::{PaperFill, PaperLedger},
        execution::{Book, Filter},
        learning::{Outcome, Selector},
        quotes::Observation,
        regime::{self, Context, Frame, Regime},
        signals::{self, Signal, Strategy},
    },
};
fn context() -> Context {
    let at = Utc.with_ymd_and_hms(2025, 3, 24, 16, 0, 16).unwrap();
    let frames = [1, 5, 15, 60]
        .map(|minutes| Frame {
            minutes,
            close: 105.,
            previous_close: 104.9,
            ema_fast: 104.,
            ema_slow: 102.,
            rsi: 60.,
            macd: 1.,
            macd_signal: 0.5,
            atr: 0.5,
            range_high: 104.5,
            range_low: 100.,
            previous_high: 104.9,
            previous_low: 104.5,
            higher_structure: true,
            lower_structure: false,
            relative_volume: Some(1.5),
            momentum: 0.02,
        })
        .to_vec();
    Context {
        symbol: "AMD".into(),
        at,
        feed: "sip".into(),
        regime: Regime::Bullish,
        directional_regime: Regime::Bullish,
        frames,
        session_vwap: 104.,
        reconstructed_history: true,
    }
}
fn quote(at: chrono::DateTime<Utc>, price: f64) -> Quote {
    Quote {
        bid: price,
        ask: price + 0.01,
        source_time: at,
        acquired_at: at,
        feed: "sip".into(),
    }
}
fn signal() -> Result<Signal> {
    let c = context();
    let q = quote(c.at, 105.);
    Ok(signals::generate(&c, Some(&q), c.at + Duration::hours(4))?
        .into_iter()
        .find_map(|d| {
            (d.strategy == Strategy::TrendFollowing)
                .then_some(d.signal)
                .flatten()
        })
        .expect("trend fixture"))
}
fn observation(at: chrono::DateTime<Utc>, price: f64) -> Observation {
    let sample = |time| QuoteSample {
        symbol: "AMD".into(),
        quote: quote(time, price),
        bid_shares: 50.,
        ask_shares: 100.,
        historical_reconstruction: true,
    };
    Observation {
        plan_id: "fixture-plan".into(),
        at,
        symbol: "AMD".into(),
        before: Some(sample(at - Duration::seconds(2))),
        after: Some(sample(at + Duration::seconds(2))),
        failures: Vec::new(),
    }
}
#[test]
fn causal_complete_timeframes_and_five_distinct_rules() -> Result<()> {
    let calendar = Calendar::embedded()?;
    let mut candles = Vec::new();
    let mut i = 0f64;
    for day in calendar.sessions_between(
        chrono::NaiveDate::from_ymd_opt(2025, 2, 3).unwrap(),
        chrono::NaiveDate::from_ymd_opt(2025, 2, 20).unwrap(),
    )? {
        let s = calendar.session(day).unwrap();
        for minute in 0..390 {
            i += 1.;
            let price = 100. * (1. + 0.00001 * i + 0.00000001 * i * i);
            let start = s.open + Duration::minutes(minute);
            candles.push(Candle {
                symbol: "AMD".into(),
                start,
                minutes: 1,
                open: price,
                high: price + 0.01,
                low: price - 0.01,
                close: price,
                volume: 1000.,
                vwap: Some(price),
                available_at: start + Duration::seconds(61),
                acquired_at: start + Duration::seconds(61),
                feed: "sip".into(),
                historical_availability_assumed: false,
            });
        }
    }
    let at = candles.last().unwrap().start - Duration::hours(2) + Duration::seconds(70);
    let before = regime::detect(&calendar, "AMD", at, &candles, false)?;
    assert_eq!(
        before.frames.iter().map(|f| f.minutes).collect::<Vec<_>>(),
        [1, 5, 15, 60]
    );
    assert_eq!(before.regime, Regime::Bullish);
    let mut flat = candles.clone();
    for c in &mut flat {
        c.open = 100.;
        c.high = 100.01;
        c.low = 99.99;
        c.close = 100.;
        c.vwap = Some(100.);
    }
    assert_eq!(
        regime::detect(&calendar, "AMD", at, &flat, false)?.regime,
        Regime::Sideways
    );
    for c in &mut flat {
        c.high = 102.;
        c.low = 98.;
    }
    assert_eq!(
        regime::detect(&calendar, "AMD", at, &flat, false)?.regime,
        Regime::HighVolatility
    );
    for c in candles.iter_mut().filter(|c| c.end() > at) {
        c.close = 1e8;
        c.high = 1e8;
    }
    assert_eq!(
        serde_json::to_value(&before)?,
        serde_json::to_value(regime::detect(&calendar, "AMD", at, &candles, false)?)?
    );
    let mut c = context();
    let q = quote(c.at, 105.);
    let close = c.at + Duration::hours(4);
    let decisions = signals::generate(&c, Some(&q), close)?;
    for strategy in [
        Strategy::TrendFollowing,
        Strategy::Breakout,
        Strategy::Momentum,
    ] {
        assert!(
            decisions
                .iter()
                .any(|d| d.strategy == strategy && d.signal.is_some())
        );
    }
    c.frames[0].close = 104.;
    c.frames[0].previous_close = 103.9;
    c.frames[0].rsi = 45.;
    assert!(
        signals::generate(&c, Some(&quote(c.at, 104.)), close)?
            .iter()
            .any(|d| d.strategy == Strategy::Pullback && d.signal.is_some())
    );
    c.regime = Regime::Sideways;
    c.frames[0].close = 100.;
    c.frames[0].previous_close = 99.9;
    c.frames[0].rsi = 30.;
    c.session_vwap = 102.;
    assert!(
        signals::generate(&c, Some(&quote(c.at, 100.)), close)?
            .iter()
            .any(|d| d.strategy == Strategy::MeanReversion && d.signal.is_some())
    );
    for regime in [Regime::Bearish, Regime::HighVolatility] {
        c.regime = regime;
        assert!(
            signals::generate(&c, Some(&q), close)?
                .iter()
                .all(|d| d.signal.is_none())
        );
    }
    assert!(
        signals::generate(&context(), None, close)?
            .iter()
            .all(|d| d.signal.is_none())
    );
    Ok(())
}
#[test]
fn partial_fills_recover_once_and_exit_when_ml_is_unavailable() -> Result<()> {
    let signal = signal()?;
    let mut book = Book::new(1000.)?;
    let entry = observation(signal.issued_at, 105.);
    book.monitor(&entry, std::slice::from_ref(&signal), Filter::RulesOnly)?;
    assert_eq!(book.fills.len(), 1);
    assert!(book.equity() <= 1000.);
    book = serde_json::from_slice(&serde_json::to_vec(&book)?)?;
    book.monitor(&entry, std::slice::from_ref(&signal), Filter::RulesOnly)?;
    assert_eq!(book.fills.len(), 1);
    let exit = observation(signal.issued_at + Duration::minutes(5), 103.);
    book.monitor(&exit, &[], Filter::Unavailable)?;
    assert!(book.positions["AMD"].shares > 0.);
    assert_eq!(book.fills.len(), 2);
    let exit2 = observation(signal.issued_at + Duration::minutes(10), 103.);
    book.monitor(&exit2, &[], Filter::Unavailable)?;
    assert!(book.positions.is_empty());
    assert_eq!(book.completed.len(), 1);
    assert!(book.completed[0].net_return < 0.);
    assert!(book.metrics[&Strategy::TrendFollowing].execution_costs > 0.);
    let mut missing = Book::new(1000.)?;
    let mut row = entry;
    row.after = None;
    missing.monitor(&row, &[signal], Filter::RulesOnly)?;
    assert!(missing.fills.is_empty());
    // A first stock's unknown future execution cannot change another stock's
    // same-timestamp allocation, even when the first order does not fill.
    let first = observation(context().at, 105.);
    let mut second = first.clone();
    second.symbol = "MSFT".into();
    for q in [&mut second.before, &mut second.after]
        .into_iter()
        .flatten()
    {
        q.symbol = "MSFT".into();
        q.ask_shares = 10000.;
    }
    let mut a = crate::signal()?;
    a.id = "first".into();
    let mut b = a.clone();
    b.id = "second".into();
    b.symbol = "MSFT".into();
    let run = |mut first: Observation, change: bool| -> Result<f64> {
        if change {
            first.after = None;
        }
        let mut book = Book::new(1000.)?;
        book.prepare(&[first.clone(), second.clone()])?;
        book.monitor(&first, std::slice::from_ref(&a), Filter::RulesOnly)?;
        book.monitor(&second, std::slice::from_ref(&b), Filter::RulesOnly)?;
        Ok(book.positions["MSFT"].shares)
    };
    assert_eq!(run(first.clone(), false)?, run(first, true)?);
    Ok(())
}
#[test]
fn selector_learns_net_strategy_outcomes_and_enforces_maturity_and_support() -> Result<()> {
    let template = signal()?;
    let origin = template.issued_at;
    let rows = |start: chrono::DateTime<Utc>, n| -> Vec<Outcome> {
        (0..n)
            .map(|i| {
                let mut s = template.clone();
                s.id = format!("{start}:{i}");
                s.issued_at = start + Duration::minutes(i * 5);
                s.x = vec![i as f64 % 7., (i % 2) as f64];
                Outcome {
                    known_at: s.issued_at + Duration::minutes(30),
                    signal: s,
                    net_return: if i % 2 == 0 { 0.003 } else { -0.002 },
                    exit_reason: "fixture_only".into(),
                }
            })
            .collect()
    };
    let train = rows(origin, 120);
    let cal = rows(origin + Duration::days(2), 40);
    let model = Selector::fit(&train, &cal)?;
    let mut history = train.clone();
    history.extend(cal.clone());
    let (purged, calibration) = mainkraft::multistrategy::learning::chronological_split(
        &history,
        origin + Duration::days(3),
    );
    assert_eq!(calibration.len(), 30);
    let start = calibration
        .iter()
        .map(|r| r.signal.issued_at)
        .min()
        .unwrap();
    assert!(purged.iter().all(|r| r.known_at < start));
    assert!(Selector::fit(&train, &train[..40]).is_err());
    let mut s = template;
    s.x = vec![1., 0.];
    s.issued_at = origin + Duration::days(3);
    let score = model.score(&s)?;
    assert!((0. ..=1.).contains(&score.probability_net_success));
    s.strategy = Strategy::Breakout;
    assert!(model.score(&s).is_err());
    s.strategy = Strategy::TrendFollowing;
    s.issued_at = origin;
    assert!(model.score(&s).is_err());
    Ok(())
}
#[test]
fn paper_attribution_is_account_isolated_immutable_and_restart_safe() -> Result<()> {
    let mut ledger = PaperLedger::new(["existing-first".into(), "existing-second".into()])?;
    ledger.track_order("existing-first", "buy-1", Strategy::TrendFollowing, "AMD")?;
    ledger.track_order("existing-first", "sell-1", Strategy::TrendFollowing, "AMD")?;
    assert!(
        ledger
            .track_order("existing-second", "buy-1", Strategy::Momentum, "AMD")
            .is_err()
    );
    let fill = PaperFill {
        execution_id: "activity-1".into(),
        order_id: "buy-1".into(),
        account_id: "existing-first".into(),
        symbol: "AMD".into(),
        at: context().at,
        buy: true,
        shares: 2.,
        price: 100.,
        actual_fees: 0.1,
        reference_mid: Some(99.99),
    };
    assert!(ledger.reconcile(fill.clone())?);
    let directory = tempfile::tempdir()?;
    let snapshot = ledger.checkpoint(directory.path())?;
    assert!(
        PaperLedger::restore(
            &snapshot,
            ["wrong-account".into(), "existing-second".into()]
        )
        .is_err()
    );
    ledger = PaperLedger::restore(
        &snapshot,
        ["existing-first".into(), "existing-second".into()],
    )?;
    assert!(!ledger.reconcile(fill.clone())?);
    let mut sell = fill.clone();
    sell.execution_id = "activity-2".into();
    sell.order_id = "sell-1".into();
    sell.buy = false;
    sell.shares = 1.;
    sell.price = 101.;
    sell.at += Duration::minutes(5);
    ledger.reconcile(sell)?;
    let metrics = ledger.performance(
        "existing-first",
        Strategy::TrendFollowing,
        &std::collections::BTreeMap::new(),
    )?;
    assert!((metrics.realized_net_pnl - 0.85).abs() < 1e-9);
    assert_eq!(metrics.open_shares, 1.);
    assert!(metrics.unrealized_net_pnl.is_none());
    let at = context().at + Duration::minutes(6);
    ledger.observe_marks(
        "existing-first",
        Strategy::TrendFollowing,
        at,
        &[("AMD".into(), (100., at))].into_iter().collect(),
    )?;
    let later = at + Duration::minutes(1);
    ledger.observe_marks(
        "existing-first",
        Strategy::TrendFollowing,
        later,
        &[("AMD".into(), (99., later))].into_iter().collect(),
    )?;
    assert!(
        (ledger
            .performance(
                "existing-first",
                Strategy::TrendFollowing,
                &[("AMD".into(), 99.)].into_iter().collect()
            )?
            .marked_maximum_drawdown_dollars
            .unwrap()
            + 1.)
            .abs()
            < 1e-9
    );
    assert!(
        ledger
            .observe_marks(
                "existing-first",
                Strategy::TrendFollowing,
                at,
                &[("AMD".into(), (999., at))].into_iter().collect()
            )
            .is_err()
    );
    let mut conflict = fill;
    conflict.price = 999.;
    assert!(ledger.reconcile(conflict).is_err());
    Ok(())
}
