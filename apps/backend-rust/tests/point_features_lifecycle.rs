use chrono::{DateTime, Utc};
use mainkraft::{calendar::Calendar, db, point_features};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::Arc;

fn equal(a: &Value, b: &Value) -> bool {
    if a == b {
        return true;
    }
    if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
        return matches!((DateTime::parse_from_rfc3339(a),DateTime::parse_from_rfc3339(b)), (Ok(a),Ok(b)) if a==b);
    }
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equal(a, b))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len() && a.iter().all(|(k, a)| b.get(k).is_some_and(|b| equal(a, b)))
        }
        _ => false,
    }
}
async fn snapshot(pool: &PgPool, query: &str) -> Value {
    json!(
        sqlx::query_scalar::<_, Value>(query)
            .fetch_all(pool)
            .await
            .expect("snapshot")
    )
}

#[tokio::test]
#[ignore = "requires original Python causal-feature database oracle"]
async fn causal_feature_reads_exact_identities_immutable_writes_and_transaction_recovery() {
    let uri = std::env::var("RUST_TEST_DATABASE_URL").expect("URI");
    let u = url::Url::parse(&uri).expect("URI");
    assert!(
        matches!(u.host_str(), Some("localhost" | "127.0.0.1"))
            && u.port() == Some(5543)
            && u.path().starts_with("/intelligence_test_rust_")
    );
    let pool = db::pool(&uri, false).await.expect("pool");
    db::migrate_research(&pool)
        .await
        .expect("idempotent migrations");
    let fixture: Value = serde_json::from_slice(
        &std::fs::read(std::env::var("RUST_POINT_ORACLE").expect("oracle")).expect("read"),
    )
    .expect("JSON");
    let calendar = Calendar::embedded().expect("calendar");
    let mut count = 0;
    for case in fixture["cases"].as_array().expect("cases") {
        let cut = case["cutoff"]
            .as_str()
            .map(|s| s.parse::<DateTime<Utc>>().expect("cutoff"));
        let observed = case["observed"].as_bool().expect("observed");
        let symbol = case["symbol"].as_str().expect("symbol");
        let mut tx = pool.begin().await.expect("transaction");
        let context = point_features::load_context(&mut tx, "sip", cut, observed)
            .await
            .expect("load");
        let mut selection = serde_json::Map::new();
        for (s, rows) in &context.streams {
            selection.insert(s.clone(),json!({"ids":rows.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"last_close":rows.last().map(|r|r["close"].clone()),"basis":rows.last().map(|r|r["adjustment_asof"].clone())}));
        }
        assert_eq!(
            json!(selection),
            case["selection"],
            "causal database selection"
        );
        let panel = point_features::build_features(
            &calendar,
            &context,
            symbol,
            observed,
            case["latest_only"].as_bool().expect("latest"),
        )
        .expect("features");
        assert_eq!(
            json!(panel),
            case["rows"],
            "exact values and lineage for {symbol}, {cut:?}, {observed}"
        );
        let mode = if observed {
            "prospective"
        } else {
            "historical"
        };
        let ids = point_features::persist_panel(&mut tx, symbol, &panel, mode)
            .await
            .expect("persist");
        assert_eq!(json!(ids), case["ids"], "exact content identities");
        assert_eq!(
            point_features::persist_panel(&mut tx, symbol, &panel, mode)
                .await
                .expect("replay"),
            ids
        );
        tx.commit().await.expect("commit");
        count += panel.len();
    }
    let query = fixture["query"].as_str().expect("query");
    assert!(
        equal(&snapshot(&pool, query).await, &fixture["snapshot"]),
        "persisted snapshot differs"
    );
    let total: i64 = sqlx::query_scalar("SELECT count(*) FROM feature_snapshots")
        .fetch_one(&pool)
        .await
        .expect("count");
    // A second-row DB failure must leave neither a partial panel nor a stale transaction.
    let mut conn = pool.acquire().await.expect("connection");
    let context = point_features::load_context(&mut conn, "sip", None, false)
        .await
        .expect("load");
    let mut fresh =
        point_features::build_features(&calendar, &context, "MSI", false, false).expect("panel");
    assert!(fresh.len() > 2);
    for row in &mut fresh {
        row.lineage["transaction_fixture"] = json!("fresh");
    }
    let fresh = Arc::new(fresh);
    let failing = fresh[1].identity("MSI", "historical").expect("ID");
    // Identity is locally derived hex, never external SQL text.
    assert!(failing.bytes().all(|c| c.is_ascii_hexdigit()));
    sqlx::raw_sql(&format!("CREATE FUNCTION fail_second_feature() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.id='{failing}' THEN RAISE EXCEPTION 'fixture second insert failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER fixture_failure BEFORE INSERT ON feature_snapshots FOR EACH ROW EXECUTE FUNCTION fail_second_feature()"))
        .execute(&mut *conn).await.expect("failure injection");
    drop(conn);
    let mut tx = pool.begin().await.expect("transaction");
    assert!(
        point_features::persist_panel(&mut tx, "MSI", &fresh, "historical")
            .await
            .is_err()
    );
    tx.rollback().await.expect("rollback");
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM feature_snapshots")
            .fetch_one(&pool)
            .await
            .expect("count"),
        total
    );
    sqlx::query("DROP TRIGGER fixture_failure ON feature_snapshots")
        .execute(&pool)
        .await
        .expect("remove injection");
    // Drop an in-flight transaction to emulate caller cancellation/connection recovery.
    let mut abandoned = pool.begin().await.expect("transaction");
    point_features::persist_panel(&mut abandoned, "MSI", &fresh[..1], "historical")
        .await
        .expect("uncommitted write");
    drop(abandoned);
    let mut check = pool.acquire().await.expect("recovered connection");
    let absent: i64 = sqlx::query_scalar("SELECT count(*) FROM feature_snapshots WHERE id=$1")
        .bind(fresh[0].identity("MSI", "historical").expect("ID"))
        .fetch_one(&mut *check)
        .await
        .expect("absence");
    assert_eq!(absent, 0);
    drop(check);
    // Concurrent independent transactions all return the same immutable identities.
    let mut writers = Vec::new();
    for _ in 0..12 {
        let pool = pool.clone();
        let fresh = fresh.clone();
        writers.push(tokio::spawn(async move {
            let mut tx = pool.begin().await.expect("transaction");
            let ids = point_features::persist_panel(&mut tx, "MSI", &fresh, "historical")
                .await
                .expect("write");
            tx.commit().await.expect("commit");
            ids
        }));
    }
    let expected: Vec<_> = fresh
        .iter()
        .map(|r| r.identity("MSI", "historical").expect("ID"))
        .collect();
    for writer in writers {
        assert_eq!(writer.await.expect("writer"), expected);
    }
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM feature_snapshots")
            .fetch_one(&pool)
            .await
            .expect("count"),
        total + fresh.len() as i64
    );
    assert!(
        sqlx::query("UPDATE feature_snapshots SET lineage='{}' WHERE id=$1")
            .bind(&expected[0])
            .execute(&pool)
            .await
            .is_err()
    );
    assert!(
        sqlx::query("DELETE FROM feature_snapshots WHERE id=$1")
            .bind(&expected[0])
            .execute(&pool)
            .await
            .is_err()
    );
    // New connection/service reconstruction sees the same persisted immutable evidence.
    pool.close().await;
    let reopened = db::pool(&uri, false).await.expect("restart pool");
    let existing:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r)-'created_at' FROM feature_snapshots r WHERE NOT lineage ? 'transaction_fixture' ORDER BY id").fetch_all(&reopened).await.expect("restart records");
    assert!(equal(&json!(existing), &fixture["snapshot"]));
    println!(
        "24 original DB feature workflows; {count} exact rows; {total} immutable identities; late data/basis, repeat, 12 concurrent writers, rollback, cancellation and reconnect passed"
    );
    reopened.close().await;
}
