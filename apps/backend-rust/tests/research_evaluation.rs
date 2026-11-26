use mainkraft::{
    calendar::Calendar,
    research_evaluation::{build_panel, evaluate},
};
use serde_json::{Value, json};
fn compare(path: &str, a: &Value, b: &Value) {
    if a == b {
        return;
    }
    if a.is_i64() && b.is_i64() {
        assert_eq!(a, b, "{path}");
        return;
    }
    if let (Some(a), Some(b)) = (a.as_f64(), b.as_f64()) {
        assert!((a - b).abs() <= 5e-9 * (1. + b.abs()), "{path}: {a} != {b}");
        return;
    }
    if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (i, (a, b)) in a.iter().zip(b).enumerate() {
            compare(&format!("{path}/{i}"), a, b);
        }
        return;
    }
    if let (Some(a), Some(b)) = (a.as_object(), b.as_object()) {
        assert_eq!(a.len(), b.len(), "{path}");
        for (k, b) in b {
            compare(
                &format!("{path}/{k}"),
                a.get(k).unwrap_or_else(|| panic!("{path}/{k} missing")),
                b,
            );
        }
        return;
    }
    assert_eq!(a, b, "{path}");
}
#[test]
fn fixed_protocol_panel_purging_training_and_bootstrap_match_original() {
    let fixture: Value = serde_json::from_str(if cfg!(target_os = "linux") {
        include_str!("fixtures/research-evaluation-linux.json")
    } else {
        include_str!("fixtures/research-evaluation.json")
    })
    .unwrap();
    let calendar = Calendar::embedded().unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let bars = case["bars"].as_array().unwrap();
        let spy = case["spy"].as_array().unwrap();
        let expected = &case["expected"];
        if let Some(error) = expected["error"].as_str() {
            match evaluate(&calendar, bars, spy, "MSI").unwrap_err() {
                mainkraft::Error::Invalid(s) => assert_eq!(s, error, "{}", case["name"]),
                _ => panic!("unexpected error"),
            }
        } else {
            let panel = build_panel(&calendar, bars, spy).unwrap();
            let panel: Vec<_> = panel
                .iter()
                .map(|r| json!({"date":r.date,"index":r.index,"x":r.x}))
                .collect();
            compare(
                &format!("{}/panel", case["name"]),
                &json!(panel),
                &expected["panel"],
            );
            let report = evaluate(&calendar, bars, spy, "MSI").unwrap();
            compare(
                &format!("{}/report", case["name"]),
                &report,
                &expected["report"],
            );
        }
    }
}
