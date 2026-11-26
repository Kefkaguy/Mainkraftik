use mainkraft::financials;
use serde_json::{Value, json};
#[test]
fn gaap_metrics_select_original_versions_periods_and_sources() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/financial-parity.json")).expect("fixture");
    for (index, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        let rows = case["rows"].as_array().expect("rows");
        let at = case["asof"].as_str().expect("asof").parse().expect("time");
        assert_eq!(
            json!(financials::select_versions(rows, at, false).expect("selection")),
            case["selected"],
            "case {index}"
        );
        assert_eq!(
            json!(financials::select_versions(rows, at, true).expect("observed")),
            case["observed"],
            "case {index}"
        );
        let actual = financials::calculate_metrics(rows, at).expect("metrics");
        let differences = diff("", &actual, &case["metrics"]);
        assert!(
            differences.is_empty(),
            "case {index}: {}",
            differences
                .into_iter()
                .take(8)
                .collect::<Vec<_>>()
                .join("; ")
        );
    }
}
fn diff(path: &str, a: &Value, b: &Value) -> Vec<String> {
    if a == b {
        return vec![];
    }
    if let (Some(a), Some(b)) = (a.as_object(), b.as_object()) {
        return a
            .keys()
            .chain(b.keys())
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .flat_map(|k| {
                diff(
                    &format!("{path}/{k}"),
                    a.get(k).unwrap_or(&Value::Null),
                    b.get(k).unwrap_or(&Value::Null),
                )
            })
            .collect();
    }
    if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
        if a.len() != b.len() {
            return vec![format!("{path}: length {} != {}", a.len(), b.len())];
        }
        return a
            .iter()
            .zip(b)
            .enumerate()
            .flat_map(|(i, (a, b))| diff(&format!("{path}/{i}"), a, b))
            .collect();
    }
    vec![format!("{path}: {a} != {b}")]
}
