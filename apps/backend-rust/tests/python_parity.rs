use mainkraft::{
    canonical, learning,
    models::{Forecast, Network, NumericModel},
};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/python-parity.json"))
        .expect("Python oracle fixture")
}
fn close(actual: f64, expected: f64, context: &str) {
    let bound = 1e-11 + expected.abs() * 1e-10;
    assert!(
        actual.is_finite() && (actual - expected).abs() <= bound,
        "{context}: actual={actual:e}, expected={expected:e}, bound={bound:e}"
    );
}

#[test]
fn frozen_and_synthetic_numeric_models_match_python() {
    for case in fixture()["numeric"].as_array().expect("cases") {
        let model: NumericModel =
            serde_json::from_value(case["artifact"].clone()).expect("artifact");
        for (input, expected) in case["inputs"]
            .as_array()
            .expect("inputs")
            .iter()
            .zip(case["expected"].as_array().expect("predictions"))
        {
            let row: Vec<Option<f64>> = serde_json::from_value(input.clone()).expect("row");
            let p = model.predict(&row).expect("prediction");
            let e: Forecast = serde_json::from_value(expected.clone()).expect("expected");
            close(p.expected_net_return, e.expected_net_return, "mean");
            close(
                p.probability_positive,
                e.probability_positive,
                "calibration",
            );
            close(p.lower_return, e.lower_return, "interval lower");
            close(p.upper_return, e.upper_return, "interval upper");
        }
    }
}

#[test]
fn residual_ridge_matches_numpy_solve() {
    for case in fixture()["learning"].as_array().expect("cases") {
        let rows: Vec<learning::Outcome> =
            serde_json::from_value(case["rows"].clone()).expect("outcomes");
        let actual = learning::fit(&rows).expect("fit");
        let expected: Vec<f64> =
            serde_json::from_value(case["expected"].clone()).expect("expected");
        for (a, e) in actual.into_iter().zip(expected) {
            close(a, e, "residual coefficient");
        }
    }
}

#[test]
fn historical_bundle_and_record_identities_match_python() {
    for case in fixture()["canonical"].as_array().expect("cases") {
        if let Some(expected) = case["expected"].as_str() {
            assert_eq!(
                canonical::canonical(&case["value"]).expect("canonical"),
                expected
            );
        }
        let expected = case["digest"]
            .as_str()
            .or_else(|| case["expected_digest"].as_str())
            .expect("hash");
        assert_eq!(canonical::digest(&case["value"]).expect("digest"), expected);
    }
}

#[test]
fn frozen_network_forward_does_not_substitute_a_model() {
    for case in fixture()["neural_forward"].as_array().expect("cases") {
        let network: Network = serde_json::from_value(case["network"].clone()).expect("network");
        let inputs: Vec<Vec<f64>> = serde_json::from_value(case["inputs"].clone()).expect("inputs");
        let expected: Vec<Vec<f64>> =
            serde_json::from_value(case["expected"].clone()).expect("expected");
        for (x, ys) in inputs.iter().zip(expected) {
            for (a, e) in network
                .forward(x, case["logistic"].as_bool().expect("flag"))
                .expect("forward")
                .into_iter()
                .zip(ys)
            {
                close(a, e, "network");
            }
        }
    }
}
