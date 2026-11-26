//! Startup for the existing two-account PAPER policy. No strategy promotion.
use crate::{
    Result,
    broker::{AccountConfig, BrokerApi, PaperBroker},
    calendar::Calendar,
    discovery::{Research, Scheduler},
    engine::{AccountPolicy, Engine, SystemClock},
    invalid,
    market::{AlpacaData, Bundle, DataCredentials, Market},
    paper_api, ranking, sentiment,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Read,
    path::Path,
    sync::Arc,
};

pub struct PaperConfig {
    database: String,
    token: String,
    accounts: Vec<AccountConfig>,
    data: DataCredentials,
    groq_key: String,
    groq_model: String,
    sentiment_url: String,
    sentiment: bool,
    discovery: bool,
}
impl std::fmt::Debug for PaperConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PaperConfig")
            .field("credentials", &"[redacted]")
            .finish()
    }
}
fn required(env: &BTreeMap<String, String>, name: &str) -> Result<String> {
    env.get(name)
        .filter(|v| !v.trim().is_empty() && v.len() <= 8192)
        .cloned()
        .ok_or_else(|| invalid(format!("Missing or invalid {name}")))
}
fn flag(env: &BTreeMap<String, String>, name: &str) -> Result<bool> {
    match env
        .get(name)
        .map(|s| s.trim().to_ascii_lowercase())
        .as_deref()
    {
        None | Some("false" | "0" | "off" | "no") => Ok(false),
        Some("true" | "1" | "on" | "yes") => Ok(true),
        _ => Err(invalid(format!("Invalid {name}"))),
    }
}
fn bounded_read(path: &Path, limit: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)?
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(invalid("Runtime artifact exceeds size limit"));
    }
    Ok(bytes)
}
impl PaperConfig {
    pub fn from_sources(env: &BTreeMap<String, String>, second: &[u8]) -> Result<Self> {
        if !flag(env, "INTELLIGENCE_PAPER_EXPERIMENT_ENABLED")? {
            return Err(invalid(
                "This runtime implements the existing two-account PAPER experiment only",
            ));
        }
        if env
            .get("INTELLIGENCE_PAPER_BASE_URL")
            .is_some_and(|v| v != crate::broker::PAPER_HOST)
        {
            return Err(invalid(
                "Only the literal Alpaca PAPER endpoint is supported",
            ));
        }
        if second.len() > 65536 {
            return Err(invalid("Account configuration exceeds size limit"));
        }
        let second: serde_json::Value =
            serde_json::from_slice(second).map_err(|_| invalid("Invalid second-account JSON"))?;
        let field = |key: &str| {
            second[key]
                .as_str()
                .filter(|v| !v.trim().is_empty() && v.len() <= 8192)
                .map(str::to_owned)
                .ok_or_else(|| invalid("Incomplete second-account configuration"))
        };
        let accounts = vec![
            AccountConfig {
                id: required(env, "INTELLIGENCE_PAPER_ACCOUNT_ID")?,
                key: required(env, "INTELLIGENCE_PAPER_KEY")?,
                secret: required(env, "INTELLIGENCE_PAPER_SECRET")?,
                account_number: None,
                label: "Original account · fixed model".into(),
                adaptive: false,
            },
            AccountConfig {
                id: field("account_id")?,
                key: field("key")?,
                secret: field("secret")?,
                account_number: Some(field("expected_account_number")?),
                label: "Second account · online learning".into(),
                adaptive: true,
            },
        ];
        if accounts[0].id == accounts[1].id {
            return Err(invalid("Distinct pinned paper accounts required"));
        }
        let database = required(env, "INTELLIGENCE_PAPER_DATABASE_URL")?;
        let parsed =
            url::Url::parse(&database).map_err(|_| invalid("Invalid paper database URI"))?;
        if !matches!(parsed.scheme(), "postgres" | "postgresql")
            || parsed.path() != "/intelligence_paper"
        {
            return Err(invalid(
                "Runtime must use the existing isolated paper database",
            ));
        }
        let feed = env
            .get("INTELLIGENCE_FEED")
            .cloned()
            .unwrap_or("sip".into());
        if !matches!(feed.as_str(), "sip" | "iex") {
            return Err(invalid("Unsupported feed; no substitution"));
        }
        let discovery = flag(env, "INTELLIGENCE_PAPER_DISCOVERY_ENABLED")?;
        let sentiment = flag(env, "INTELLIGENCE_PAPER_SENTIMENT_ENABLED")?;
        let groq_key = if discovery {
            required(env, "INTELLIGENCE_GROQ_KEY")?
        } else {
            String::new()
        };
        let sentiment_url = env
            .get("INTELLIGENCE_PAPER_SENTIMENT_URL")
            .cloned()
            .unwrap_or("http://sentiment:8300".into());
        if sentiment {
            sentiment::HttpTransport::new(sentiment_url.clone())?;
        }
        Ok(Self {
            database,
            token: required(env, "INTELLIGENCE_PAPER_INTERNAL_TOKEN")?,
            accounts,
            data: DataCredentials {
                key: required(env, "INTELLIGENCE_ALPACA_KEY")?,
                secret: required(env, "INTELLIGENCE_ALPACA_SECRET")?,
                feed,
            },
            groq_key,
            groq_model: env
                .get("INTELLIGENCE_GROQ_MODEL")
                .cloned()
                .unwrap_or("openai/gpt-oss-120b".into()),
            sentiment_url,
            sentiment,
            discovery,
        })
    }
    pub fn from_environment() -> Result<Self> {
        // No implicit .env, fallback database or credential rewrite.
        let env: BTreeMap<_, _> = std::env::vars()
            .filter(|(k, _)| k.starts_with("INTELLIGENCE_"))
            .collect();
        let path = required(&env, "INTELLIGENCE_PAPER_EXPERIMENT_ACCOUNTS_FILE")?;
        Self::from_sources(&env, &bounded_read(Path::new(&path), 65536)?)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Cutover {
    version: u32,
    policy: String,
    account_ids: Vec<String>,
    binary_sha256: String,
    bundle_sha256: String,
    database_target: String,
    operator_approved: bool,
    checks: BTreeMap<String, bool>,
}
pub(crate) struct VerifiedCutover {
    _verified: (),
}
fn verify_cutover(
    bytes: &[u8],
    expected: &str,
    config: &PaperConfig,
    binary: &[u8],
    bundle: &[u8],
) -> Result<VerifiedCutover> {
    if hex::encode(Sha256::digest(bytes)) != expected {
        return Err(invalid("Cutover receipt hash mismatch"));
    }
    let receipt: Cutover =
        serde_json::from_slice(bytes).map_err(|_| invalid("Invalid cutover receipt"))?;
    let required: BTreeSet<_> = [
        "paper_policy_parity",
        "order_recovery",
        "database_backup_restore",
        "artifact_rollback",
        "release_health_resources",
        "python_worker_stopped",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    let database =
        url::Url::parse(&config.database).map_err(|_| invalid("Paper database target"))?;
    let target = format!(
        "{}:{}{}",
        database
            .host_str()
            .ok_or_else(|| invalid("Paper database host"))?,
        database.port().unwrap_or(5432),
        database.path()
    );
    if receipt.version != 1
        || receipt.database_target != target
        || receipt.policy != paper_api::VERSION
        || !receipt.operator_approved
        || receipt.account_ids
            != config
                .accounts
                .iter()
                .map(|a| a.id.clone())
                .collect::<Vec<_>>()
        || receipt.binary_sha256 != hex::encode(Sha256::digest(binary))
        || receipt.bundle_sha256 != hex::encode(Sha256::digest(bundle))
        || receipt.checks.keys().cloned().collect::<BTreeSet<_>>() != required
        || receipt.checks.values().any(|v| !*v)
    {
        return Err(invalid(
            "Mandatory paper cutover requirements have not passed",
        ));
    }
    Ok(VerifiedCutover { _verified: () })
}
struct DisabledRanker;
#[async_trait::async_trait]
impl ranking::Transport for DisabledRanker {
    async fn complete(&self, _: &serde_json::Value) -> Result<(u16, Vec<u8>)> {
        Err(invalid("Discovery disabled"))
    }
}

/// All authority checks precede database migration, network acquisition or intent creation.
pub async fn paper(bundle_path: &Path, receipt_path: &Path, receipt_sha: &str) -> Result<()> {
    let config = PaperConfig::from_environment()?;
    let bundle_bytes = bounded_read(bundle_path, 16 * 1024 * 1024)?;
    let bundle = Arc::new(Bundle::from_json(&bundle_bytes)?);
    let receipt = bounded_read(receipt_path, 65536)?;
    let binary = bounded_read(&std::env::current_exe()?, 256 * 1024 * 1024)?;
    let verified = verify_cutover(&receipt, receipt_sha, &config, &binary, &bundle_bytes)?;
    let pool = crate::db::pool(&config.database, true).await?;
    let registered: Vec<(String, String)> = sqlx::query_as(
        "SELECT account_id,account_number FROM learning_accounts ORDER BY account_id",
    )
    .fetch_all(&pool)
    .await?;
    let actual: BTreeSet<_> = registered.iter().map(|a| a.0.as_str()).collect();
    let expected: BTreeSet<_> = config.accounts.iter().map(|a| a.id.as_str()).collect();
    if actual != expected
        || config.accounts.iter().any(|a| {
            a.account_number.as_ref().is_some_and(|n| {
                !registered
                    .iter()
                    .any(|(id, number)| id == &a.id && number == n)
            })
        })
    {
        return Err(invalid(
            "Existing paper account ledger identity mismatch; no new ledger may be bootstrapped at cutover",
        ));
    }
    crate::db::migrate_paper(&pool).await?;
    let clock = Arc::new(SystemClock);
    let calendar = Arc::new(Calendar::embedded()?);
    let mut policies = Vec::new();
    let mut brokers = BTreeMap::new();
    let mut bridge = Vec::new();
    for account in config.accounts {
        policies.push(AccountPolicy {
            id: account.id.clone(),
            label: account.label.clone(),
            adaptive: account.adaptive,
        });
        let id = account.id.clone();
        let broker: Arc<dyn BrokerApi> = Arc::new(PaperBroker::with_cutover(account, &verified)?);
        broker.account().await?;
        brokers.insert(id, broker.clone());
        bridge.push(broker);
    }
    let feed = config.data.feed.clone();
    let data = Arc::new(AlpacaData::new(config.data)?);
    let market = Arc::new(Market::new(
        pool.clone(),
        data.clone(),
        clock.clone(),
        calendar.clone(),
        bundle,
        feed.clone(),
    ));
    let transport: Arc<dyn ranking::Transport> = if config.discovery {
        Arc::new(ranking::HttpTransport::new(&config.groq_key)?)
    } else {
        Arc::new(DisabledRanker)
    };
    let scheduler = Arc::new(Scheduler {
        enabled: config.discovery,
        attempted: tokio::sync::Mutex::new(None),
        research: Arc::new(Research {
            pool: pool.clone(),
            broker: bridge[0].clone(),
            data,
            ranker: Arc::new(ranking::Ranker {
                pool: pool.clone(),
                model: config.groq_model,
                transport,
                clock: clock.clone(),
            }),
            sentiment: if config.sentiment {
                Some(Arc::new(sentiment::HttpTransport::new(
                    config.sentiment_url,
                )?))
            } else {
                None
            },
            clock: clock.clone(),
            calendar: calendar.clone(),
            feed,
        }),
    });
    let engine = Engine::new(pool.clone(), policies, brokers, market, clock, calendar)?;
    let router = paper_api::router(paper_api::PaperState {
        pool: pool.clone(),
        token: config.token,
        brokers: bridge,
    });
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8200").await?;
    let (sender, receiver) = tokio::sync::watch::channel(false);
    let signal = tokio::spawn(async move {
        shutdown_signal().await;
        sender.send_replace(true);
    });
    let result = crate::service::paper(engine, scheduler, router, listener, receiver).await;
    signal.abort();
    let _ = signal.await;
    pool.close().await;
    result
}
async fn shutdown_signal() {
    #[cfg(unix)]
    {
        if let Ok(mut terminate) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            tokio::select! {_=tokio::signal::ctrl_c()=>{},_=terminate.recv()=>{}};
            return;
        }
    }
    let _ = tokio::signal::ctrl_c().await;
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (BTreeMap<String, String>, Vec<u8>) {
        let env = [
            ("INTELLIGENCE_PAPER_EXPERIMENT_ENABLED", "true"),
            ("INTELLIGENCE_PAPER_ACCOUNT_ID", "first-pinned"),
            ("INTELLIGENCE_PAPER_KEY", "fixture-first-key"),
            ("INTELLIGENCE_PAPER_SECRET", "fixture-first-secret"),
            ("INTELLIGENCE_ALPACA_KEY", "fixture-data-key"),
            ("INTELLIGENCE_ALPACA_SECRET", "fixture-data-secret"),
            ("INTELLIGENCE_PAPER_INTERNAL_TOKEN", "fixture-private-token"),
            (
                "INTELLIGENCE_PAPER_DATABASE_URL",
                "postgresql://fixture:fixture-pass@paper-postgres:5432/intelligence_paper",
            ),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), v.into()))
        .collect();
        (env,br#"{"account_id":"second-pinned","key":"fixture-second-key","secret":"fixture-second-secret","expected_account_number":"TEST-2"}"#.to_vec())
    }
    #[test]
    fn startup_rejects_live_endpoints_duplicate_accounts_and_secret_debug_output() {
        let (mut env, second) = fixture();
        let config = PaperConfig::from_sources(&env, &second).unwrap();
        let debug = format!("{config:?}");
        assert!(!debug.contains("fixture"));
        assert!(debug.contains("redacted"));
        env.insert(
            "INTELLIGENCE_PAPER_BASE_URL".into(),
            "https://api.alpaca.markets".into(),
        );
        assert!(PaperConfig::from_sources(&env, &second).is_err());
        env.remove("INTELLIGENCE_PAPER_BASE_URL");
        env.insert(
            "INTELLIGENCE_PAPER_ACCOUNT_ID".into(),
            "second-pinned".into(),
        );
        assert!(PaperConfig::from_sources(&env, &second).is_err());
    }
    #[test]
    fn receipt_requires_all_final_gates_explicit_approval_and_exact_binary_model_database_accounts()
    {
        let (env, second) = fixture();
        let config = PaperConfig::from_sources(&env, &second).unwrap();
        let checks: [(&str, bool); 6] = [
            ("paper_policy_parity", true),
            ("order_recovery", true),
            ("database_backup_restore", true),
            ("artifact_rollback", true),
            ("release_health_resources", true),
            ("python_worker_stopped", true),
        ];
        let mut receipt = serde_json::json!({"version":1,"policy":paper_api::VERSION,"account_ids":["first-pinned","second-pinned"],"binary_sha256":hex::encode(Sha256::digest(b"binary")),"bundle_sha256":hex::encode(Sha256::digest(b"model")),
            "database_target":"paper-postgres:5432/intelligence_paper","operator_approved":false,"checks":checks.into_iter().collect::<BTreeMap<_,_>>()});
        let verify = |value: &serde_json::Value, binary: &[u8]| {
            let bytes = serde_json::to_vec(value).unwrap();
            verify_cutover(
                &bytes,
                &hex::encode(Sha256::digest(&bytes)),
                &config,
                binary,
                b"model",
            )
        };
        assert!(verify(&receipt, b"binary").is_err());
        receipt["operator_approved"] = serde_json::json!(true);
        assert!(verify(&receipt, b"binary").is_ok());
        assert!(verify(&receipt, b"changed").is_err());
        receipt["checks"]["python_worker_stopped"] = serde_json::json!(false);
        assert!(verify(&receipt, b"binary").is_err());
        receipt["checks"]["python_worker_stopped"] = serde_json::json!(true);
        receipt["database_target"] = serde_json::json!("another-host:5432/intelligence_paper");
        assert!(verify(&receipt, b"binary").is_err());
    }
}
