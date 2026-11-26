use mainkraft::{
    Error, canonical,
    engine::Clock,
    evidence::{self, Family, Ledger, Store},
};
use serde_json::{Value, json};
struct FixedClock(chrono::DateTime<chrono::Utc>);
impl Clock for FixedClock {
    fn now(&self) -> chrono::DateTime<chrono::Utc> {
        self.0
    }
}
#[test]
fn original_record_ids_bytes_reads_pointers_and_capacity_boundaries() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/evidence-storage.json")).expect("oracle");
    for c in f["encoding"].as_array().expect("encodings") {
        for (field, spaced) in [("compact", false), ("spaced", true)] {
            let bytes = canonical::bounded(&c["value"], 65536, spaced).expect("encode");
            assert_eq!(bytes, c[field].as_str().expect("bytes").as_bytes());
            assert_eq!(
                canonical::bounded(&c["value"], bytes.len(), spaced).expect("exact"),
                bytes
            );
            if !bytes.is_empty() {
                assert!(canonical::bounded(&c["value"], bytes.len() - 1, spaced).is_err());
            }
        }
    }
    let directory = tempfile::Builder::new()
        .prefix("mainkraft-evidence-fixture-")
        .tempdir()
        .expect("directory");
    let output = std::env::var_os("RUST_NATIVE_EVIDENCE_OUTPUT").map(std::path::PathBuf::from);
    let base = output.as_deref().unwrap_or(directory.path());
    for (i, c) in f["cases"].as_array().expect("cases").iter().enumerate() {
        let root = base.join(i.to_string());
        let family = match c["family"].as_str().expect("family") {
            "chart" => Family::Chart,
            "lab" => Family::Lab,
            _ => Family::Compressed,
        };
        let store = Store::new(&root, family, 65536).expect("store");
        assert!(!root.exists());
        if family != Family::Compressed {
            let initial = store.latest().expect("not run");
            assert_eq!(
                initial["status"],
                if family == Family::Lab {
                    "not_run"
                } else {
                    "NOT_RUN"
                }
            );
            assert!(!root.exists());
        }
        let id = store.put(&c["kind"], &c["payload"]).expect("put");
        assert_eq!(id, c["id"]);
        assert_eq!(store.read(&id).expect("read"), c["read"]);
        assert_eq!(store.put(&c["kind"], &c["payload"]).expect("replay"), id);
        let path = root
            .join(if family == Family::Compressed {
                "compressed"
            } else {
                "records"
            })
            .join(format!(
                "{id}.{}",
                if family == Family::Compressed {
                    "json.gz"
                } else {
                    "json"
                }
            ));
        if family == Family::Compressed {
            let bytes = std::fs::read(&path).expect("gzip");
            let reference = hex::decode(c["gzip_hex"].as_str().expect("gzip")).expect("hex");
            assert_eq!(&bytes[..10], &reference[..10]);
            assert_eq!(&bytes[bytes.len() - 8..], &reference[reference.len() - 8..]);
            let legacy_root = root.join("legacy");
            std::fs::create_dir_all(legacy_root.join("compressed")).expect("legacy directory");
            let path = legacy_root.join("compressed").join(format!("{id}.json.gz"));
            let legacy = Store::new(&legacy_root, family, 65536).expect("legacy store");
            std::fs::write(&path, &reference).expect("legacy gzip");
            assert_eq!(
                legacy
                    .put(&c["kind"], &c["payload"])
                    .expect("existing Python gzip"),
                id
            );
            assert_eq!(std::fs::read(&path).expect("unchanged"), reference);
        } else {
            assert_eq!(
                std::fs::read(&path).expect("record"),
                c["encoded"].as_str().expect("bytes").as_bytes()
            );
            if family == Family::Chart {
                store.publish_chart(&id).expect("publish");
            } else {
                assert_eq!(
                    store
                        .publish_lab(
                            &c["payload"],
                            &FixedClock(
                                f["instant"].as_str().expect("time").parse().expect("time")
                            )
                        )
                        .expect("publish"),
                    c["published_id"]
                );
            }
            assert_eq!(
                std::fs::read_to_string(root.join("latest.json")).expect("pointer"),
                c["pointer"]
            );
            match store.latest() {
                Ok(value) => assert_eq!(value, c["latest"]),
                Err(Error::Reference { kind, message }) => {
                    assert_eq!(kind, c["latest_error"]["kind"]);
                    assert_eq!(message, c["latest_error"]["message"]);
                }
                other => panic!("unexpected latest {other:?}"),
            }
        }
        assert!(store.read("../escape").is_err());
    }
    let store = Store::new(&directory.path().join("capacity"), Family::Chart, 20).expect("store");
    assert!(
        store
            .put(&json!("fixture"), &json!({"large":"x".repeat(1000)}))
            .is_err()
    );
}
#[tokio::test]
async fn original_append_only_journal_bytes_duplicates_rollback_and_busy_recovery() {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/evidence-storage.json")).expect("oracle");
    let directory = tempfile::tempdir().expect("dir");
    let output = std::env::var_os("RUST_NATIVE_EVIDENCE_OUTPUT")
        .map(|p| std::path::PathBuf::from(p).join("journal"));
    let root = output.as_deref().unwrap_or(directory.path());
    let ledger = Ledger::open(root).await.expect("ledger");
    for c in f["journal"].as_array().expect("journal") {
        let mut tx = ledger.begin().await.expect("begin");
        evidence::append(
            &mut tx,
            c["key"].as_str().expect("key"),
            c["kind"].as_str().expect("kind"),
            &c["payload"],
            65536,
        )
        .await
        .expect("append");
        tx.commit().await.expect("commit");
    }
    let mut tx = ledger.begin().await.expect("begin");
    evidence::append(
        &mut tx,
        "rollback",
        "outcome",
        &json!({"future":true}),
        65536,
    )
    .await
    .expect("append");
    tx.rollback().await.expect("rollback");
    assert!(ledger.read("rollback").await.expect("read").is_none());
    let mut tx = ledger.begin().await.expect("snapshot");
    let rows: Vec<(String, String, String)> =
        sqlx::query_as("SELECT key,kind,payload FROM evidence ORDER BY rowid")
            .fetch_all(&mut *tx)
            .await
            .expect("rows");
    assert_eq!(json!(rows), f["journal_rows"]);
    for statement in ["UPDATE evidence SET payload='{}'", "DELETE FROM evidence"] {
        let error = sqlx::query(statement)
            .execute(&mut *tx)
            .await
            .expect_err("immutable trigger");
        assert_eq!(
            error.as_database_error().expect("SQLite error").message(),
            "Research evidence is append-only"
        );
    }
    tx.rollback().await.expect("end");
    let competing = Ledger::open(root).await.expect("competing");
    let held = ledger.begin().await.expect("held");
    assert!(competing.begin().await.is_err());
    held.rollback().await.expect("release");
    let mut tx = competing.begin().await.expect("recovered capacity");
    assert!(
        evidence::append(
            &mut tx,
            "after-lock",
            "controller_run",
            &json!({"ok":true}),
            65536
        )
        .await
        .expect("append")
    );
    tx.commit().await.expect("commit");
    competing.close().await;
    ledger.close().await;
}
