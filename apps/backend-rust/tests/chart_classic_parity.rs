use base64::{Engine, engine::general_purpose::STANDARD};
use mainkraft::{
    calendar::Calendar,
    charts::{self, Compression},
};
use serde_json::Value;
use sha2::{Digest, Sha256};

#[test]
fn classic_linux_pngs_pixels_filters_and_manifest_identities_match_original() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/chart-parity-linux.json")).unwrap();
    assert_eq!(fixture["zlib"], "1.3");
    let calendar = Calendar::embedded().unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let chart = charts::render_with(
            &calendar,
            case["bars"].as_array().unwrap(),
            case["cutoff"].as_str().unwrap(),
            Compression::Classic13,
        )
        .unwrap();
        assert_eq!(
            hex::encode(Sha256::digest(&chart.pixels)),
            case["pixel_hash"],
            "{}",
            case["scenario"]
        );
        assert_eq!(chart.filtered_hash, case["filtered_hash"]);
        assert_eq!(chart.image_hash, case["image_hash"]);
        assert_eq!(
            chart.png,
            STANDARD.decode(case["png"].as_str().unwrap()).unwrap()
        );
        assert_eq!(chart.manifest, case["manifest"]);
        assert_eq!(chart.input_hash, case["input_hash"]);
    }
    for case in fixture["invalid"].as_array().unwrap() {
        match charts::render_with(
            &calendar,
            case["bars"].as_array().unwrap(),
            case["cutoff"].as_str().unwrap(),
            Compression::Classic13,
        )
        .err()
        .unwrap()
        {
            mainkraft::Error::Invalid(message) => assert_eq!(message, case["error"]),
            _ => panic!("unexpected error"),
        }
    }
}

#[test]
fn classic_linux_annotations_match_original_route_with_full_glyph_and_edge_cases() {
    let base: Value =
        serde_json::from_str(include_str!("fixtures/chart-parity-linux.json")).unwrap();
    let annotation: Value =
        serde_json::from_str(include_str!("fixtures/annotation-parity-linux.json")).unwrap();
    let edges: Value =
        serde_json::from_str(include_str!("fixtures/annotation-edge-parity-linux.json")).unwrap();
    let case = &base["cases"][0];
    let calendar = Calendar::embedded().unwrap();
    for (i, expected) in annotation["cases"]
        .as_array()
        .unwrap()
        .iter()
        .chain(edges["cases"].as_array().unwrap())
        .enumerate()
    {
        let mut chart = charts::render_with(
            &calendar,
            case["bars"].as_array().unwrap(),
            case["cutoff"].as_str().unwrap(),
            Compression::Classic13,
        )
        .unwrap();
        let png = charts::annotate_with(
            &mut chart.pixels,
            &expected["boxes"],
            Compression::Classic13,
        )
        .unwrap();
        assert_eq!(
            hex::encode(Sha256::digest(&chart.pixels)),
            expected["pixel_hash"],
            "pixels {i}"
        );
        assert_eq!(
            hex::encode(Sha256::digest(&png)),
            expected["image_hash"],
            "encoded {i}"
        );
        assert_eq!(
            png,
            STANDARD.decode(expected["png"].as_str().unwrap()).unwrap()
        );
    }
}
