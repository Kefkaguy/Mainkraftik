use mainkraft::forecasting;
use serde_json::Value;
#[test]
fn original_baseline_and_decimal_outcome_arithmetic() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/forecast-statistics.json")).expect("oracle");
    for case in f["baselines"].as_array().expect("cases") {
        let samples: Vec<f64> = case["samples"]
            .as_array()
            .expect("samples")
            .iter()
            .map(|v| v.as_f64().expect("value"))
            .collect();
        let actual = forecasting::baseline(&samples);
        if case.get("error").is_some() {
            assert!(
                matches!(actual,Err(mainkraft::Error::Invalid(reason)) if reason==case["error"].as_str().expect("error"))
            );
        } else {
            let actual = actual.expect("baseline");
            let expected = &case["prediction"];
            for key in [
                "expected_return",
                "probability_positive",
                "lower_return",
                "upper_return",
            ] {
                let a = actual[key].as_f64().expect("value");
                let b = expected[key].as_f64().expect("value");
                assert!(
                    (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs())),
                    "{key}: {a} != {b}"
                );
            }
            assert_eq!(actual["sample_count"], expected["sample_count"]);
            assert_eq!(actual["interval_method"], expected["interval_method"]);
        }
    }
    for case in f["returns"].as_array().expect("returns") {
        let actual = forecasting::decimal_return(
            case["close"].as_str().expect("close"),
            case["open"].as_str().expect("open"),
        )
        .expect("return");
        let expected = case["return"].as_f64().expect("return");
        assert_eq!(actual.to_bits(), expected.to_bits(), "{case}");
    }
    assert!(forecasting::baseline(&[f64::NAN; 252]).is_err());
    assert!(forecasting::decimal_return("1", "0").is_err());
}
