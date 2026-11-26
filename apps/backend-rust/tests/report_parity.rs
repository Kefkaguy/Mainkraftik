mod common;
use mainkraft::report_api;
use serde_json::json;
use sha2::{Digest, Sha256};
#[tokio::test]
async fn original_immutable_research_and_sqlite_status_contracts_match_without_writes() {
    let root = tempfile::tempdir().expect("isolated evidence");
    let fixture = common::seed_reports(root.path()).await;
    let database = root.path().join("chart-intelligence/controller.sqlite");
    let before = Sha256::digest(std::fs::read(&database).expect("SQLite"));
    assert_eq!(
        report_api::phase7(root.path())
            .await
            .unwrap_or_else(|_| panic!("phase7")),
        fixture["expected"]["phase7"]
    );
    assert_eq!(
        report_api::chart_status(root.path())
            .await
            .unwrap_or_else(|_| panic!("chart")),
        fixture["expected"]["chart"]
    );
    assert_eq!(
        report_api::lab_results(root.path())
            .await
            .unwrap_or_else(|_| panic!("lab")),
        fixture["expected"]["lab"]
    );
    assert_eq!(
        before,
        Sha256::digest(std::fs::read(&database).expect("SQLite unchanged"))
    );
    assert!(!fixture["expected"].to_string().contains("PRIVATE"));
    assert!(!fixture["expected"].to_string().contains("features"));
    assert_eq!(
        fixture["expected"]["chart"]["monitoring"]["prediction"]
            .as_array()
            .expect("predictions")
            .len(),
        100
    );
    assert!(report_api::read_record(root.path(), "../outside").is_err());
    let folder = root.path().join("phase7");
    let pointer: serde_json::Value =
        serde_json::from_slice(&std::fs::read(folder.join("latest.json")).expect("pointer"))
            .expect("JSON");
    let record = folder
        .join("records")
        .join(format!("{}.json", pointer["id"].as_str().expect("id")));
    std::fs::write(
        record,
        b"{\"kind\":\"report\",\"payload\":{\"status\":\"tampered\"}}",
    )
    .expect("tamper synthetic fixture");
    let error = report_api::phase7(root.path())
        .await
        .expect_err("tamper rejected");
    assert_eq!(error.status.as_u16(), 503);
    assert_eq!(error.detail, "Phase 7 immutable evidence unavailable");
    let empty = tempfile::tempdir().expect("empty root");
    assert_eq!(
        report_api::latest(empty.path(), false).expect("not run"),
        json!({"status":"NOT_RUN","execution_authority":false})
    );
    assert_eq!(
        report_api::latest(empty.path(), true).expect("not run"),
        json!({"status":"not_run","prospective":{"status":"not_nominated"}})
    );
    assert_eq!(
        report_api::monitor(empty.path())
            .await
            .expect("not running"),
        json!({"status":"NOT_RUNNING","predictions":[],"runs":[],"outcomes":[],"execution_authority":false})
    );
    assert!(!empty.path().join("controller.sqlite").exists());
}
