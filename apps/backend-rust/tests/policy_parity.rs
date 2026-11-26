use chrono::{DateTime, NaiveDate, Utc};
use mainkraft::{
    allocation::{self, Observation},
    calendar::Calendar,
    features::{self, Bar},
};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/policy-parity.json")).expect("oracle")
}
#[test]
fn portfolio_policy_including_seeded_exploration_matches_python() {
    for case in fixture()["allocations"].as_array().expect("cases") {
        let rows: Vec<Observation> =
            serde_json::from_value(case["observations"].clone()).expect("observations");
        let correction: Option<Vec<f64>> =
            serde_json::from_value(case["correction"].clone()).expect("correction");
        let targets = allocation::allocate(
            &rows,
            case["capacity"].as_f64().expect("capacity"),
            case["adaptive"].as_bool().expect("adaptive"),
            case["slot"].as_str().expect("slot"),
            correction.as_deref(),
        )
        .expect("allocation");
        assert_eq!(
            targets.len(),
            case["expected"].as_object().expect("expected").len()
        );
        for (symbol, target) in targets {
            let expected = &case["expected"][&symbol];
            let value = expected["target_value"].as_f64().expect("value");
            assert!(
                (target.target_value - value).abs() <= 1e-8 + value.abs() * 1e-12,
                "allocation {symbol}"
            );
            assert!(
                (target.forecast_return - expected["forecast_return"].as_f64().expect("return"))
                    .abs()
                    < 1e-14
            );
            assert_eq!(target.asset, expected["asset"]);
        }
    }
}
#[test]
fn decimal_shares_and_close_before_reversal_match_python() {
    for case in fixture()["quantities"].as_array().expect("cases") {
        let actual = allocation::quantity_change(
            case["current"].as_str(),
            case["target"].as_f64().expect("target"),
            case["price"].as_f64().expect("price"),
            case["fractionable"].as_bool().expect("fractionable"),
            case["shortable"].as_bool().expect("shortable"),
        )
        .expect("change");
        assert_eq!(
            serde_json::to_value(actual).expect("JSON"),
            case["expected"],
            "case {case}"
        );
    }
}
#[test]
fn exchange_targets_match_through_holidays_early_close_and_dst() {
    let calendar = Calendar::embedded().expect("calendar");
    for case in fixture()["targets"].as_array().expect("cases") {
        let instant: DateTime<Utc> = case["instant"]
            .as_str()
            .expect("instant")
            .parse()
            .expect("timestamp");
        let expected: DateTime<Utc> = case["expected"]
            .as_str()
            .expect("expected")
            .parse()
            .expect("timestamp");
        assert_eq!(calendar.target_time(instant).expect("target"), expected);
    }
}
#[test]
fn causal_daily_features_match_numpy() {
    let calendar = Calendar::embedded().expect("calendar");
    let case = &fixture()["features"];
    let own: Vec<Bar> = serde_json::from_value(case["own"].clone()).expect("own");
    let spy: Vec<Bar> = serde_json::from_value(case["spy"].clone()).expect("spy");
    let tech: Vec<Bar> = serde_json::from_value(case["tech"].clone()).expect("tech");
    let session: NaiveDate = case["session"]
        .as_str()
        .expect("session")
        .parse()
        .expect("date");
    let actual = features::features(&calendar, session, &own, &spy, &tech).expect("features");
    for (a, e) in actual
        .x
        .iter()
        .zip(case["expected"]["x"].as_array().expect("expected"))
    {
        assert!((a - e.as_f64().expect("feature")).abs() < 1e-12);
    }
    let mut poisoned = own.clone();
    poisoned.last_mut().expect("last").close = 1e9;
    assert_eq!(
        features::features(&calendar, session, &poisoned, &spy, &tech)
            .expect("causal")
            .x,
        actual.x
    );
}
