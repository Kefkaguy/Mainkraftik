use mainkraft::ranking;
use serde_json::{Value, json};

#[test]
fn request_hash_prompt_order_and_response_validation_match_python() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/ranking-parity.json")).expect("fixture");
    let request = ranking::prepare(
        "fixture-model",
        &fixture["candidates"],
        &fixture["articles"],
        None,
    )
    .expect("request");
    assert_eq!(request.body, fixture["body"]);
    for case in fixture["cases"].as_array().expect("cases") {
        let parsed = ranking::parse_response(
            &request,
            &fixture["candidates"],
            &fixture["articles"],
            &serde_json::to_vec(&case["response"]).expect("response"),
        );
        let result = match parsed.result {
            Ok(output) => json!({"picks":output.picks,"metadata":output.metadata}),
            Err(error) => json!({"error":error.message,"rejections":error.rejections}),
        };
        assert_eq!(result, case["expected"]);
        let events = parsed
            .event
            .map(|event| vec![json!([event.kind, event.payload])])
            .unwrap_or_default();
        assert_eq!(json!(events), case["events"]);
    }
    let parsed = ranking::parse_response(
        &request,
        &fixture["candidates"],
        &fixture["articles"],
        b"not json SECRET",
    );
    assert_eq!(
        parsed.result.err().expect("rejection").rejections,
        vec![json!({"reason":"malformed_provider_json"})]
    );
    assert!(
        !parsed
            .event
            .expect("event")
            .payload
            .to_string()
            .contains("SECRET")
    );
}
