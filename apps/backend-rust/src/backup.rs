//! Compatible binary-COPY archives. No broker, service startup or cutover action.
use crate::{
    Error, Result, db,
    engine::{Clock, stamp},
    invalid,
};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Connection, PgConnection};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    sync::Arc,
};
use tempfile::NamedTempFile;
use tokio::sync::{Semaphore, mpsc};
use uuid::Uuid;
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};
const APP: &str = "keftrade-intelligence";
const CHUNK: usize = 64 * 1024;
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Research,
    Paper,
}
impl Kind {
    fn paper(self) -> bool {
        self == Self::Paper
    }
}
#[derive(Clone, Copy)]
pub struct Limits {
    pub compressed: u64,
    pub expanded: u64,
    pub copy_frame: usize,
    pub tables: usize,
    pub manifest: usize,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            compressed: 8 * 1024u64.pow(3),
            expanded: 32 * 1024u64.pow(3),
            copy_frame: 32 * 1024 * 1024,
            tables: 256,
            manifest: 1024 * 1024,
        }
    }
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Table {
    pub name: String,
    pub rows: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes: Option<u64>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Sequence {
    pub table: String,
    pub column: String,
    pub last_value: i64,
    pub is_called: bool,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Manifest {
    pub application: String,
    pub format: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<Kind>,
    pub created_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub postgres_major: Option<i32>,
    pub tables: Vec<Table>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub migrations: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sequences: Vec<Sequence>,
}
pub struct Backup {
    clock: Arc<dyn Clock>,
    limits: Limits,
    slots: Arc<Semaphore>,
}
enum Part {
    Start(String),
    Bytes(Vec<u8>),
    Finish(Manifest),
}
fn archive_error(error: zip::result::ZipError) -> Error {
    match error {
        zip::result::ZipError::Io(e) => Error::Io(e),
        _ => invalid("Invalid or unsupported application backup ZIP"),
    }
}
fn quote(name: &str) -> Result<String> {
    if name.is_empty() || name.len() > 63 || name.contains('\0') {
        return Err(invalid("Invalid archive table/column identity"));
    }
    Ok(format!("\"{}\"", name.replace('"', "\"\"")))
}
fn table_sql(name: &str) -> Result<String> {
    Ok(format!("public.{}", quote(name)?))
}
fn protected(path: &Path) -> Result<()> {
    if path
        .to_string_lossy()
        .replace('\\', "/")
        .to_lowercase()
        .contains("/home/ubuntu/minecraft")
    {
        return Err(invalid(
            "Protected Minecraft resources must not be accessed",
        ));
    }
    Ok(())
}
fn sync_directory(path: &Path) -> Result<()> {
    #[cfg(unix)]
    File::open(path)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
fn sha_file(path: &Path, maximum: u64) -> Result<String> {
    sha_reader(File::open(path)?, maximum)
}
fn sha_reader(mut f: File, maximum: u64) -> Result<String> {
    if f.metadata()?.len() > maximum {
        return Err(invalid("Backup digest resource boundary"));
    }
    f.seek(SeekFrom::Start(0))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; CHUNK];
    let mut total = 0u64;
    loop {
        let n = f.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        total += n as u64;
        if total > maximum {
            return Err(invalid("Backup digest resource boundary"));
        }
        hash.update(&buffer[..n]);
    }
    Ok(hex::encode(hash.finalize()))
}
fn validate_uri(uri: &str, kind: Kind, restore: bool) -> Result<url::Url> {
    let u = url::Url::parse(uri).map_err(|_| invalid("Invalid application database URI"))?;
    if !matches!(u.scheme(), "postgres" | "postgresql")
        || u.query_pairs()
            .any(|(key, _)| matches!(key.as_ref(), "host" | "hostaddr" | "port" | "dbname"))
    {
        return Err(invalid("Database target overrides are not permitted"));
    }
    let name = u.path().trim_start_matches('/');
    let valid = if kind.paper() {
        name == "intelligence_paper" || name.starts_with("intelligence_paper_test_")
    } else {
        name == "intelligence"
            || name.starts_with("intelligence_test_")
            || name.starts_with("intelligence_restore_")
    };
    if !valid {
        return Err(invalid(
            "Database target is outside the independent application",
        ));
    }
    if restore
        && (!matches!(u.host_str(), Some("localhost" | "127.0.0.1"))
            || u.port() != Some(5543)
            || !(name.starts_with("intelligence_restore_")
                || kind.paper() && name.starts_with("intelligence_paper_test_")))
    {
        return Err(invalid(
            "Restore requires a separate local verification database on port 5543",
        ));
    }
    Ok(u)
}
async fn names(c: &mut PgConnection) -> Result<BTreeSet<String>> {
    Ok(
        sqlx::query_scalar("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            .fetch_all(c)
            .await?
            .into_iter()
            .collect(),
    )
}
async fn order(c: &mut PgConnection, tables: &BTreeSet<String>) -> Result<Vec<String>> {
    let mut deps: BTreeMap<String, BTreeSet<String>> = tables
        .iter()
        .map(|t| (t.clone(), BTreeSet::new()))
        .collect();
    let pairs:Vec<(String,String)>=sqlx::query_as("SELECT child.relname,parent.relname FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid JOIN pg_class parent ON parent.oid=c.confrelid JOIN pg_namespace n ON n.oid=child.relnamespace WHERE c.contype='f' AND n.nspname='public'").fetch_all(c).await?;
    for (child, parent) in pairs {
        if child != parent {
            deps.get_mut(&child)
                .ok_or_else(|| invalid("Unknown dependent table"))?
                .insert(parent);
        }
    }
    let mut result = Vec::new();
    let mut done = BTreeSet::new();
    while !deps.is_empty() {
        let ready: Vec<_> = deps
            .iter()
            .filter(|(_, d)| d.is_subset(&done))
            .map(|(t, _)| t.clone())
            .collect();
        if ready.is_empty() {
            return Err(invalid("Cyclic schema requires explicit restore procedure"));
        }
        for t in ready {
            deps.remove(&t);
            done.insert(t.clone());
            result.push(t);
        }
    }
    Ok(result)
}
async fn sequence_columns(c: &mut PgConnection) -> Result<Vec<(String, String)>> {
    Ok(sqlx::query_as("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND column_default LIKE 'nextval(%' ORDER BY table_name,column_name").fetch_all(c).await?)
}
async fn sequence_name(c: &mut PgConnection, table: &str, column: &str) -> Result<String> {
    Ok(sqlx::query_scalar("SELECT pg_get_serial_sequence($1,$2)")
        .bind(table_sql(table)?)
        .bind(column)
        .fetch_one(c)
        .await?)
}
async fn sequences(c: &mut PgConnection) -> Result<Vec<Sequence>> {
    let mut values = Vec::new();
    for (table, column) in sequence_columns(c).await? {
        let seq = sequence_name(c, &table, &column).await?;
        // pg_get_serial_sequence returns a server-quoted regclass identifier, never archive SQL.
        let (last_value, is_called): (i64, bool) =
            sqlx::query_as(&format!("SELECT last_value,is_called FROM {seq}"))
                .fetch_one(&mut *c)
                .await?;
        values.push(Sequence {
            table,
            column,
            last_value,
            is_called,
        });
    }
    Ok(values)
}
fn write_zip(
    mut file: NamedTempFile,
    mut receiver: mpsc::Receiver<Part>,
    limits: Limits,
) -> Result<(NamedTempFile, Manifest)> {
    let mut writer = ZipWriter::new(file.as_file_mut());
    let options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .compression_level(Some(6))
        .large_file(true);
    while let Some(part) = receiver.blocking_recv() {
        match part {
            Part::Start(name) => writer
                .start_file(format!("tables/{name}.bin"), options)
                .map_err(archive_error)?,
            Part::Bytes(bytes) => {
                writer.write_all(&bytes)?;
                if writer
                    .get_ref()
                    .ok_or_else(|| invalid("Closed ZIP writer"))?
                    .metadata()?
                    .len()
                    > limits.compressed
                {
                    return Err(invalid("Compressed backup resource boundary"));
                }
            }
            Part::Finish(manifest) => {
                let bytes = serde_json::to_vec(&manifest)?;
                if bytes.len() > limits.manifest {
                    return Err(invalid("Backup manifest resource boundary"));
                }
                writer
                    .start_file("manifest.json", options)
                    .map_err(archive_error)?;
                writer.write_all(&bytes)?;
                writer.finish().map_err(archive_error)?;
                file.as_file().sync_all()?;
                if file.as_file().metadata()?.len() > limits.compressed {
                    return Err(invalid("Compressed backup resource boundary"));
                }
                return Ok((file, manifest));
            }
        }
    }
    Err(invalid("Backup stream ended before a complete manifest"))
}
// Bound central-directory allocation before handing an archive to the ZIP decoder.
// Mainkraft archives have no executable prefix and are never multi-disk archives.
fn preflight(file: &mut File, limits: Limits) -> Result<()> {
    let size = file.metadata()?.len();
    if size > limits.compressed || size < 22 {
        return Err(invalid("Compressed backup resource boundary"));
    }
    let tail_size = size.min(65535 + 22) as usize;
    file.seek(SeekFrom::End(-(tail_size as i64)))?;
    let mut tail = vec![0; tail_size];
    file.read_exact(&mut tail)?;
    let read = |bytes: &[u8], offset: usize, length: usize| -> Result<u64> {
        let slice = bytes
            .get(offset..offset + length)
            .ok_or_else(|| invalid("Truncated ZIP directory"))?;
        Ok(slice
            .iter()
            .enumerate()
            .fold(0u64, |value, (i, b)| value | u64::from(*b) << (i * 8)))
    };
    let end = (0..=tail.len() - 22)
        .rev()
        .find(|&i| {
            tail[i..i + 4] == *b"PK\x05\x06"
                && read(&tail, i + 20, 2).is_ok_and(|n| i + 22 + n as usize == tail.len())
        })
        .ok_or_else(|| invalid("Missing complete ZIP directory"))?;
    if read(&tail, end + 4, 2)? != 0 || read(&tail, end + 6, 2)? != 0 {
        return Err(invalid("Multi-disk backup refused"));
    }
    let mut count = read(&tail, end + 10, 2)?;
    let mut directory_size = read(&tail, end + 12, 4)?;
    let mut offset = read(&tail, end + 16, 4)?;
    let absolute_end = size - tail_size as u64 + end as u64;
    let mut directory_end = absolute_end;
    let mut locator = [0; 20];
    if absolute_end >= 20 {
        file.seek(SeekFrom::Start(absolute_end - 20))?;
        file.read_exact(&mut locator)?;
    }
    let needs64 = count == 65535 || directory_size == u32::MAX as u64 || offset == u32::MAX as u64;
    if locator[..4] == *b"PK\x06\x07" || needs64 {
        let bytes = locator;
        if bytes[..4] != *b"PK\x06\x07" || read(&bytes, 4, 4)? != 0 || read(&bytes, 16, 4)? != 1 {
            return Err(invalid("Invalid ZIP64 locator"));
        }
        directory_end = read(&bytes, 8, 8)?;
        file.seek(SeekFrom::Start(directory_end))?;
        let mut bytes = [0; 56];
        file.read_exact(&mut bytes)?;
        if bytes[..4] != *b"PK\x06\x06"
            || read(&bytes, 4, 8)? != 44
            || read(&bytes, 16, 4)? != 0
            || read(&bytes, 20, 4)? != 0
            || read(&bytes, 24, 8)? != read(&bytes, 32, 8)?
        {
            return Err(invalid("Invalid ZIP64 directory"));
        }
        count = read(&bytes, 32, 8)?;
        directory_size = read(&bytes, 40, 8)?;
        offset = read(&bytes, 48, 8)?;
    } else if read(&tail, end + 8, 2)? != count {
        return Err(invalid("Inconsistent ZIP directory count"));
    }
    let maximum = limits.tables.saturating_add(1) as u64;
    if count > maximum
        || directory_size > maximum.saturating_mul(46 + 3 * 65535)
        || offset
            .checked_add(directory_size)
            .is_none_or(|end| end != directory_end)
    {
        return Err(invalid("Backup directory resource boundary"));
    }
    file.seek(SeekFrom::Start(0))?;
    Ok(())
}
fn inspect_file(mut file: File, kind: Kind, limits: Limits) -> Result<Manifest> {
    preflight(&mut file, limits)?;
    if file.metadata()?.len() > limits.compressed {
        return Err(invalid("Compressed backup resource boundary"));
    }
    let mut zip = ZipArchive::new(file).map_err(archive_error)?;
    if zip.has_duplicate_names() {
        return Err(invalid("Duplicate backup members refused"));
    }
    if zip.len() > limits.tables + 1 {
        return Err(invalid("Backup member count resource boundary"));
    }
    let mut members = BTreeSet::new();
    let mut expanded = 0u64;
    for i in 0..zip.len() {
        let member = zip.by_index(i).map_err(archive_error)?;
        if member.encrypted() || !members.insert(member.name().map_err(archive_error)?.into_owned())
        {
            return Err(invalid("Duplicate/encrypted backup members refused"));
        }
        expanded = expanded
            .checked_add(member.size())
            .ok_or_else(|| invalid("Expanded backup resource boundary"))?;
    }
    if expanded > limits.expanded {
        return Err(invalid("Expanded backup resource boundary"));
    }
    let mut header = zip.by_name("manifest.json").map_err(archive_error)?;
    if header.size() > limits.manifest as u64 {
        return Err(invalid("Backup manifest resource boundary"));
    }
    let mut bytes = Vec::new();
    header
        .by_ref()
        .take(limits.manifest as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > limits.manifest {
        return Err(invalid("Backup manifest resource boundary"));
    }
    drop(header);
    let manifest: Manifest = serde_json::from_slice(&bytes)?;
    if manifest.application != APP
        || !(manifest.format == 2 && manifest.kind == Some(kind)
            || manifest.format == 1 && kind == Kind::Research)
    {
        return Err(invalid("Wrong application/kind archive"));
    }
    let mut expected = BTreeSet::from(["manifest.json".to_owned()]);
    if manifest.format == 2 && manifest.postgres_major.is_none_or(|v| v < 1)
        || manifest.format == 1 && manifest.migrations.is_none()
    {
        return Err(invalid("Missing archive version/provenance"));
    }
    let mut tables = BTreeSet::new();
    for t in &manifest.tables {
        quote(&t.name)?;
        if !tables.insert(&t.name) {
            return Err(invalid("Duplicate archive table identity"));
        }
        expected.insert(format!("tables/{}.bin", t.name));
        if t.sha256
            .as_ref()
            .is_some_and(|s| s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()))
        {
            return Err(invalid("Invalid archived payload identity"));
        }
    }
    if expected != members {
        return Err(invalid("Backup member set does not match its manifest"));
    }
    if manifest.tables.len() > limits.tables {
        return Err(invalid("Backup table count resource boundary"));
    }
    let mut sequence_keys = BTreeSet::new();
    for s in &manifest.sequences {
        quote(&s.table)?;
        quote(&s.column)?;
        if !tables.contains(&s.table) || !sequence_keys.insert((&s.table, &s.column)) {
            return Err(invalid("Invalid archive sequence identity"));
        }
    }
    Ok(manifest)
}
fn read_member(
    mut file: File,
    name: &str,
    limits: Limits,
    sender: mpsc::Sender<Vec<u8>>,
) -> Result<(String, u64)> {
    preflight(&mut file, limits)?;
    let mut zip = ZipArchive::new(file).map_err(archive_error)?;
    let mut member = zip.by_name(name).map_err(archive_error)?;
    let mut hash = Sha256::new();
    let mut count = 0u64;
    loop {
        let mut bytes = vec![0u8; CHUNK];
        let n = member.read(&mut bytes)?;
        if n == 0 {
            break;
        }
        count += n as u64;
        if count > limits.expanded {
            return Err(invalid("Expanded backup resource boundary"));
        }
        bytes.truncate(n);
        hash.update(&bytes);
        sender
            .blocking_send(bytes)
            .map_err(|_| invalid("Restore consumer stopped"))?;
    }
    Ok((hex::encode(hash.finalize()), count))
}
impl Backup {
    pub fn new(clock: Arc<dyn Clock>, limits: Limits) -> Result<Arc<Self>> {
        if limits.compressed == 0
            || limits.expanded == 0
            || limits.copy_frame == 0
            || limits.tables == 0
            || limits.manifest == 0
        {
            return Err(invalid("Positive backup resource limits required"));
        }
        Ok(Arc::new(Self {
            clock,
            limits,
            slots: Arc::new(Semaphore::new(1)),
        }))
    }
    /// An owned task retains its database/ZIP ownership if the requesting future is cancelled.
    pub async fn export(self: &Arc<Self>, uri: &str, kind: Kind, path: &Path) -> Result<Value> {
        validate_uri(uri, kind, false)?;
        protected(path)?;
        let this = self.clone();
        let uri = uri.to_owned();
        let path = path.to_owned();
        let slot = this
            .slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| invalid("Backup worker busy"))?;
        tokio::spawn(async move {
            let _slot = slot;
            this.export_inner(&uri, kind, &path).await
        })
        .await
        .map_err(|_| invalid("Backup task failed"))?
    }
    async fn export_inner(&self, uri: &str, kind: Kind, path: &Path) -> Result<Value> {
        if path.symlink_metadata().is_ok() {
            return Err(invalid(
                "Choose a new backup filename; existing backups are never overwritten",
            ));
        }
        let parent = path
            .parent()
            .ok_or_else(|| invalid("Backup directory required"))?;
        fs::create_dir_all(parent)?;
        protected(&parent.canonicalize()?)?;
        let stage = NamedTempFile::new_in(parent)?;
        let (sender, receiver) = mpsc::channel(4);
        let limits = self.limits;
        let pool = db::pool(uri, kind.paper()).await?;
        let mut c = pool.acquire().await?;
        c.close_on_drop();
        sqlx::query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            .execute(&mut *c)
            .await?;
        let writer = tokio::task::spawn_blocking(move || write_zip(stage, receiver, limits));
        let work = async {
            let tables = names(&mut c).await?;
            if tables.len() > limits.tables {
                return Err(invalid("Backup table count resource boundary"));
            }
            let ordered = order(&mut c, &tables).await?;
            let version: i32 = sqlx::query_scalar::<_, String>("SHOW server_version_num")
                .fetch_one(&mut *c)
                .await?
                .parse()
                .map_err(|_| invalid("PostgreSQL version unavailable"))?;
            let mut manifest = Manifest {
                application: APP.into(),
                format: 2,
                kind: Some(kind),
                created_at: stamp(self.clock.now()),
                postgres_major: Some(version / 10000),
                tables: Vec::new(),
                migrations: None,
                sequences: Vec::new(),
            };
            let mut total = 0u64;
            for table in ordered {
                let quoted = table_sql(&table)?;
                let rows: i64 = sqlx::query_scalar(&format!("SELECT count(*) FROM {quoted}"))
                    .fetch_one(&mut *c)
                    .await?;
                sender
                    .send(Part::Start(table.clone()))
                    .await
                    .map_err(|_| invalid("Backup ZIP writer stopped"))?;
                let mut hash = Sha256::new();
                let mut size = 0u64;
                let mut copy = c
                    .copy_out_raw(&format!("COPY {quoted} TO STDOUT WITH(FORMAT BINARY)"))
                    .await?;
                while let Some(bytes) = copy.next().await {
                    let bytes = bytes?;
                    if bytes.len() > limits.copy_frame {
                        return Err(invalid("COPY message resource boundary"));
                    }
                    total = total
                        .checked_add(bytes.len() as u64)
                        .ok_or_else(|| invalid("Expanded backup resource boundary"))?;
                    if total > limits.expanded {
                        return Err(invalid("Expanded backup resource boundary"));
                    }
                    size += bytes.len() as u64;
                    hash.update(&bytes);
                    for chunk in bytes.chunks(CHUNK) {
                        sender
                            .send(Part::Bytes(chunk.to_vec()))
                            .await
                            .map_err(|_| invalid("Backup ZIP writer stopped"))?;
                    }
                }
                drop(copy);
                manifest.tables.push(Table {
                    name: table,
                    rows: rows as u64,
                    bytes: Some(size),
                    sha256: Some(hex::encode(hash.finalize())),
                });
            }
            manifest.sequences = sequences(&mut c).await?;
            sqlx::query("COMMIT").execute(&mut *c).await?;
            sender
                .send(Part::Finish(manifest))
                .await
                .map_err(|_| invalid("Backup ZIP writer stopped"))?;
            Ok(())
        }
        .await;
        drop(sender);
        let prepared = writer
            .await
            .map_err(|_| invalid("Backup ZIP worker failed"))?;
        drop(c);
        pool.close().await;
        work?;
        let (file, manifest) = prepared?;
        let path = path.to_owned();
        let receipt=tokio::task::spawn_blocking(move||->Result<Value>{let bytes=file.as_file().metadata()?.len();let sha=sha_file(file.path(),limits.compressed)?;file.persist_noclobber(&path).map_err(|e|Error::Io(e.error))?;sync_directory(path.parent().ok_or_else(||invalid("Backup directory required"))?)?;Ok(json!({"application":APP,"kind":kind,"sha256":sha,"bytes":bytes,"created_at":manifest.created_at,"rows":manifest.tables.iter().map(|t|t.rows).sum::<u64>()}))}).await.map_err(|_|invalid("Backup publication task failed"))??;
        Ok(receipt)
    }
    pub async fn inspect(self: &Arc<Self>, path: &Path, kind: Kind) -> Result<Manifest> {
        protected(path)?;
        let path = path.to_owned();
        let limits = self.limits;
        tokio::task::spawn_blocking(move || {
            protected(&path.canonicalize()?)?;
            inspect_file(File::open(path)?, kind, limits)
        })
        .await
        .map_err(|_| invalid("Archive inspection task failed"))?
    }
    pub async fn restore_empty(
        self: &Arc<Self>,
        uri: &str,
        kind: Kind,
        acknowledgement: &str,
        path: &Path,
    ) -> Result<Value> {
        let u = validate_uri(uri, kind, true)?;
        if acknowledgement != u.path().trim_start_matches('/') {
            return Err(invalid("Explicit new database acknowledgement required"));
        }
        protected(path)?;
        let this = self.clone();
        let uri = uri.to_owned();
        let path = path.to_owned();
        let slot = this
            .slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| invalid("Backup worker busy"))?;
        tokio::spawn(async move {
            let _slot = slot;
            this.restore_inner(&uri, kind, &path).await
        })
        .await
        .map_err(|_| invalid("Restore task failed"))?
    }
    async fn restore_inner(&self, uri: &str, kind: Kind, path: &Path) -> Result<Value> {
        protected(&path.canonicalize()?)?;
        let file = File::open(path)?;
        let inspection = file.try_clone()?;
        let limits = self.limits;
        let manifest = tokio::task::spawn_blocking(move || inspect_file(inspection, kind, limits))
            .await
            .map_err(|_| invalid("Archive inspection task failed"))??;
        let digest = file.try_clone()?;
        let original_hash =
            tokio::task::spawn_blocking(move || sha_reader(digest, limits.compressed))
                .await
                .map_err(|_| invalid("Backup digest task failed"))??;
        let mut c = PgConnection::connect(uri).await?;
        let mut tx = c.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock(753194090)")
            .execute(&mut *tx)
            .await?;
        if !names(&mut tx).await?.is_empty() {
            return Err(invalid(
                "Restore refused: target has existing tables; no overwrite permitted",
            ));
        }
        let version: i32 = sqlx::query_scalar::<_, String>("SHOW server_version_num")
            .fetch_one(&mut *tx)
            .await?
            .parse()
            .map_err(|_| invalid("PostgreSQL version unavailable"))?;
        if manifest
            .postgres_major
            .is_some_and(|v| v != version / 10000)
        {
            return Err(invalid(
                "Binary restore requires same PostgreSQL major version",
            ));
        }
        if kind.paper() {
            db::migrate_paper_in(
                &mut tx,
                manifest
                    .tables
                    .iter()
                    .any(|t| t.name.starts_with("learning_")),
            )
            .await?;
        } else {
            db::migrate_research_in(&mut tx).await?;
        }
        let tables = names(&mut tx).await?;
        let expected: BTreeSet<_> = manifest.tables.iter().map(|t| t.name.clone()).collect();
        if tables != expected {
            return Err(invalid("Archive/current migration schema mismatch"));
        }
        if let Some(expected) = &manifest.migrations {
            let current: Vec<String> =
                sqlx::query_scalar("SELECT name FROM schema_migrations ORDER BY name")
                    .fetch_all(&mut *tx)
                    .await?;
            if &current != expected {
                return Err(invalid("Migration history mismatch"));
            }
        }
        let quoted = tables
            .iter()
            .map(|t| table_sql(t))
            .collect::<Result<Vec<_>>>()?
            .join(",");
        sqlx::query(&format!("TRUNCATE {quoted} CASCADE"))
            .execute(&mut *tx)
            .await?;
        let mut counts = BTreeMap::new();
        for table in order(&mut tx, &tables).await? {
            let entry = manifest
                .tables
                .iter()
                .find(|t| t.name == table)
                .ok_or_else(|| invalid("Missing archived table"))?;
            let input = file.try_clone()?;
            let member = format!("tables/{table}.bin");
            let (sender, mut receiver) = mpsc::channel(4);
            let reader =
                tokio::task::spawn_blocking(move || read_member(input, &member, limits, sender));
            let mut copy = tx
                .copy_in_raw(&format!(
                    "COPY {} FROM STDIN WITH(FORMAT BINARY)",
                    table_sql(&table)?
                ))
                .await?;
            let sending = async {
                while let Some(chunk) = receiver.recv().await {
                    copy.send(chunk.as_slice()).await?;
                }
                Ok::<_, Error>(())
            }
            .await;
            drop(receiver);
            let read = reader
                .await
                .map_err(|_| invalid("Restore ZIP worker failed"))?;
            if let Err(error) = sending {
                let _ = copy.abort("Restore input failed").await;
                return Err(error);
            }
            let (hash, size) = match read {
                Ok(v) => v,
                Err(error) => {
                    let _ = copy.abort("Restore archive invalid").await;
                    return Err(error);
                }
            };
            if entry.sha256.as_ref().is_some_and(|s| s != &hash)
                || entry.bytes.is_some_and(|b| b != size)
            {
                let _ = copy.abort("Restore identity mismatch").await;
                return Err(invalid("Archived COPY payload identity mismatch"));
            }
            let rows = copy.finish().await?;
            if rows != entry.rows {
                return Err(invalid("Restored counts do not match consistent snapshot"));
            }
            counts.insert(table, rows);
        }
        let mut repairs = Vec::new();
        let columns = sequence_columns(&mut tx).await?;
        let present: BTreeSet<_> = columns.iter().cloned().collect();
        if manifest
            .sequences
            .iter()
            .any(|s| !present.contains(&(s.table.clone(), s.column.clone())))
        {
            return Err(invalid("Archive sequence/schema mismatch"));
        }
        for (table, column) in columns {
            let seq = sequence_name(&mut tx, &table, &column).await?;
            let maximum: Option<i64> = sqlx::query_scalar(&format!(
                "SELECT max({})::bigint FROM {}",
                quote(&column)?,
                table_sql(&table)?
            ))
            .fetch_one(&mut *tx)
            .await?;
            let state = manifest
                .sequences
                .iter()
                .find(|s| s.table == table && s.column == column);
            let (mut value, mut called) = state
                .map_or((maximum.unwrap_or(1), maximum.is_some()), |s| {
                    (s.last_value, s.is_called)
                });
            if value < 1 {
                return Err(invalid("Invalid archived sequence position"));
            }
            if maximum.is_some_and(|m| value < m || !called && value <= m) {
                value = maximum.ok_or_else(|| invalid("Sequence maximum absent"))?;
                called = true;
                repairs.push(json!({"table":table,"column":column,"reason":"Archived sequence below restored row maximum"}));
            }
            sqlx::query("SELECT setval($1::regclass,$2,$3)")
                .bind(seq)
                .bind(value)
                .bind(called)
                .execute(&mut *tx)
                .await?;
        }
        let digest_file = file.try_clone()?;
        let hash = tokio::task::spawn_blocking(move || sha_reader(digest_file, limits.compressed))
            .await
            .map_err(|_| invalid("Backup digest task failed"))??;
        if hash != original_hash {
            return Err(invalid(
                "Archive changed during restore; transaction rolled back",
            ));
        }
        tx.commit().await?;
        c.close().await?;
        Ok(
            json!({"application":APP,"kind":kind,"restore_verified":true,"restored_database":url::Url::parse(uri).map_err(|_|invalid("Invalid restore URI"))?.path().trim_start_matches('/'),"row_counts":counts,"sha256":hash,"bytes":file.metadata()?.len(),"sequence_repairs":repairs}),
        )
    }
    pub async fn verify_local(
        self: &Arc<Self>,
        source_uri: &str,
        kind: Kind,
        path: &Path,
    ) -> Result<Value> {
        validate_uri(source_uri, kind, false)?;
        protected(path)?;
        let this = self.clone();
        let source_uri = source_uri.to_owned();
        let path = path.to_owned();
        let slot = this
            .slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| invalid("Backup worker busy"))?;
        tokio::spawn(async move {
            let _slot = slot;
            this.verify_inner(&source_uri, kind, &path).await
        })
        .await
        .map_err(|_| invalid("Backup verification task failed"))?
    }
    async fn verify_inner(&self, source_uri: &str, kind: Kind, path: &Path) -> Result<Value> {
        let mut target = validate_uri(source_uri, kind, false)?;
        if !matches!(target.host_str(), Some("localhost" | "127.0.0.1"))
            || target.port() != Some(5543)
        {
            return Err(invalid(
                "Off-host restore verification must use independent local port 5543",
            ));
        }
        let name = format!(
            "{}{}",
            if kind.paper() {
                "intelligence_paper_test_bak_"
            } else {
                "intelligence_restore_"
            },
            Uuid::new_v4().simple()
        );
        target.set_path("/postgres");
        let mut admin = PgConnection::connect(target.as_str()).await?;
        sqlx::query(&format!("CREATE DATABASE {}", quote(&name)?))
            .execute(&mut admin)
            .await?;
        target.set_path(&format!("/{name}"));
        let restored = self.restore_inner(target.as_str(), kind, path).await;
        if restored.is_err() {
            sqlx::query(&format!("DROP DATABASE {} WITH(FORCE)", quote(&name)?))
                .execute(&mut admin)
                .await?;
        }
        admin.close().await?;
        restored
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(extra: bool) -> NamedTempFile {
        let mut file = NamedTempFile::new().unwrap();
        let mut zip = ZipWriter::new(file.as_file_mut());
        let options = SimpleFileOptions::default();
        zip.start_file("manifest.json", options).unwrap();
        zip.write_all(br#"{"application":"keftrade-intelligence","format":2,"kind":"research","postgres_major":18,"created_at":"2026-11-21T00:00:00Z","tables":[]}"#).unwrap();
        if extra {
            zip.start_file("../outside", options).unwrap();
            zip.write_all(b"never extract").unwrap();
        }
        zip.finish().unwrap();
        file
    }
    #[test]
    fn archive_identity_members_and_resources_are_checked_before_restore() {
        let good = fixture(false);
        assert!(
            inspect_file(
                File::open(good.path()).unwrap(),
                Kind::Research,
                Limits::default()
            )
            .is_ok()
        );
        assert!(
            inspect_file(
                File::open(good.path()).unwrap(),
                Kind::Paper,
                Limits::default()
            )
            .is_err()
        );
        assert!(
            inspect_file(
                File::open(fixture(true).path()).unwrap(),
                Kind::Research,
                Limits::default()
            )
            .is_err()
        );
        for limits in [
            Limits {
                compressed: 10,
                ..Limits::default()
            },
            Limits {
                expanded: 10,
                ..Limits::default()
            },
            Limits {
                manifest: 10,
                ..Limits::default()
            },
        ] {
            assert!(
                inspect_file(File::open(good.path()).unwrap(), Kind::Research, limits).is_err()
            );
        }
        // A falsified central-directory count must be rejected before decoder allocation.
        let mut bytes = fs::read(good.path()).unwrap();
        let end = bytes.len() - 22;
        bytes[end + 8..end + 12].copy_from_slice(&[254, 127, 254, 127]);
        fs::write(good.path(), bytes).unwrap();
        assert!(
            inspect_file(
                File::open(good.path()).unwrap(),
                Kind::Research,
                Limits::default()
            )
            .is_err()
        );
    }
    #[test]
    fn zip64_metadata_is_bounded_even_without_legacy_sentinels() {
        let file = fixture(false);
        let original = fs::read(file.path()).unwrap();
        let end = original.len() - 22;
        let size = u32::from_le_bytes(original[end + 12..end + 16].try_into().unwrap()) as u64;
        let offset = u32::from_le_bytes(original[end + 16..end + 20].try_into().unwrap()) as u64;
        for sentinel in [false, true] {
            let mut bytes = original[..end].to_vec();
            bytes.extend_from_slice(b"PK\x06\x06");
            bytes.extend_from_slice(&44u64.to_le_bytes());
            bytes.extend_from_slice(&[45, 0, 45, 0]);
            bytes.extend_from_slice(&[0; 8]);
            for n in [1u64, 1, size, offset] {
                bytes.extend_from_slice(&n.to_le_bytes());
            }
            bytes.extend_from_slice(b"PK\x06\x07");
            bytes.extend_from_slice(&[0; 4]);
            bytes.extend_from_slice(&(end as u64).to_le_bytes());
            bytes.extend_from_slice(&1u32.to_le_bytes());
            let tail = bytes.len();
            bytes.extend_from_slice(&original[end..]);
            if sentinel {
                bytes[tail + 8..tail + 20].fill(255);
            }
            fs::write(file.path(), &bytes).unwrap();
            assert!(
                inspect_file(
                    File::open(file.path()).unwrap(),
                    Kind::Research,
                    Limits::default()
                )
                .is_ok()
            );
            for position in [end + 24, end + 32] {
                bytes[position..position + 8].copy_from_slice(&1000000u64.to_le_bytes());
            }
            fs::write(file.path(), &bytes).unwrap();
            assert!(
                inspect_file(
                    File::open(file.path()).unwrap(),
                    Kind::Research,
                    Limits::default()
                )
                .is_err()
            );
        }
    }
    #[test]
    fn restore_authority_is_limited_to_explicit_local_disposable_databases() {
        for (uri, kind) in [
            ("postgres://localhost:5543/intelligence", Kind::Research),
            ("postgres://localhost:5543/intelligence_paper", Kind::Paper),
            (
                "postgres://example.invalid:5543/intelligence_restore_fixture",
                Kind::Research,
            ),
            (
                "postgres://localhost:5432/intelligence_restore_fixture",
                Kind::Research,
            ),
            ("postgres://localhost:5543/minecraft", Kind::Paper),
        ] {
            assert!(validate_uri(uri, kind, true).is_err());
        }
        assert!(
            validate_uri(
                "postgres://localhost:5543/intelligence_restore_fixture",
                Kind::Research,
                true
            )
            .is_ok()
        );
        assert!(protected(Path::new("/home/ubuntu/minecraft/backup.zip")).is_err());
        for query in [
            "host=example.invalid",
            "hostaddr=203.0.113.1",
            "port=5432",
            "dbname=intelligence",
            "%64bname=intelligence",
        ] {
            let uri = format!("postgres://localhost:5543/intelligence_restore_fixture?{query}");
            assert!(validate_uri(&uri, Kind::Research, true).is_err());
            assert!(validate_uri(&uri, Kind::Research, false).is_err());
        }
    }
    #[test]
    fn incomplete_writer_never_produces_a_publishable_archive() {
        let directory = tempfile::tempdir().unwrap();
        let stage = NamedTempFile::new_in(directory.path()).unwrap();
        let path = stage.path().to_owned();
        let (sender, receiver) = mpsc::channel(4);
        sender.blocking_send(Part::Start("fixture".into())).unwrap();
        sender.blocking_send(Part::Bytes(vec![1, 2, 3])).unwrap();
        drop(sender);
        assert!(write_zip(stage, receiver, Limits::default()).is_err());
        assert!(!path.exists());
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 0);
    }
}
