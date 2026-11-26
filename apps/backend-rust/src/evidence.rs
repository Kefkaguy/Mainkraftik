//! Compatible, atomic local research records and append-only controller journals.
//! No broker imports, enrollment, forecasting, promotion or deployed-root defaults.
use crate::{
    Error, Result, canonical,
    engine::{Clock, stamp},
    invalid,
};
use flate2::{Compression, GzBuilder, read::MultiGzDecoder};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{
    Sqlite, SqliteConnection, SqlitePool, Transaction,
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::sync::Semaphore;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Family {
    Chart,
    Lab,
    Compressed,
}
#[derive(Clone)]
pub struct Store {
    root: PathBuf,
    family: Family,
    limit: usize,
    workers: Arc<Semaphore>,
    #[cfg(test)]
    hook: Option<Arc<dyn Fn(Stage) + Send + Sync>>,
}
#[derive(Clone, Copy, Debug)]
enum Stage {
    RecordStaged,
    RecordCommitted,
    PointerStaged,
    PointerCommitted,
}
fn checkpoint(stage: Stage) {
    #[cfg(test)]
    if std::env::var("RUST_EVIDENCE_CRASH").is_ok_and(|v| v == format!("{stage:?}")) {
        std::process::exit(73);
    }
    let _ = stage;
}
fn sync_directory(path: &Path) -> Result<()> {
    #[cfg(unix)]
    File::open(path)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
fn replace_pointer(mut file: tempfile::NamedTempFile, target: &Path) -> Result<()> {
    // Retry only a retained staged file after known Windows sharing failures.
    // The previous pointer is never removed and failure is never success.
    for attempt in 0..16 {
        match file.persist(target) {
            Ok(_) => return Ok(()),
            Err(error) => {
                if cfg!(windows)
                    && matches!(error.error.raw_os_error(), Some(5 | 32 | 33))
                    && attempt < 15
                {
                    file = error.file;
                    std::thread::sleep(Duration::from_millis(1_u64 << attempt.min(4)));
                    continue;
                }
                return Err(Error::Io(error.error));
            }
        }
    }
    Err(invalid("Research pointer replacement did not complete"))
}
fn valid_key(key: &str, family: Family) -> Result<()> {
    if key.len() != 64
        || !key
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid(match family {
            Family::Chart => "Invalid record ID",
            Family::Lab => "Invalid research record id",
            Family::Compressed => "Invalid evidence ID",
        }));
    }
    Ok(())
}
fn read_limited(path: &Path, limit: usize) -> Result<Vec<u8>> {
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(invalid("Research evidence path is a symbolic link"));
    }
    let mut out = Vec::new();
    File::open(path)?
        .take(limit as u64 + 1)
        .read_to_end(&mut out)?;
    if out.len() > limit {
        return Err(invalid("Research evidence exceeds configured byte limit"));
    }
    Ok(out)
}
impl Store {
    /// Limit is explicit: the runtime must supply its verified artifact budget.
    /// Merely creating a reader does not create or mutate a store directory.
    pub fn new(root: &Path, family: Family, limit: usize) -> Result<Self> {
        if limit == 0 || limit > usize::MAX / 2 {
            return Err(invalid("Invalid evidence byte limit"));
        }
        Ok(Self {
            root: root.to_owned(),
            family,
            limit,
            workers: Arc::new(Semaphore::new(2)),
            #[cfg(test)]
            hook: None,
        })
    }
    pub fn directory_sync_supported() -> bool {
        cfg!(unix)
    }
    fn stage(&self, stage: Stage) {
        checkpoint(stage);
        #[cfg(test)]
        if let Some(hook) = &self.hook {
            hook(stage);
        }
    }
    fn path(&self, key: &str) -> PathBuf {
        self.root
            .join(if self.family == Family::Compressed {
                "compressed"
            } else {
                "records"
            })
            .join(format!(
                "{key}.{}",
                if self.family == Family::Compressed {
                    "json.gz"
                } else {
                    "json"
                }
            ))
    }
    fn prepare(&self, folder: &Path) -> Result<()> {
        let mut missing = Vec::new();
        let mut directory = Some(folder);
        while let Some(path) = directory {
            if path.as_os_str().is_empty() || path.try_exists()? {
                break;
            }
            missing.push(path.to_owned());
            directory = path.parent();
        }
        fs::create_dir_all(folder)?;
        let root = self.root.canonicalize()?;
        let resolved = folder.canonicalize()?;
        if !resolved.starts_with(root) {
            return Err(invalid("Research evidence directory escapes store"));
        }
        for path in missing.iter().rev() {
            sync_directory(path)?;
            sync_directory(
                path.parent()
                    .filter(|p| !p.as_os_str().is_empty())
                    .unwrap_or(Path::new(".")),
            )?;
        }
        Ok(())
    }
    fn decoded(&self, path: &Path) -> Result<Vec<u8>> {
        // Encoded data is bounded too; gzip overhead can exceed a tiny plain limit.
        let data = read_limited(path, self.limit.saturating_add(1024))?;
        if self.family != Family::Compressed {
            if data.len() > self.limit {
                return Err(invalid("Research evidence exceeds configured byte limit"));
            }
            return Ok(data);
        }
        let mut out = Vec::new();
        MultiGzDecoder::new(data.as_slice())
            .take(self.limit as u64 + 1)
            .read_to_end(&mut out)?;
        if out.len() > self.limit {
            return Err(invalid("Research evidence exceeds configured byte limit"));
        }
        Ok(out)
    }
    pub fn record(&self, key: &str) -> Result<Value> {
        valid_key(key, self.family)?;
        let value: Value = serde_json::from_slice(&self.decoded(&self.path(key))?)?;
        if canonical::digest(&value)? != key {
            return Err(invalid(match self.family {
                Family::Chart => "Chart evidence integrity failure",
                Family::Lab => "Research record integrity failed",
                Family::Compressed => "Compressed evidence hash mismatch",
            }));
        }
        Ok(value)
    }
    pub fn read(&self, key: &str) -> Result<Value> {
        let mut record = self.record(key)?;
        Ok(if self.family == Family::Chart {
            record
        } else {
            record["payload"].take()
        })
    }
    fn atomic(&self, target: &Path, bytes: &[u8], pointer: bool) -> Result<()> {
        let parent = target
            .parent()
            .ok_or_else(|| invalid("Missing evidence directory"))?;
        self.prepare(parent)?;
        // Serialize pointer replacement across processes. On Windows, competing
        // MoveFileEx replacements can otherwise observe a delete-pending target.
        // The lock is separate from the pointer so readers remain independent.
        let _publication_lock = if pointer {
            let file = fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(self.root.join(".evidence-publication.lock"))?;
            fs2::FileExt::lock_exclusive(&file)?;
            Some(file)
        } else {
            None
        };
        let mut builder = tempfile::Builder::new();
        builder.prefix(".evidence-").suffix(".tmp");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            builder.permissions(fs::Permissions::from_mode(
                if !pointer && self.family == Family::Lab {
                    0o600
                } else {
                    0o666
                },
            ));
        }
        let mut temporary = builder.tempfile_in(parent)?;
        temporary.write_all(bytes)?;
        temporary.as_file().sync_all()?;
        self.stage(if pointer {
            Stage::PointerStaged
        } else {
            Stage::RecordStaged
        });
        if pointer {
            replace_pointer(temporary, target)?;
        } else {
            match temporary.persist_noclobber(target) {
                Ok(_) => {}
                Err(e) if e.error.kind() == std::io::ErrorKind::AlreadyExists => {
                    let expected = if self.family == Family::Compressed {
                        let mut out = Vec::new();
                        MultiGzDecoder::new(bytes)
                            .take(self.limit as u64 + 1)
                            .read_to_end(&mut out)?;
                        out
                    } else {
                        bytes.to_vec()
                    };
                    if self.decoded(target)? != expected {
                        return Err(invalid(self.conflict()));
                    }
                    sync_directory(parent)?;
                    return Ok(());
                }
                Err(e) => return Err(Error::Io(e.error)),
            }
        }
        self.stage(if pointer {
            Stage::PointerCommitted
        } else {
            Stage::RecordCommitted
        });
        sync_directory(parent)?;
        Ok(())
    }
    fn conflict(&self) -> &'static str {
        match self.family {
            Family::Chart => "Immutable chart evidence differs",
            Family::Lab => "Research record corruption",
            Family::Compressed => "Immutable compressed evidence mismatch",
        }
    }
    pub fn put(&self, kind: &Value, payload: &Value) -> Result<String> {
        // Encode borrowed payloads rather than cloning a potentially large model.
        let mut content = b"{\"kind\":".to_vec();
        if self.limit < content.len() + 12 {
            return Err(invalid("Research evidence exceeds configured byte limit"));
        }
        content.extend(canonical::bounded(
            kind,
            self.limit - content.len() - 12,
            false,
        )?);
        content.extend_from_slice(b",\"payload\":");
        content.extend(canonical::bounded(
            payload,
            self.limit - content.len() - 1,
            false,
        )?);
        content.push(b'}');
        let key = hex::encode(Sha256::digest(&content));
        let path = self.path(&key);
        if path.try_exists()? {
            if self.decoded(&path)? != content {
                return Err(invalid(self.conflict()));
            }
            sync_directory(
                path.parent()
                    .ok_or_else(|| invalid("Missing evidence directory"))?,
            )?;
            return Ok(key);
        }
        let bytes = if self.family == Family::Compressed {
            let mut out = GzBuilder::new()
                .mtime(0)
                .operating_system(255)
                .write(Vec::new(), Compression::best());
            out.write_all(&content)?;
            out.finish()?
        } else {
            content
        };
        self.atomic(&path, &bytes, false)?;
        Ok(key)
    }
    pub async fn put_async(&self, kind: Value, payload: Value) -> Result<String> {
        self.blocking(move |store| store.put(&kind, &payload)).await
    }
    async fn blocking<T: Send + 'static>(
        &self,
        operation: impl FnOnce(Self) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let permit = self
            .workers
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| invalid("Research evidence workers unavailable"))?;
        let owned = self.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            operation(owned)
        })
        .await
        .map_err(|_| invalid("Research evidence writer failed"))?
    }
    pub async fn publish_chart_async(&self, key: String) -> Result<()> {
        self.blocking(move |store| store.publish_chart(&key)).await
    }
    pub async fn publish_lab_async(&self, payload: Value, clock: Arc<dyn Clock>) -> Result<String> {
        self.blocking(move |store| store.publish_lab(&payload, clock.as_ref()))
            .await
    }
    pub fn publish_chart(&self, key: &str) -> Result<()> {
        if self.family != Family::Chart {
            return Err(invalid("Chart publisher requires chart store"));
        }
        self.record(key)?;
        self.atomic(
            &self.root.join("latest.json"),
            format!("{{\"id\": \"{key}\"}}").as_bytes(),
            true,
        )
    }
    pub fn publish_lab(&self, payload: &Value, clock: &dyn Clock) -> Result<String> {
        if self.family != Family::Lab {
            return Err(invalid("Lab publisher requires lab store"));
        }
        let key = self.put(&json!("report"), payload)?;
        let content = canonical::bounded(
            &json!({"id":key,"published":stamp(clock.now())}),
            self.limit,
            false,
        )?;
        self.atomic(&self.root.join("latest.json"), &content, true)?;
        Ok(key)
    }
    pub fn latest(&self) -> Result<Value> {
        if self.family == Family::Compressed {
            return Err(invalid("Compressed evidence has no latest pointer"));
        }
        let path = self.root.join("latest.json");
        if !path.try_exists()? {
            return Ok(if self.family == Family::Lab {
                json!({"status":"not_run","prospective":{"status":"not_nominated"}})
            } else {
                json!({"status":"NOT_RUN","execution_authority":false})
            });
        }
        let pointer: Value = serde_json::from_slice(&read_limited(&path, self.limit)?)?;
        let key = pointer["id"]
            .as_str()
            .ok_or_else(|| invalid("Invalid research pointer"))?;
        let record = self.record(key)?;
        let mut payload = record["payload"]
            .as_object()
            .ok_or_else(|| {
                let ty = match &record["payload"] {
                    Value::Null => "NoneType",
                    Value::Bool(_) => "bool",
                    Value::Array(_) => "list",
                    Value::String(_) => "str",
                    Value::Number(n) => {
                        if n.is_f64() {
                            "float"
                        } else {
                            "int"
                        }
                    }
                    Value::Object(_) => "dict",
                };
                Error::Reference {
                    kind: "TypeError",
                    message: if self.family == Family::Lab {
                        format!("unsupported operand type(s) for |: '{ty}' and 'dict'")
                    } else {
                        format!("'{ty}' object is not a mapping")
                    },
                }
            })?
            .clone();
        if self.family == Family::Lab {
            payload.insert("report_id".into(), json!(key));
        } else {
            payload.entry("record_id").or_insert(json!(key));
        }
        Ok(Value::Object(payload))
    }
}

pub struct Ledger {
    pool: SqlitePool,
}
impl Ledger {
    pub async fn open(root: &Path) -> Result<Self> {
        tokio::fs::create_dir_all(root).await?;
        let options = SqliteConnectOptions::new()
            .filename(root.join("controller.sqlite"))
            .create_if_missing(true)
            .busy_timeout(Duration::from_secs(2));
        let pool = SqlitePoolOptions::new()
            .max_connections(2)
            .acquire_timeout(Duration::from_secs(2))
            .connect_with(options)
            .await?;
        let mut tx = pool.begin().await?;
        sqlx::query("CREATE TABLE IF NOT EXISTS evidence (key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)").execute(&mut *tx).await?;
        for action in ["UPDATE", "DELETE"] {
            sqlx::query(&format!("CREATE TRIGGER IF NOT EXISTS immutable_{action} BEFORE {action} ON evidence BEGIN SELECT RAISE(ABORT,'Research evidence is append-only'); END")).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        Ok(Self { pool })
    }
    pub async fn begin(&self) -> Result<Transaction<'static, Sqlite>> {
        Ok(self.pool.begin_with("BEGIN IMMEDIATE").await?)
    }
    pub async fn read(&self, key: &str) -> Result<Option<Value>> {
        let text: Option<String> = sqlx::query_scalar("SELECT payload FROM evidence WHERE key=?")
            .bind(key)
            .fetch_optional(&self.pool)
            .await?;
        Ok(text.map(|s| serde_json::from_str(&s)).transpose()?)
    }
    pub async fn close(self) {
        self.pool.close().await;
    }
}
pub async fn append(
    db: &mut SqliteConnection,
    key: &str,
    kind: &str,
    payload: &Value,
    limit: usize,
) -> Result<bool> {
    let content = String::from_utf8(canonical::bounded(payload, limit, true)?)
        .map_err(|_| invalid("Research JSON encoding failed"))?;
    Ok(sqlx::query("INSERT OR IGNORE INTO evidence VALUES(?,?,?)")
        .bind(key)
        .bind(kind)
        .bind(content)
        .execute(db)
        .await?
        .rows_affected()
        == 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        process::Command,
        sync::{
            Condvar, Mutex,
            atomic::{AtomicUsize, Ordering},
        },
    };
    fn child_root() -> PathBuf {
        let root = PathBuf::from(std::env::var_os("RUST_EVIDENCE_TEST_ROOT").expect("child root"));
        assert_eq!(
            root.parent()
                .expect("parent")
                .canonicalize()
                .expect("canonical"),
            std::env::temp_dir().canonicalize().expect("temp")
        );
        assert!(
            root.file_name()
                .expect("name")
                .to_string_lossy()
                .starts_with("mainkraft-evidence-crash-")
        );
        root
    }
    #[test]
    fn simultaneous_record_creation_keeps_one_complete_identity_in_all_families() {
        for family in [Family::Chart, Family::Lab, Family::Compressed] {
            let root = tempfile::tempdir().expect("root");
            let store = Store::new(root.path(), family, 65536).expect("store");
            let barrier = Arc::new(std::sync::Barrier::new(12));
            let ids = std::thread::scope(|scope| {
                let mut threads = Vec::new();
                for _ in 0..12 {
                    let store = &store;
                    let barrier = barrier.clone();
                    threads.push(scope.spawn(move || {
                        barrier.wait();
                        store
                            .put(&json!("duplicate"), &json!({"text":"λ😀","return":1e-7}))
                            .expect("exclusive insert")
                    }));
                }
                threads
                    .into_iter()
                    .map(|t| t.join().expect("thread"))
                    .collect::<Vec<_>>()
            });
            assert!(ids.iter().all(|id| id == &ids[0]));
            assert_eq!(
                store.record(&ids[0]).expect("complete")["payload"]["text"],
                "λ😀"
            );
            let folder = store.path(&ids[0]).parent().expect("parent").to_owned();
            assert_eq!(fs::read_dir(folder).expect("records").count(), 1);
        }
    }
    #[test]
    fn compressed_output_limit_refuses_expansion_without_modifying_the_file() {
        let root = tempfile::tempdir().expect("root");
        let large = Store::new(root.path(), Family::Compressed, 1024 * 1024).expect("store");
        let key = large
            .put(&json!("fixture"), &json!({"text":"x".repeat(128*1024)}))
            .expect("large record");
        let before = fs::read(large.path(&key)).expect("original");
        let bounded = Store::new(root.path(), Family::Compressed, 1024).expect("bounded");
        assert!(
            matches!(bounded.record(&key),Err(Error::Invalid(message)) if message=="Research evidence exceeds configured byte limit")
        );
        assert_eq!(fs::read(large.path(&key)).expect("unchanged"), before);
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn concurrent_journal_issuers_keep_the_first_semantic_prediction() {
        let root = tempfile::tempdir().expect("root");
        let ledger = Arc::new(Ledger::open(root.path()).await.expect("ledger"));
        let mut tasks = Vec::new();
        for i in 0..12 {
            let ledger = ledger.clone();
            tasks.push(tokio::spawn(async move {
                let mut tx = ledger.begin().await.expect("exclusive transaction");
                let inserted = append(
                    &mut tx,
                    "same/forecast",
                    "prediction",
                    &json!({"issued":i}),
                    65536,
                )
                .await
                .expect("append");
                tx.commit().await.expect("commit");
                (i, inserted)
            }));
        }
        let mut winners = Vec::new();
        for task in tasks {
            let (i, inserted) = task.await.expect("join");
            if inserted {
                winners.push(i);
            }
        }
        assert_eq!(winners.len(), 1);
        assert_eq!(
            ledger.read("same/forecast").await.expect("read"),
            Some(json!({"issued":winners[0]}))
        );
        Arc::try_unwrap(ledger)
            .ok()
            .expect("sole owner")
            .close()
            .await;
    }
    #[test]
    #[ignore = "only invoked by the parent process-interruption test"]
    fn interrupted_writer_child() {
        let root = child_root();
        let store = Store::new(&root, Family::Chart, 65536).expect("store");
        let key = store
            .put(&json!("report"), &json!({"version":2}))
            .expect("put");
        store.publish_chart(&key).expect("publish");
        panic!("crash checkpoint was not reached");
    }
    #[test]
    fn process_exit_at_each_atomic_boundary_preserves_old_or_complete_new_evidence() {
        for stage in [
            "RecordStaged",
            "RecordCommitted",
            "PointerStaged",
            "PointerCommitted",
        ] {
            let dir = tempfile::Builder::new()
                .prefix("mainkraft-evidence-crash-")
                .tempdir()
                .expect("dir");
            let store = Store::new(dir.path(), Family::Chart, 65536).expect("store");
            let old = store
                .put(&json!("report"), &json!({"version":1}))
                .expect("old");
            store.publish_chart(&old).expect("old pointer");
            let child = Command::new(std::env::current_exe().expect("test executable"))
                .args([
                    "--ignored",
                    "--exact",
                    "evidence::tests::interrupted_writer_child",
                ])
                .env("RUST_EVIDENCE_TEST_ROOT", dir.path())
                .env("RUST_EVIDENCE_CRASH", stage)
                .output()
                .expect("child");
            assert_eq!(
                child.status.code(),
                Some(73),
                "{stage}: {}",
                String::from_utf8_lossy(&child.stderr)
            );
            let key =
                canonical::digest(&json!({"kind":"report","payload":{"version":2}})).expect("ID");
            if stage == "RecordStaged" {
                assert!(!store.path(&key).exists());
            } else {
                assert_eq!(
                    store.read(&key).expect("complete record")["payload"]["version"],
                    2
                );
            }
            assert_eq!(
                store.latest().expect("complete pointer")["version"],
                if stage == "PointerCommitted" { 2 } else { 1 }
            );
            assert_eq!(
                store
                    .put(&json!("report"), &json!({"version":2}))
                    .expect("retry"),
                key
            );
            store.publish_chart(&key).expect("retry pointer");
            assert_eq!(store.latest().expect("latest")["version"], 2);
        }
    }
    #[test]
    fn concurrent_publication_never_exposes_partial_records_or_pointers_and_corruption_is_not_repaired()
     {
        let dir = tempfile::tempdir().expect("dir");
        let store = Store::new(dir.path(), Family::Chart, 65536).expect("store");
        let keys = [
            store
                .put(&json!("report"), &json!({"version":1}))
                .expect("one"),
            store
                .put(&json!("report"), &json!({"version":2}))
                .expect("two"),
        ];
        store.publish_chart(&keys[0]).expect("initial");
        std::thread::scope(|scope| {
            for i in 0..12 {
                let store = &store;
                let key = &keys[i % 2];
                scope.spawn(move || {
                    for _ in 0..8 {
                        store.publish_chart(key).expect("concurrent publish");
                    }
                });
            }
            for _ in 0..300 {
                let latest = store.latest().expect("atomic read");
                assert!(matches!(latest["version"].as_u64(), Some(1 | 2)));
            }
        });
        let before = fs::read(store.root.join("latest.json")).expect("pointer");
        let corrupt = b"{\"kind\":\"tampered\",\"payload\":{}}";
        fs::write(store.path(&keys[0]), corrupt).expect("corruption fixture");
        assert!(store.put(&json!("report"), &json!({"version":1})).is_err());
        assert!(store.publish_chart(&keys[0]).is_err());
        assert_eq!(fs::read(store.path(&keys[0])).expect("unchanged"), corrupt);
        assert_eq!(
            fs::read(store.root.join("latest.json")).expect("unchanged pointer"),
            before
        );
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn cancelled_async_writers_keep_both_slots_until_blocking_commits_finish() {
        let dir = tempfile::tempdir().expect("dir");
        let mut store = Store::new(dir.path(), Family::Chart, 65536).expect("store");
        let entered = Arc::new(Semaphore::new(0));
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let release = Arc::new((Mutex::new(false), Condvar::new()));
        struct Release(Arc<(Mutex<bool>, Condvar)>);
        impl Drop for Release {
            fn drop(&mut self) {
                *self.0.0.lock().unwrap_or_else(|e| e.into_inner()) = true;
                self.0.1.notify_all();
            }
        }
        let _release = Release(release.clone());
        store.hook = Some(Arc::new({
            let entered = entered.clone();
            let active = active.clone();
            let peak = peak.clone();
            let release = release.clone();
            move |stage| {
                if matches!(stage, Stage::RecordStaged) {
                    let n = active.fetch_add(1, Ordering::SeqCst) + 1;
                    peak.fetch_max(n, Ordering::SeqCst);
                    entered.add_permits(1);
                    let mut ready = release.0.lock().unwrap_or_else(|e| e.into_inner());
                    while !*ready {
                        ready = release.1.wait(ready).unwrap_or_else(|e| e.into_inner());
                    }
                    active.fetch_sub(1, Ordering::SeqCst);
                }
            }
        }));
        let store = Arc::new(store);
        let mut tasks = Vec::new();
        for i in 0..12 {
            let s = store.clone();
            tasks.push(tokio::spawn(async move {
                s.put_async(json!("fixture"), json!({"index":i})).await
            }));
        }
        let permits = tokio::time::timeout(Duration::from_secs(10), entered.acquire_many(2))
            .await
            .expect("entered")
            .expect("permits");
        permits.forget();
        assert_eq!(active.load(Ordering::SeqCst), 2);
        for task in &tasks {
            task.abort();
        }
        for task in tasks {
            assert!(task.await.expect_err("cancelled").is_cancelled());
        }
        let s = store.clone();
        let third =
            tokio::spawn(async move { s.put_async(json!("fixture"), json!({"index":99})).await });
        tokio::time::sleep(Duration::from_millis(40)).await;
        assert!(!third.is_finished());
        assert_eq!(active.load(Ordering::SeqCst), 2);
        *release.0.lock().unwrap_or_else(|e| e.into_inner()) = true;
        release.1.notify_all();
        third.await.expect("join").expect("committed");
        let _all = tokio::time::timeout(Duration::from_secs(10), store.workers.acquire_many(2))
            .await
            .expect("finished")
            .expect("slots");
        assert_eq!(peak.load(Ordering::SeqCst), 2);
        assert_eq!(
            fs::read_dir(dir.path().join("records"))
                .expect("records")
                .filter_map(|p| p.ok())
                .filter(|p| p.path().extension().is_some_and(|e| e == "json"))
                .count(),
            3
        );
    }
    #[tokio::test]
    #[ignore = "only invoked by journal restart parent"]
    async fn uncommitted_journal_child() {
        let root = child_root();
        let ledger = Ledger::open(&root).await.expect("ledger");
        let mut tx = ledger.begin().await.expect("begin");
        append(
            &mut tx,
            "abandoned",
            "prediction",
            &json!({"future":true}),
            65536,
        )
        .await
        .expect("append");
        std::process::exit(74);
    }
    #[tokio::test]
    async fn sqlite_process_death_rolls_back_uncommitted_evidence_and_allows_restart() {
        let dir = tempfile::Builder::new()
            .prefix("mainkraft-evidence-crash-")
            .tempdir()
            .expect("dir");
        let ledger = Ledger::open(dir.path()).await.expect("ledger");
        let mut tx = ledger.begin().await.expect("begin");
        append(
            &mut tx,
            "original",
            "prediction",
            &json!({"kept":true}),
            65536,
        )
        .await
        .expect("append");
        tx.commit().await.expect("commit");
        ledger.close().await;
        let child = Command::new(std::env::current_exe().expect("executable"))
            .args([
                "--ignored",
                "--exact",
                "evidence::tests::uncommitted_journal_child",
            ])
            .env("RUST_EVIDENCE_TEST_ROOT", dir.path())
            .output()
            .expect("child");
        assert_eq!(
            child.status.code(),
            Some(74),
            "{}",
            String::from_utf8_lossy(&child.stderr)
        );
        let ledger = Ledger::open(dir.path()).await.expect("restart");
        assert_eq!(
            ledger.read("original").await.expect("original"),
            Some(json!({"kept":true}))
        );
        assert!(ledger.read("abandoned").await.expect("rollback").is_none());
        let mut tx = ledger.begin().await.expect("recovered lock");
        assert!(
            append(
                &mut tx,
                "abandoned",
                "prediction",
                &json!({"retry":true}),
                65536
            )
            .await
            .expect("retry")
        );
        tx.commit().await.expect("commit");
        ledger.close().await;
    }
}
