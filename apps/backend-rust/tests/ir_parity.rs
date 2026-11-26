use mainkraft::{ir_html::Document, ir_robots::Robots};
use serde_json::Value;
#[test]
fn legacy_html_text_entities_attributes_and_robots_match_python() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/ir-html-robots.json")).expect("fixtures");
    for (i, case) in f["html"].as_array().expect("html").iter().enumerate() {
        let actual = Document::parse(case["html"].as_str().expect("html"))
            .expect("legacy DOM")
            .projection();
        assert_eq!(actual, case["tags"], "HTML case {i}");
        let metadata =
            mainkraft::official_ir::publication_metadata(case["html"].as_str().expect("html"))
                .expect("publication");
        let metadata = metadata
            .map(|(raw, time)| serde_json::json!([raw, mainkraft::engine::stamp(time)]))
            .unwrap_or_else(|| serde_json::json!([null, null]));
        assert_eq!(metadata, case["publication"], "publication case {i}");
    }
    for case in f["robots"].as_array().expect("robots") {
        let parser = Robots::parse(case["rules"].as_str().expect("rules"));
        for url in case["urls"].as_array().expect("urls") {
            assert_eq!(
                parser.can_fetch(url["url"].as_str().expect("url")),
                url["allowed"].as_bool().expect("allowed"),
                "{} {}",
                case["rules"],
                url["url"]
            );
        }
    }
    let edge: Value =
        serde_json::from_str(include_str!("fixtures/ir-edges.json")).expect("edge fixtures");
    for case in edge["html"].as_array().expect("html") {
        assert_eq!(
            Document::parse(case["html"].as_str().expect("html"))
                .expect("DOM")
                .projection(),
            case["tags"]
        );
    }
    for case in edge["joins"].as_array().expect("joins") {
        assert_eq!(
            mainkraft::official_ir::join(
                case["base"].as_str().expect("base"),
                case["target"].as_str().expect("target")
            )
            .expect("join"),
            case["result"]
        );
    }
    let r = &edge["robots"];
    let boundaries: Value = serde_json::from_str(include_str!("fixtures/ir-boundaries.json"))
        .expect("boundary fixture");
    for case in boundaries["html"].as_array().expect("cases") {
        assert_eq!(
            Document::parse(case["html"].as_str().expect("html"))
                .expect("DOM")
                .projection(),
            case["tags"]
        );
    }
    let parser = Robots::parse(r["rules"].as_str().expect("rules"));
    for url in r["urls"].as_array().expect("urls") {
        assert_eq!(
            parser.can_fetch(url["url"].as_str().expect("url")),
            url["allowed"].as_bool().expect("allowed")
        );
    }
}
