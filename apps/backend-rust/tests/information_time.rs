use mainkraft::information::information_stamp;
use serde_json::{Value, json};
#[test]
fn original_python_information_timestamp_acceptance_precision_and_errors() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures/information-time.json"))
        .expect("frozen Python oracle");
    let boundaries: Value =
        serde_json::from_str(include_str!("fixtures/information-time-boundaries.json"))
            .expect("frozen Python boundary oracle");
    let mut failures = Vec::new();
    for case in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .chain(boundaries["cases"].as_array().expect("boundaries"))
    {
        let text = case["input"].as_str().expect("input");
        let actual = match information_stamp(text) {
            Ok(time) => {
                json!({"input":text,"result":time.to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"micros":time.timestamp_micros()})
            }
            Err(error) => json!({"input":text,"kind":error.kind,"message":error.message}),
        };
        if &actual != case {
            failures.push(json!({"expected":case,"actual":actual}));
        }
    }
    if let Ok(path) = std::env::var("RUST_INFORMATION_TIME_FAILURES") {
        std::fs::write(path, serde_json::to_vec_pretty(&failures).expect("JSON"))
            .expect("failure evidence");
    }
    assert!(
        failures.is_empty(),
        "{} timestamp discrepancies; first: {}",
        failures.len(),
        failures.first().unwrap_or(&Value::Null)
    );
}

#[test]
fn overflowing_publication_metadata_keeps_original_uncaught_error() {
    let html = "<meta name=press_release_publish_date content='0001-01-01T00:00:00+01:00'>";
    let acquired = "2026-11-20T12:00:00Z".parse().expect("time");
    for result in [
        mainkraft::official_ir::publication_metadata(html).map(|_| ()),
        mainkraft::official_ir::article_metadata(
            "AXON",
            "https://investor.axon.com/2026-11-18-fixture",
            "fixture",
            html,
            acquired,
        )
        .map(|_| ()),
    ] {
        assert!(
            matches!(result,Err(mainkraft::Error::Reference{kind:"OverflowError",message}) if message=="date value out of range")
        );
    }
}
