use serde_json::Value;
#[test]
fn existing_research_progress_does_not_promote_or_finalize() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures/legacy-progress-parity.json"))
        .expect("fixture");
    for case in fixture["cases"].as_array().expect("cases") {
        let protocol = if case["protocol"].is_null() {
            None
        } else {
            Some(&case["protocol"])
        };
        let existing = if case["existing"].is_null() {
            None
        } else {
            Some(&case["existing"])
        };
        assert_eq!(
            mainkraft::research_api::legacy_progress(
                protocol,
                existing,
                case["rows"].as_array().expect("rows")
            )
            .expect("read-only progress"),
            case["expected"]
        );
    }
}
