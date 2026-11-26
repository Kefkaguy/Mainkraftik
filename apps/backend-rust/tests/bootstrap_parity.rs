use mainkraft::{numpy_rng::Pcg64, statistics};
use serde_json::{Value, json};
#[test]
fn original_bounded_integer_streams_mixed_draws_and_paired_bootstrap() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/bootstrap-parity.json")).expect("oracle");
    for case in f["integer_streams"].as_array().expect("streams") {
        let mut rng = Pcg64::new(20261112);
        let bound = case["bound"].as_u64().expect("bound");
        for value in case["values"].as_array().expect("values") {
            assert_eq!(
                rng.bounded_u64(bound).expect("draw"),
                value.as_u64().expect("integer"),
                "bound {bound}"
            );
        }
        assert_eq!(
            json!(rng.next_u64()),
            case["next_raw"],
            "state bound {bound}"
        );
    }
    let mut rng = Pcg64::new(42);
    for case in f["mixed"].as_array().expect("mixed") {
        for expected in case["values"].as_array().expect("values") {
            match case["kind"].as_str().expect("kind") {
                "integers" => assert_eq!(
                    json!(
                        rng.bounded_u64(case["bound"].as_u64().expect("bound"))
                            .expect("draw")
                    ),
                    *expected
                ),
                "raw" => assert_eq!(json!(rng.next_u64()), *expected),
                "normal" => {
                    assert!((rng.normal() - expected.as_f64().expect("normal")).abs() < 1e-12)
                }
                _ => panic!("kind"),
            }
        }
    }
    let mut failures = Vec::new();
    for (index, case) in f["intervals"]
        .as_array()
        .expect("intervals")
        .iter()
        .enumerate()
    {
        let dates =
            serde_json::from_value::<Vec<chrono::NaiveDate>>(case["dates"].clone()).expect("dates");
        let differences =
            serde_json::from_value::<Vec<f64>>(case["differences"].clone()).expect("differences");
        let actual = statistics::paired_block_interval(
            &dates,
            &differences,
            case["samples"].as_u64().expect("samples") as usize,
            case["block"].as_u64().expect("block") as usize,
            case["confidence"].as_f64().expect("confidence"),
        )
        .expect("interval");
        if json!(actual) != case["expected"] {
            failures.push(json!({"case":index,"actual":actual,"expected":case["expected"]}));
        }
    }
    if let Ok(path) = std::env::var("RUST_BOOTSTRAP_FAILURES") {
        std::fs::write(path, serde_json::to_vec_pretty(&failures).expect("JSON"))
            .expect("failure evidence");
    }
    assert!(failures.is_empty(), "{failures:?}");
}
