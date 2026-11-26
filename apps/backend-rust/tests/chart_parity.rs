use base64::{Engine, engine::general_purpose::STANDARD};
use mainkraft::{calendar::Calendar, charts};
use serde_json::Value;
use sha2::{Digest, Sha256};
#[test]
fn original_chart_pixels_png_and_archived_manifest_identities_match() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/chart-parity.json")).expect("fixture");
    let calendar = Calendar::embedded().expect("calendar");
    for case in fixture["cases"].as_array().expect("cases") {
        let result = charts::render(
            &calendar,
            case["bars"].as_array().expect("bars"),
            case["cutoff"].as_str().expect("cutoff"),
        )
        .expect("render");
        assert_eq!(
            hex::encode(Sha256::digest(&result.pixels)),
            case["pixel_hash"],
            "pixels {}",
            case["scenario"]
        );
        assert_eq!(
            result.filtered_hash, case["filtered_hash"],
            "PNG filters {}",
            case["scenario"]
        );
        assert_eq!(
            result.image_hash, case["image_hash"],
            "PNG {}",
            case["scenario"]
        );
        assert_eq!(
            result.png,
            STANDARD
                .decode(case["png"].as_str().expect("PNG"))
                .expect("base64")
        );
        assert_eq!(result.manifest, case["manifest"]);
        assert_eq!(result.input_hash, case["input_hash"]);
    }
    for case in fixture["invalid"].as_array().expect("invalid") {
        let error = charts::render(
            &calendar,
            case["bars"].as_array().expect("bars"),
            case["cutoff"].as_str().expect("cutoff"),
        )
        .err()
        .expect("refused");
        match error {
            mainkraft::Error::Invalid(message) => assert_eq!(message, case["error"]),
            _ => panic!("unexpected error class"),
        }
    }
}

#[test]
fn archived_detection_annotation_font_pixels_and_png_match_original_route() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/annotation-parity.json")).expect("annotations");
    let base: Value =
        serde_json::from_str(include_str!("fixtures/chart-parity.json")).expect("base");
    let base = &base["cases"][0];
    let calendar = Calendar::embedded().expect("calendar");
    let edge: Value = serde_json::from_str(include_str!("fixtures/annotation-edge-parity.json"))
        .expect("edge cases");
    for (index, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .chain(edge["cases"].as_array().expect("edges"))
        .enumerate()
    {
        let mut chart = charts::render(
            &calendar,
            base["bars"].as_array().expect("bars"),
            base["cutoff"].as_str().expect("cutoff"),
        )
        .expect("base");
        let png = charts::annotate(&mut chart.pixels, &case["boxes"]).expect("annotations");
        assert_eq!(
            hex::encode(Sha256::digest(&chart.pixels)),
            case["pixel_hash"],
            "annotation pixels case {index}"
        );
        assert_eq!(
            hex::encode(Sha256::digest(&png)),
            case["image_hash"],
            "annotation PNG case {index}"
        );
        assert_eq!(
            png,
            STANDARD
                .decode(case["png"].as_str().expect("PNG"))
                .expect("base64")
        );
    }
    for case in fixture["validation"].as_array().expect("validation") {
        let actual = match charts::validate_boxes(&case["boxes"]) {
            Ok(_) => serde_json::json!({"accepted":true}),
            Err(mainkraft::Error::Invalid(error)) => serde_json::json!({"error":error}),
            _ => panic!("unexpected validation error"),
        };
        assert_eq!(actual, case["expected"]);
    }
}
