#![cfg(feature = "sentiment-inference")]
use mainkraft::sentiment_worker::{Batch, Inference};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::PathBuf;

#[test]
#[ignore = "requires scripts/test-rust-sentiment.py pinned frozen artifacts/runtime"]
fn native_frozen_worker_matches_original_python_tokens_logits_and_scores() {
    let root = PathBuf::from(std::env::var("RUST_SENTIMENT_MODEL_DIR").expect("model root"));
    let dylib = PathBuf::from(std::env::var("RUST_SENTIMENT_LIBRARY").expect("runtime library"));
    let fixture: Value = if let Ok(path) = std::env::var("RUST_SENTIMENT_REFERENCE") {
        serde_json::from_slice(&std::fs::read(path).expect("original reference")).expect("fixture")
    } else {
        serde_json::from_str(include_str!("fixtures/sentiment-inference-parity.json"))
            .expect("fixture")
    };
    assert_eq!(
        hex::encode(Sha256::digest(std::fs::read(&dylib).expect("runtime"))),
        fixture["runtime_library_sha256"]
    );
    let mut inference = Inference::load(&root, &dylib).expect("frozen inference");
    assert_eq!(inference.health(), fixture["health"]);
    let mut max_logit_error = 0.0f64;
    let mut max_probability_error = 0.0f64;
    for case in fixture["cases"].as_array().expect("cases") {
        let batch: Batch = serde_json::from_value(json!({"items":case["items"]})).expect("batch");
        for (items, chunk) in batch
            .items
            .chunks(4)
            .zip(case["chunks"].as_array().expect("chunks"))
        {
            assert_eq!(
                serde_json::to_value(inference.encode(items).expect("tokens")).expect("encoding"),
                chunk["encoding"]
            );
            for (actual, expected) in inference
                .logits(items)
                .expect("logits")
                .iter()
                .zip(chunk["logits"].as_array().expect("logits"))
            {
                for (a, b) in actual.iter().zip(expected.as_array().expect("row")) {
                    let error = (f64::from(*a) - b.as_f64().expect("number")).abs();
                    max_logit_error = max_logit_error.max(error);
                    assert!(
                        error <= 1e-6,
                        "native/Python raw logit discrepancy: {error}"
                    );
                }
            }
        }
        let actual = inference.score(&batch).expect("scores");
        assert_eq!(actual["model"], case["expected"]["model"]);
        assert_eq!(actual["meaning"], case["expected"]["meaning"]);
        for (actual, expected) in actual["scores"]
            .as_array()
            .expect("scores")
            .iter()
            .zip(case["expected"]["scores"].as_array().expect("scores"))
        {
            assert_eq!(actual["id"], expected["id"]);
            assert_eq!(actual["text_hash"], expected["text_hash"]);
            for label in ["negative", "neutral", "positive"] {
                let error = (actual["scores"][label].as_f64().expect("score")
                    - expected["scores"][label].as_f64().expect("expected"))
                .abs();
                max_probability_error = max_probability_error.max(error);
                assert!(
                    error <= 2e-6,
                    "native/Python probability discrepancy: {error}"
                );
            }
        }
    }
    let duplicate: Batch = serde_json::from_value(
        json!({"items":[{"id":"same","text":"one"},{"id":"same","text":"two"}]}),
    )
    .expect("batch");
    assert!(
        inference
            .score(&duplicate)
            .expect_err("duplicates")
            .to_string()
            .contains("Duplicate article IDs")
    );
    assert!(inference.score(&Batch { items: vec![] }).is_err());
    println!("FROZEN_TOKEN_IDS_MASKS_AND_BATCH_PADDING_EXACT=PASS");
    println!("MAX_RAW_LOGIT_ERROR={max_logit_error}");
    println!("MAX_FLOAT32_SOFTMAX_PROBABILITY_ERROR={max_probability_error}");
    println!("SAME_MODEL_RUNTIME_MANIFEST_AND_TEXT_HASHES=PASS");
}
