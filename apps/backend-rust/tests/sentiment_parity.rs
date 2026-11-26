use mainkraft::{canonical, sentiment};
use serde_json::{Value, json};
#[test]
fn sourced_text_hashes_and_sentiment_aggregation_match_python() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/sentiment-parity.json")).expect("fixture");
    let input = sentiment::prepare(&f["candidates"], &f["articles"]).expect("input");
    assert_eq!(json!(input.items), f["items"]);
    let mut evidence = sentiment::validate_and_aggregate(
        &f["candidates"],
        &f["articles"],
        f["source_id"].as_str().expect("source"),
        &input,
        &f["response"],
        f["at"].as_str().expect("timestamp"),
    )
    .expect("validate");
    let id = canonical::digest(&json!(["*", "hf_sentiment", evidence])).expect("identity");
    evidence["source_id"] = json!(id);
    assert_eq!(evidence, f["expected"]);
    let mut corrupt = f["response"].clone();
    corrupt["scores"][0]["text_hash"] = json!("wrong");
    assert!(
        sentiment::validate_and_aggregate(
            &f["candidates"],
            &f["articles"],
            "source",
            &input,
            &corrupt,
            "time"
        )
        .is_err()
    );
    corrupt = f["response"].clone();
    corrupt["model"]["revision"] = json!("unapproved");
    assert!(
        sentiment::validate_and_aggregate(
            &f["candidates"],
            &f["articles"],
            "source",
            &input,
            &corrupt,
            "time"
        )
        .is_err()
    );
}
