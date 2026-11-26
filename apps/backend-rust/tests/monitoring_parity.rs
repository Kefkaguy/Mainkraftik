use mainkraft::{calendar::Calendar, monitoring};
use serde_json::{Value, json};
fn compare(a: &Value, b: &Value, path: &str) {
    if a == b {
        return;
    }
    if let (Some(a), Some(b)) = (a.as_f64(), b.as_f64()) {
        assert!(
            (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs())),
            "{path}: {a} != {b}"
        );
        return;
    }
    if let (Some(a), Some(b)) = (a.as_object(), b.as_object()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (key, a) in a {
            compare(a, b.get(key).expect("same field"), &format!("{path}/{key}"));
        }
        return;
    }
    if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (i, (a, b)) in a.iter().zip(b).enumerate() {
            compare(a, b, &format!("{path}/{i}"));
        }
        return;
    }
    assert_eq!(a, b, "{path}");
}
#[test]
fn descriptive_risk_reliability_calibration_and_issuance_match_python() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/monitoring-parity.json")).expect("fixture");
    let calendar = Calendar::embedded().expect("calendar");
    for (index, case) in fixture["risk"].as_array().expect("risk").iter().enumerate() {
        let bars = case["bars"].as_array().expect("bars");
        assert_eq!(
            json!(monitoring::continuity(&calendar, bars).expect("continuity")),
            case["continuity"]
        );
        compare(
            &monitoring::risk_summary(&calendar, bars).expect("risk"),
            &case["risk"],
            &format!("risk/{index}"),
        );
    }
    for (index, case) in fixture["metrics"]
        .as_array()
        .expect("metrics")
        .iter()
        .enumerate()
    {
        let values = |key: &str| {
            case[key]
                .as_array()
                .expect("array")
                .iter()
                .map(|v| v.as_f64().expect("number"))
                .collect::<Vec<_>>()
        };
        compare(
            &monitoring::metrics(
                &values("y"),
                &values("expected"),
                &values("p"),
                Some((&values("lower"), &values("upper"))),
            )
            .expect("metrics"),
            &case["metrics"],
            &format!("metrics/{index}"),
        );
    }
    compare(
        &json!(
            monitoring::reliability(fixture["history"].as_array().expect("history"))
                .expect("reliability")
        ),
        &fixture["reliability"],
        "reliability",
    );
    for case in fixture["issuance"].as_array().expect("issuance") {
        let (day, reason) = calendar
            .issuance_window(
                case["instant"]
                    .as_str()
                    .expect("instant")
                    .parse()
                    .expect("time"),
            )
            .expect("window");
        assert_eq!(
            json!([day.map(|d| d.to_string()), reason]),
            case["expected"]
        );
    }
    assert!(monitoring::metrics(&[], &[], &[], None).is_err());
    assert!(monitoring::metrics(&[0.0], &[0.0], &[1.1], None).is_err());
    assert!(monitoring::metrics(&[0.0], &[f64::NAN], &[0.5], None).is_err());
    assert!(monitoring::metrics(&[0.0], &[0.0], &[0.5], Some((&[0.1], &[-0.1]))).is_err());
}
