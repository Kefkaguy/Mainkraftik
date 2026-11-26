use mainkraft::news;
use serde_json::{Value, json};
#[test]
fn sourced_context_budget_and_input_order_match_python() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/news-parity.json")).expect("fixture");
    for case in fixture["contexts"].as_array().expect("contexts") {
        let context = news::research_context(
            &case["candidates"],
            &case["articles"],
            if case["sentiment"].is_null() {
                None
            } else {
                Some(&case["sentiment"])
            },
        )
        .expect("context");
        assert_eq!(context.content, case["content"].as_str().expect("content"));
        assert_eq!(context.metadata, case["metadata"]);
        assert!(context.content.len() <= 12000);
        assert!(!context.content.contains("must-not-be-forwarded"));
    }
}
#[test]
fn strict_schema_citations_and_duplicate_rejections_match_python() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/news-parity.json")).expect("fixture");
    for case in fixture["filters"].as_array().expect("cases") {
        let actual = match news::filter_output(
            &case["output"],
            &case["candidates"],
            &case["articles"],
            &case["presented"],
        ) {
            Ok((accepted, rejections)) => json!({"accepted":accepted,"rejections":rejections}),
            Err(error) => json!({"error":error.message,"rejections":error.rejections}),
        };
        assert_eq!(actual, case["expected"]);
    }
    for case in fixture["provenance"].as_array().expect("cases") {
        assert_eq!(news::model_provenance(&case["input"]), case["expected"]);
    }
}
