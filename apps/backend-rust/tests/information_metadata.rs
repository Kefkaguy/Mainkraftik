use mainkraft::information;
use serde_json::Value;
#[test]
fn original_private_news_metadata_and_unicode_flag_semantics() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/information-metadata.json")).expect("oracle");
    for case in fixture["tags"].as_array().expect("tags") {
        assert_eq!(
            information::tags(case["text"].as_str().expect("text")),
            case["flags"],
            "{}",
            case["text"]
        );
    }
    for case in fixture["news"].as_array().expect("news") {
        match information::news_metadata(&case["row"]) {
            Ok(metadata) => {
                assert_eq!(metadata, case["metadata"]);
                assert!(metadata.get("content").is_none());
                assert!(metadata.get("summary").is_none());
            }
            Err(e) => {
                assert_eq!(e.kind, case["kind"].as_str().expect("kind"));
                assert_eq!(e.message, case["message"].as_str().expect("message"));
            }
        }
    }
}
