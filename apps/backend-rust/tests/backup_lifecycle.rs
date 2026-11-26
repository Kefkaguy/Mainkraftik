use mainkraft::{
    backup::{Backup, Kind, Limits},
    engine::SystemClock,
};
use serde_json::{Value, json};
use sqlx::{Connection, PgConnection};
use std::{path::Path, sync::Arc};

fn uri(base: &str, name: &str) -> String {
    let mut u = url::Url::parse(base).unwrap();
    u.set_path(&format!("/{name}"));
    u.to_string()
}
async fn sequence(uri: &str, query: &str) -> i64 {
    let mut c = PgConnection::connect(uri).await.unwrap();
    sqlx::query_scalar(query).fetch_one(&mut c).await.unwrap()
}

#[tokio::test]
#[ignore = "Requires owned local disposable databases; scripts/test-rust-backup.py"]
async fn python_native_archives_restore_atomically_and_preserve_order_state() {
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_BACKUP_FIXTURE").unwrap()).unwrap(),
    )
    .unwrap();
    let base = std::env::var("RUST_BACKUP_BASE_URI").unwrap();
    let u = url::Url::parse(&base).unwrap();
    assert!(matches!(u.host_str(), Some("localhost" | "127.0.0.1")) && u.port() == Some(5543));
    let root = Path::new(fixture["directory"].as_str().unwrap());
    let backup = Backup::new(Arc::new(SystemClock), Limits::default()).unwrap();
    let mut receipts = Vec::new();
    let mut verified = Vec::new();
    for (label, kind) in [("research", Kind::Research), ("paper", Kind::Paper)] {
        let source = uri(&base, fixture["sources"][label].as_str().unwrap());
        let target = fixture["targets"][label].as_str().unwrap();
        let original = root.join(format!("{label}-python-v2.zip"));
        let native = root.join(format!("{label}-native.zip"));
        let receipt = backup.export(&source, kind, &native).await.unwrap();
        let bytes = std::fs::read(&native).unwrap();
        assert!(backup.export(&source, kind, &native).await.is_err());
        assert_eq!(std::fs::read(&native).unwrap(), bytes);
        let restore = backup
            .restore_empty(&uri(&base, target), kind, target, &original)
            .await
            .unwrap();
        assert_eq!(
            restore["row_counts"]
                .as_object()
                .unwrap()
                .values()
                .map(|v| v.as_u64().unwrap())
                .sum::<u64>(),
            receipt["rows"].as_u64().unwrap()
        );
        assert!(
            backup
                .restore_empty(&uri(&base, target), kind, target, &original)
                .await
                .is_err()
        );
        let own = backup.verify_local(&source, kind, &native).await.unwrap();
        // Persist created names before later assertions, so the Python owner can clean up.
        verified.push(own.clone());
        std::fs::write(
            root.join("native-results.json"),
            serde_json::to_vec_pretty(&json!({"verified":verified})).unwrap(),
        )
        .unwrap();
        let restored = uri(&base, own["restored_database"].as_str().unwrap());
        if kind == Kind::Research {
            assert_eq!(
                sequence(&restored, "SELECT nextval('watchlist_versions_id_seq')").await,
                9877
            );
            assert_eq!(
                sequence(&restored, "SELECT nextval('corporate_actions_id_seq')").await,
                7654
            );
            assert_eq!(
                sequence(
                    &uri(&base, target),
                    "SELECT nextval('watchlist_versions_id_seq')"
                )
                .await,
                2
            );
        } else {
            assert_eq!(
                sequence(&restored, "SELECT nextval('pilot_events_id_seq')").await,
                4322
            );
            let mut c = PgConnection::connect(&restored).await.unwrap();
            let count:i64=sqlx::query_scalar("SELECT count(*) FROM learning_intents WHERE state IN('unknown','partially_filled')").fetch_one(&mut c).await.unwrap();
            assert_eq!(count, 2);
            assert!(
                sqlx::query("UPDATE learning_records SET kind='tampered'")
                    .execute(&mut c)
                    .await
                    .is_err()
            );
        }
        receipts.push(json!({"kind":label,"export":receipt,"restore":restore}));
    }
    let legacy = fixture["legacy_target"].as_str().unwrap();
    backup
        .restore_empty(
            &uri(&base, legacy),
            Kind::Research,
            legacy,
            &root.join("research-python-v1.zip"),
        )
        .await
        .unwrap();
    let mut failures = Vec::new();
    for case in fixture["failures"].as_array().unwrap() {
        let target = case["target"].as_str().unwrap();
        let result = backup
            .restore_empty(
                &uri(&base, target),
                Kind::Research,
                target,
                &root.join(case["archive"].as_str().unwrap()),
            )
            .await;
        assert!(result.is_err(), "{} was accepted", case["name"]);
        let mut c = PgConnection::connect(&uri(&base, target)).await.unwrap();
        let count: i64 =
            sqlx::query_scalar("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                .fetch_one(&mut c)
                .await
                .unwrap();
        assert_eq!(count, 0, "{} left a partial schema", case["name"]);
        failures.push(json!({"case":case["name"],"rejected":true,"target_tables":count}));
    }
    let source = uri(&base, fixture["sources"]["research"].as_str().unwrap());
    let race = root.join("concurrent.zip");
    let other = Backup::new(Arc::new(SystemClock), Limits::default()).unwrap();
    let (a, b) = tokio::join!(
        backup.export(&source, Kind::Research, &race),
        other.export(&source, Kind::Research, &race)
    );
    assert_ne!(
        a.is_ok(),
        b.is_ok(),
        "concurrent archive publication must have exactly one winner"
    );
    backup.inspect(&race, Kind::Research).await.unwrap();
    // Observe an owned export blocked at a real PostgreSQL relation lock. Cancelling
    // its request must retain the worker and finish a single complete archive.
    let mut lock = PgConnection::connect(&source).await.unwrap();
    let mut transaction = lock.begin().await.unwrap();
    sqlx::query("LOCK TABLE securities IN ACCESS EXCLUSIVE MODE")
        .execute(&mut *transaction)
        .await
        .unwrap();
    let mut tagged = url::Url::parse(&source).unwrap();
    tagged
        .query_pairs_mut()
        .append_pair("application_name", "mainkraft_backup_cancel_test");
    let destination = root.join("cancelled-request.zip");
    let worker = backup.clone();
    let tagged = tagged.to_string();
    let archive = destination.clone();
    let request =
        tokio::spawn(async move { worker.export(&tagged, Kind::Research, &archive).await });
    let mut observer = PgConnection::connect(&source).await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(20),async {
        loop {
            let blocked:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='mainkraft_backup_cancel_test' AND wait_event_type='Lock' AND backend_xmin IS NOT NULL)").fetch_one(&mut observer).await.unwrap();
            if blocked {break;}
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    }).await.unwrap();
    let busy = backup
        .export(&source, Kind::Research, &root.join("busy.zip"))
        .await;
    assert!(busy.is_err());
    assert!(!root.join("busy.zip").exists());
    request.abort();
    assert!(request.await.unwrap_err().is_cancelled());
    sqlx::query("UPDATE securities SET name='committed after export snapshot' WHERE symbol='MSI'")
        .execute(&mut *transaction)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(20), async {
        while !destination.exists() {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    backup.inspect(&destination, Kind::Research).await.unwrap();
    sqlx::query("UPDATE securities SET name='Motorola Solutions' WHERE symbol='MSI'")
        .execute(&mut observer)
        .await
        .unwrap();
    let mut admin_url = url::Url::parse(&source).unwrap();
    admin_url.set_path("/postgres");
    let mut admin = PgConnection::connect(admin_url.as_str()).await.unwrap();
    let before: Vec<String> =
        sqlx::query_scalar("SELECT datname FROM pg_database ORDER BY datname")
            .fetch_all(&mut admin)
            .await
            .unwrap();
    assert!(
        backup
            .verify_local(&source, Kind::Research, &root.join("bad-copy.zip"))
            .await
            .is_err()
    );
    let after: Vec<String> = sqlx::query_scalar("SELECT datname FROM pg_database ORDER BY datname")
        .fetch_all(&mut admin)
        .await
        .unwrap();
    assert_eq!(
        before, after,
        "failed automatic verification leaked a database"
    );
    let target = fixture["targets"]["research"].as_str().unwrap();
    assert!(
        backup
            .restore_empty(
                &uri(&base, target),
                Kind::Research,
                "incorrect acknowledgement",
                &root.join("research-native.zip")
            )
            .await
            .is_err()
    );
    let mut restricted = url::Url::parse(&source).unwrap();
    restricted.set_path("/intelligence");
    assert!(
        backup
            .restore_empty(
                restricted.as_str(),
                Kind::Research,
                "intelligence",
                &root.join("research-native.zip")
            )
            .await
            .is_err()
    );
    let tiny = Backup::new(
        Arc::new(SystemClock),
        Limits {
            compressed: 16,
            ..Limits::default()
        },
    )
    .unwrap();
    assert!(
        tiny.inspect(&root.join("research-native.zip"), Kind::Research)
            .await
            .is_err()
    );
    assert!(
        tiny.export(&source, Kind::Research, &root.join("too-large.zip"))
            .await
            .is_err()
    );
    assert!(!root.join("too-large.zip").exists());
    let result = json!({"verified":verified,"receipts":receipts,"failures":failures,"overwrite_prevented":true,"concurrent_publication":true,"request_cancellation_retains_worker":true,"sequence_continuity":true,"immutable_records_preserved":true,"failed_verifier_database_cleaned":true,"resource_boundary":true,"broker_requests":0});
    std::fs::write(
        root.join("native-results.json"),
        serde_json::to_vec_pretty(&result).unwrap(),
    )
    .unwrap();
}
