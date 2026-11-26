use mainkraft::python_set::{Seed, hash_string, ordered, ranked};
use serde_json::Value;
#[test]
fn actual_discovery_adapter_refuses_unvalidated_inputs_and_preserves_empty_news() {
    use mainkraft::discovery::candidate_order_seeded;
    use serde_json::json;
    let seed = Seed::from_integer(0);
    let valid = json!({"MSI":{},"A":{}});
    for field in [Value::Null, json!(false), json!(0), json!("")] {
        assert!(
            candidate_order_seeded(&json!({"a":{"symbols":field}}), &valid, &seed)
                .unwrap()
                .is_empty()
        );
    }
    assert!(
        candidate_order_seeded(&json!({"a":{}}), &valid, &seed)
            .unwrap()
            .is_empty()
    );
    for bad in [
        Value::Null,
        json!([]),
        json!({"a":null}),
        json!({"a":{"symbols":true}}),
        json!({"a":{"symbols":["MSI",null]}}),
        json!({"a":{"symbols":[["MSI"]]}}),
    ] {
        assert!(candidate_order_seeded(&bad, &valid, &seed).is_err());
    }
    assert_eq!(
        candidate_order_seeded(&json!({"a":{"symbols":["MSI","MSI"]}}), &valid, &seed).unwrap(),
        vec![("MSI".into(), 1)]
    );
    assert_eq!(
        candidate_order_seeded(&json!({"a":{"symbols":"AAA"}}), &valid, &seed).unwrap(),
        vec![("A".into(), 1)]
    );
    let too_many = vec![json!("MSI"); mainkraft::python_set::MAX_SYMBOLS + 1];
    assert!(candidate_order_seeded(&json!({"a":{"symbols":too_many}}), &valid, &seed).is_err());
}
#[test]
fn original_cpython_hash_set_collisions_and_counter_order_match() {
    for blob in [
        include_str!("fixtures/python-set-parity.json"),
        include_str!("fixtures/python-set-parity-linux.json"),
    ] {
        let corpus: Value = serde_json::from_str(blob).unwrap();
        for reference in corpus["references"].as_array().unwrap() {
            let seed = Seed::from_integer(reference["seed"].as_u64().unwrap() as u32);
            for case in reference["cases"].as_array().unwrap() {
                for (value, expected) in case["hashes"].as_object().unwrap() {
                    assert_eq!(
                        hash_string(value, &seed),
                        expected.as_i64().unwrap(),
                        "hash {}",
                        value
                    );
                }
                let mut articles = Vec::new();
                for (index, article) in case["articles"].as_array().unwrap().iter().enumerate() {
                    let values: Vec<String> =
                        serde_json::from_value(article[1]["symbols"].clone()).unwrap();
                    assert_eq!(
                        serde_json::to_value(ordered(&values, &seed).unwrap()).unwrap(),
                        case["set_order"][index],
                        "set {} seed {}",
                        case["name"],
                        reference["seed"]
                    );
                    articles.push(values);
                }
                let mapping: serde_json::Map<String, Value> = case["articles"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| (row[0].as_str().unwrap().to_owned(), row[1].clone()))
                    .collect();
                let actual = mainkraft::discovery::candidate_order_seeded(
                    &Value::Object(mapping),
                    &case["valid"],
                    &seed,
                )
                .unwrap();
                assert_eq!(
                    serde_json::to_value(actual).unwrap(),
                    case["expected"],
                    "actual discovery adapter {} seed {}",
                    case["name"],
                    reference["seed"]
                );
                let result = ranked(
                    articles.iter().map(Vec::as_slice),
                    |s| case["valid"].get(s).is_some(),
                    &seed,
                )
                .unwrap();
                assert_eq!(
                    serde_json::to_value(result).unwrap(),
                    case["expected"],
                    "Counter {} seed {}",
                    case["name"],
                    reference["seed"]
                );
            }
        }
    }
}

fn symbol(mut value: usize) -> String {
    let mut chars = Vec::new();
    loop {
        chars.push((b'A' + (value % 26) as u8) as char);
        value /= 26;
        if value == 0 {
            break;
        }
    }
    chars.reverse();
    format!("X{}", chars.into_iter().collect::<String>())
}
#[test]
fn resize_boundaries_and_large_growth_match_original_digests() {
    use sha2::{Digest, Sha256};
    for blob in [
        include_str!("fixtures/python-set-extended.json"),
        include_str!("fixtures/python-set-extended-linux.json"),
    ] {
        let corpus: Value = serde_json::from_str(blob).unwrap();
        for reference in corpus["references"].as_array().unwrap() {
            let seed = Seed::from_integer(reference["seed"].as_u64().unwrap() as u32);
            for case in reference["large"].as_array().unwrap() {
                let values: Vec<_> = (0..case["size"].as_u64().unwrap() as usize)
                    .map(symbol)
                    .collect();
                let order = ordered(&values, &seed).unwrap();
                assert_eq!(serde_json::to_value(&order[..60]).unwrap(), case["first60"]);
                let digest = format!("{:x}", Sha256::digest(serde_json::to_vec(&order).unwrap()));
                assert_eq!(
                    digest, case["order_sha256"],
                    "large {} seed {}",
                    case["size"], reference["seed"]
                );
                let result = ranked([values.as_slice()], |_| true, &seed).unwrap();
                let expected: Vec<_> = order[..60].iter().map(|s| (s.clone(), 1_usize)).collect();
                assert_eq!(result, expected);
            }
        }
    }
}
#[test]
fn oversized_collections_fail_and_process_randomization_stays_stable() {
    let seed = Seed::from_integer(0);
    assert!(ordered(&vec![String::new(); 100001], &seed).is_err());
    assert!(ordered(&["X".repeat(8 * 1024 * 1024 + 1)], &seed).is_err());
    let values = vec!["MSI".into(); 50001];
    assert!(ranked([values.as_slice(), values.as_slice()], |_| true, &seed).is_err());
    let first = mainkraft::python_set::process_seed().unwrap();
    let second = mainkraft::python_set::process_seed().unwrap();
    assert_eq!(hash_string("MSI", first), hash_string("MSI", second));
}
