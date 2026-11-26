use mainkraft::{
    Error,
    calendar::Calendar,
    point_features::{self, Context},
};
use serde_json::{Value, json};
#[test]
fn original_causal_information_features_numeric_values_lineage_and_exact_snapshot_ids() {
    #[cfg(target_os = "linux")]
    let raw = include_str!("fixtures/point-features-linux.json");
    #[cfg(not(target_os = "linux"))]
    let raw = include_str!("fixtures/point-features.json");
    let f: Value = serde_json::from_str(raw).expect("oracle");
    let calendar = Calendar::embedded().expect("calendar");
    assert_eq!(point_features::VERSION, f["version"]);
    assert_eq!(json!(point_features::NAMES.as_slice()), f["names"]);
    for (group, expected) in f["groups"].as_object().expect("groups") {
        assert_eq!(
            json!(point_features::columns(group).expect("columns")),
            *expected
        );
    }
    let mut failures = Vec::new();
    for case in f["information"].as_array().expect("information") {
        let context:Context=serde_json::from_value(json!({"streams":{},"information":case["information"],"coverage":case["coverage"],"facts":case["facts"],"events":case["events"]})).expect("context");
        let (values, lineage) = point_features::information_values(
            case["symbol"].as_str().expect("symbol"),
            case["cutoff"]
                .as_str()
                .expect("cutoff")
                .parse()
                .expect("time"),
            &context,
            case["observed"].as_bool().expect("observed"),
        )
        .expect("features");
        if json!(values) != case["values"] || lineage != case["lineage"] {
            failures.push(json!({"information":case["name"],"observed":case["observed"],"expected_values":case["values"],"actual_values":values,"expected_lineage":case["lineage"],"actual_lineage":lineage}));
        }
    }
    #[cfg(target_os = "linux")]
    let raw = include_str!("fixtures/point-feature-boundaries-linux.json");
    #[cfg(not(target_os = "linux"))]
    let raw = include_str!("fixtures/point-feature-boundaries.json");
    let boundaries: Value = serde_json::from_str(raw).expect("boundary oracle");
    for corpus in [&f, &boundaries] {
        for case in corpus["build"].as_array().expect("build") {
            let context: Context = serde_json::from_value(
                corpus["contexts"][case["context"].as_u64().expect("index") as usize].clone(),
            )
            .expect("context");
            let observed = case["observed"].as_bool().expect("observed");
            let symbol = case["symbol"].as_str().expect("symbol");
            match point_features::build_features(
                &calendar,
                &context,
                symbol,
                observed,
                case["latest_only"].as_bool().expect("latest"),
            ) {
                Ok(rows) => {
                    let actual = json!(rows);
                    let ids: Vec<_> = rows
                        .iter()
                        .map(|r| {
                            r.identity(
                                symbol,
                                if observed {
                                    "prospective"
                                } else {
                                    "historical"
                                },
                            )
                            .expect("identity")
                        })
                        .collect();
                    if actual != case["rows"] || json!(ids) != case["ids"] {
                        failures.push(json!({"context":case["context"],"observed":observed,"expected":case["rows"],"actual":actual,"expected_ids":case["ids"],"actual_ids":ids}));
                    }
                }
                Err(Error::Invalid(message)) if case.get("error").is_some() => {
                    assert_eq!(message, case["error"]["message"])
                }
                other => panic!("unexpected feature outcome for {case}: {other:?}"),
            }
        }
    }
    if let Ok(path) = std::env::var("RUST_POINT_FAILURES") {
        std::fs::write(path, serde_json::to_vec_pretty(&failures).expect("JSON"))
            .expect("failure evidence");
    }
    assert!(
        failures.is_empty(),
        "{} causal feature discrepancies; first {}",
        failures.len(),
        failures.first().unwrap_or(&Value::Null)
    );
}
