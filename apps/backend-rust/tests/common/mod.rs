use serde_json::Value;
use sqlx::{Connection, SqliteConnection, sqlite::SqliteConnectOptions};
use std::path::Path;
pub async fn seed_reports(root: &Path) -> Value {
    let fixture: Value =
        serde_json::from_str(include_str!("../fixtures/report-api-parity.json")).expect("fixture");
    for (path, content) in fixture["files"].as_object().expect("files") {
        let path = root.join(path);
        std::fs::create_dir_all(path.parent().expect("parent")).expect("directory");
        std::fs::write(path, content.as_str().expect("content")).expect("fixture file");
    }
    let path = root.join("chart-intelligence/controller.sqlite");
    let mut connection = SqliteConnection::connect_with(
        &SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true),
    )
    .await
    .expect("SQLite fixture");
    sqlx::raw_sql(fixture["sqlite_schema"].as_str().expect("schema"))
        .execute(&mut connection)
        .await
        .expect("original schema");
    for row in fixture["evidence"].as_array().expect("evidence") {
        sqlx::query("INSERT INTO evidence(key,kind,payload) VALUES(?,?,?)")
            .bind(row["key"].as_str().expect("key"))
            .bind(row["kind"].as_str().expect("kind"))
            .bind(row["payload"].to_string())
            .execute(&mut connection)
            .await
            .expect("row");
    }
    connection.close().await.expect("close");
    fixture
}
