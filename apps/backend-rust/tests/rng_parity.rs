use mainkraft::numpy_rng::Pcg64;
use serde_json::Value;

#[test]
fn pcg64_and_ziggurat_preserve_existing_exploration() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/numpy-rng.json")).expect("oracle");
    for case in fixture["cases"].as_array().expect("cases") {
        let seed = case["seed"].as_u64().expect("seed") as u32;
        let mut raw = Pcg64::new(seed);
        for expected in case["raw"].as_array().expect("raw") {
            assert_eq!(
                raw.next_u64(),
                expected.as_u64().expect("word"),
                "seed {seed}"
            );
        }
        let mut normal = Pcg64::new(seed);
        for expected in case["normal"].as_array().expect("normal") {
            let a = normal.normal();
            let b = expected.as_f64().expect("sample");
            assert!((a - b).abs() < 1e-14, "seed {seed}: {a} != {b}");
        }
    }
}
