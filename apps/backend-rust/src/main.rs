//! Existing paper policy starts only with a pinned, approved cutover receipt.
use anyhow::{Context, Result};
use clap::{Parser, Subcommand, ValueEnum};
use mainkraft::models::NumericModel;
use serde::Deserialize;
use std::{hint::black_box, path::PathBuf, time::Instant};

#[derive(Parser)]
#[command(
    name = "mainkraft",
    about = "Native Mainkraft migration verification tools"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    MultiEvaluate {
        #[arg(long,num_args=1..)]
        additional_plan: Vec<PathBuf>,
        #[arg(long,num_args=1..)]
        additional_quotes: Vec<PathBuf>,
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        quotes: PathBuf,
        #[arg(long,required=true,num_args=1..)]
        dataset: Vec<PathBuf>,
        #[arg(long)]
        output: PathBuf,
    },
    MultiQuoteDeclare {
        #[arg(long)]
        start: chrono::NaiveDate,
        #[arg(long)]
        end: chrono::NaiveDate,
        #[arg(long,default_value="sip",value_parser=["sip","iex"])]
        feed: String,
        #[arg(long)]
        output: PathBuf,
    },
    MultiQuoteCollect {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    HourlyFeedProbe {
        #[arg(long)]
        output: PathBuf,
    },
    CheckPaperHealth,
    /// Declare a separate bounded native hourly experiment before acquiring data.
    HourlyDeclare {
        /// Explicit research data arm; historical SIP does not establish current entitlement.
        #[arg(long, default_value = "iex", value_parser = ["iex", "sip"])]
        feed: String,
        #[arg(long)]
        output: PathBuf,
    },
    /// Read-only Alpaca market data. No broker or execution credentials are read.
    HourlyCollect {
        #[arg(long)]
        candles_only: bool,
        #[arg(long)]
        protocol: PathBuf,
        #[arg(long)]
        start: chrono::DateTime<chrono::Utc>,
        #[arg(long)]
        end: chrono::DateTime<chrono::Utc>,
        #[arg(long, default_value = "iex")]
        feed: String,
        #[arg(long, default_value = "iex")]
        quote_feed: String,
        #[arg(long)]
        output: PathBuf,
    },
    /// Native purged walk-forward training and portfolio replay; no promotion.
    HourlyEvaluate {
        #[arg(long)]
        protocol: PathBuf,
        #[arg(long,required=true,num_args=1..)]
        dataset: Vec<PathBuf>,
        #[arg(long,num_args=1..)]
        quote_dataset: Vec<PathBuf>,
        #[arg(long)]
        output: PathBuf,
    },
    /// Existing two-account PAPER service; refuses startup before verified cutover.
    PaperService {
        #[arg(long, default_value = "/models/mainkraft/bundle.json")]
        bundle: PathBuf,
        #[arg(long)]
        cutover_receipt: PathBuf,
        #[arg(long)]
        receipt_sha256: String,
    },
    /// Compatible database archives. Restore authority is local disposable databases only.
    Backup {
        #[arg(value_enum)]
        action: BackupAction,
        #[arg(long, value_enum)]
        kind: ArchiveKind,
        #[arg(long)]
        archive: PathBuf,
        #[arg(long)]
        ack_new_database: Option<String>,
    },
    /// Read and verify existing content-addressed research records without writing.
    VerifyEvidence {
        #[arg(long)]
        root: PathBuf,
        #[arg(long)]
        compressed: bool,
    },
    /// Benchmark an identical, non-submitting numeric parity workload.
    BenchmarkNumeric {
        #[arg(long)]
        fixture: PathBuf,
        #[arg(long, default_value_t = 30)]
        iterations: usize,
    },
}
#[derive(Clone, Copy, ValueEnum)]
enum BackupAction {
    Export,
    Inspect,
    RestoreNew,
    VerifyLocal,
}
#[derive(Clone, Copy, ValueEnum)]
enum ArchiveKind {
    Research,
    Paper,
}
#[derive(Deserialize)]
struct Fixture {
    numeric: Vec<Case>,
}
#[derive(Deserialize)]
struct Case {
    artifact: NumericModel,
    inputs: Vec<Vec<Option<f64>>>,
}

fn main() -> Result<()> {
    match Cli::parse().command {
        Command::MultiEvaluate {
            additional_plan,
            additional_quotes,
            plan,
            quotes,
            dataset,
            output,
        } => {
            let plan: mainkraft::multistrategy::quotes::Plan =
                mainkraft::intraday::research::load(&plan)?;
            let data = mainkraft::multistrategy::study::load_data(&dataset)?;
            let mut plans = vec![plan];
            for path in additional_plan {
                plans.push(mainkraft::intraday::research::load(&path)?);
            }
            let mut directories = vec![quotes];
            directories.extend(additional_quotes);
            let result = mainkraft::multistrategy::study::run_joined(&plans, &directories, &data)?;
            let path = mainkraft::intraday::research::save(&output, "report", &result)?;
            println!(
                "{}",
                serde_json::json!({"report":path,"status":result["status"],"signals":result["signal_count"],"labels":result["matured_quote_backed_labels"],"quote_coverage":result["paired_coverage"],"profitable_advantage_validated":false,"strategy_activated":false})
            );
        }
        Command::MultiQuoteDeclare {
            start,
            end,
            feed,
            output,
        } => {
            let plan = mainkraft::multistrategy::quotes::Plan {
                version: 1,
                declared_at: chrono::Utc::now(),
                start,
                end,
                feed,
                symbols: ["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]
                    .map(str::to_owned)
                    .to_vec(),
                monitoring_minutes: 5,
                maximum_requests: 10000,
            };
            plan.validate()?;
            let path = mainkraft::intraday::research::save(
                &output,
                "protocol",
                &serde_json::to_value(plan)?,
            )?;
            println!(
                "{}",
                serde_json::json!({"plan":path,"execution_authority":false})
            );
        }
        Command::MultiQuoteCollect { plan, output } => {
            let plan: mainkraft::multistrategy::quotes::Plan =
                mainkraft::intraday::research::load(&plan)?;
            plan.validate()?;
            let provider =
                mainkraft::market::AlpacaData::new(mainkraft::market::DataCredentials {
                    key: std::env::var("INTELLIGENCE_ALPACA_KEY")
                        .context("Existing data key required")?,
                    secret: std::env::var("INTELLIGENCE_ALPACA_SECRET")
                        .context("Existing data secret required")?,
                    feed: plan.feed.clone(),
                })?;
            let result = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()?
                .block_on(mainkraft::multistrategy::quotes::collect(
                    &provider, &plan, &output,
                ))?;
            let path = mainkraft::intraday::research::save(&output, "report", &result)?;
            println!("{}", serde_json::json!({"report":path,"result":result}));
        }
        Command::HourlyFeedProbe { output } => {
            let key =
                std::env::var("INTELLIGENCE_ALPACA_KEY").context("Market data key missing")?;
            let secret = std::env::var("INTELLIGENCE_ALPACA_SECRET")
                .context("Market data secret missing")?;
            let report=tokio::runtime::Builder::new_current_thread().enable_all().build()?.block_on(async {
                let mut checks=Vec::new();
                for feed in ["sip","iex"] {
                    let client=mainkraft::market::AlpacaData::new(mainkraft::market::DataCredentials{key:key.clone(),secret:secret.clone(),feed:feed.into()})?;
                    let end=chrono::Utc::now();let start=end-chrono::Duration::minutes(5);
                    checks.push(match client.minute_bars("SPY",1,start,end,false).await {
                        Ok(bars)=>serde_json::json!({"feed":feed,"http_authorized":true,"completed_recent_bars":bars.len(),"fresh_during_open_market_not_established":bars.is_empty()}),
                        Err(e)=>serde_json::json!({"feed":feed,"http_authorized":false,"reason":e.to_string()}),
                    });
                }Ok::<_,mainkraft::Error>(serde_json::json!({"at":chrono::Utc::now(),"checks":checks,"broker_requests":0,"feed_substitution":false}))
            })?;
            let path = mainkraft::intraday::research::save(&output, "report", &report)?;
            println!("{}", serde_json::json!({"report":path,"result":report}));
        }
        Command::CheckPaperHealth => {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            runtime.block_on(async {
                let response = reqwest::Client::builder()
                    .timeout(std::time::Duration::from_secs(3))
                    .build()?
                    .get("http://127.0.0.1:8200/health")
                    .send()
                    .await?
                    .error_for_status()?
                    .json::<serde_json::Value>()
                    .await?;
                anyhow::ensure!(
                    response == serde_json::json!({"status":"ok","execution":"paper_only"}),
                    "Paper health contract failed"
                );
                Ok::<_, anyhow::Error>(())
            })?;
        }
        Command::HourlyDeclare { output, feed } => {
            let protocol =
                mainkraft::intraday::research::Protocol::new_with_feed(chrono::Utc::now(), &feed)?;
            let path = mainkraft::intraday::research::save(
                &output,
                "protocol",
                &serde_json::to_value(protocol)?,
            )?;
            println!(
                "{}",
                serde_json::json!({"protocol":path,"execution_authority":false})
            );
        }
        Command::HourlyCollect {
            candles_only,
            protocol,
            start,
            end,
            feed,
            quote_feed,
            output,
        } => {
            let protocol: mainkraft::intraday::research::Protocol =
                mainkraft::intraday::research::load(&protocol)?;
            protocol.validate()?;
            anyhow::ensure!(
                protocol.search["candle_feed"].as_str() == Some(feed.as_str())
                    && protocol.search["execution_quote_feed"].as_str()
                        == Some(quote_feed.as_str()),
                "Collection feed must match the explicit declaration; no fallback"
            );
            anyhow::ensure!(
                start.date_naive() >= protocol.development_start
                    && end.date_naive()
                        <= protocol
                            .development_end
                            .succ_opt()
                            .context("Development end")?,
                "Collection must stay in the declared development window"
            );
            let mut symbols = protocol.symbols.clone();
            symbols.extend(["SPY".into(), "XLK".into()]);
            let data = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()?
                .block_on(mainkraft::intraday::research::collect(
                    start,
                    end,
                    feed,
                    quote_feed,
                    &symbols,
                    !candles_only,
                ))?;
            let counts = serde_json::json!({"candles":data.candles.len(),"quotes":data.quotes.len(),"failures":data.failures});
            let path = mainkraft::intraday::research::save(
                &output,
                "dataset",
                &serde_json::to_value(data)?,
            )?;
            println!(
                "{}",
                serde_json::json!({"dataset":path,"counts":counts,"broker_requests":0})
            );
        }
        Command::HourlyEvaluate {
            protocol,
            dataset,
            quote_dataset,
            output,
        } => {
            let protocol: mainkraft::intraday::research::Protocol =
                mainkraft::intraday::research::load(&protocol)?;
            let mut data: Option<mainkraft::intraday::data::Dataset> = None;
            for path in dataset {
                anyhow::ensure!(
                    std::fs::metadata(&path)?.len() < 256 * 1024 * 1024,
                    "Dataset resource boundary"
                );
                let next: mainkraft::intraday::data::Dataset =
                    mainkraft::intraday::research::load(&path)?;
                if let Some(d) = &mut data {
                    anyhow::ensure!(
                        d.candle_feed == next.candle_feed && d.quote_feed == next.quote_feed,
                        "Do not mix feeds"
                    );
                    d.start = d.start.min(next.start);
                    d.end = d.end.max(next.end);
                    d.collected_at = d.collected_at.max(next.collected_at);
                    d.candles.extend(next.candles);
                    d.quotes.extend(next.quotes);
                    d.news.extend(next.news);
                    d.failures.extend(next.failures);
                } else {
                    data = Some(next);
                }
            }
            let mut data = data.context("No datasets")?;
            anyhow::ensure!(
                data.candle_feed == protocol.search["candle_feed"].as_str().unwrap_or(""),
                "Dataset feed differs from the declared hourly experiment"
            );
            for path in quote_dataset {
                let extra: mainkraft::intraday::data::Dataset =
                    mainkraft::intraday::research::load(&path)?;
                anyhow::ensure!(
                    extra.quote_feed == data.quote_feed,
                    "Execution quote feeds differ"
                );
                data.quotes.extend(extra.quotes);
                data.failures.push(serde_json::json!({"kind":"quote_only_archive","candle_feed_ignored":extra.candle_feed,"candle_rows_not_used":extra.candles.len()}));
            }
            let report = mainkraft::intraday::research::run(
                &mainkraft::calendar::Calendar::embedded()?,
                &data,
                &protocol,
            )?;
            let path = mainkraft::intraday::research::save(&output, "report", &report)?;
            println!(
                "{}",
                serde_json::json!({"report":path,"status":report["status"],"sessions":report["sessions"],"samples":report["samples"],"profitable_advantage_validated":false,"strategy_activated":false})
            );
        }
        Command::PaperService {
            bundle,
            cutover_receipt,
            receipt_sha256,
        } => {
            tracing_subscriber::fmt()
                .json()
                .with_env_filter("info")
                .init();
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()?
                .block_on(mainkraft::runtime::paper(
                    &bundle,
                    &cutover_receipt,
                    &receipt_sha256,
                ))?;
        }
        Command::Backup {
            action,
            kind,
            archive,
            ack_new_database,
        } => {
            let (kind, variable) = match kind {
                ArchiveKind::Research => (
                    mainkraft::backup::Kind::Research,
                    "INTELLIGENCE_DATABASE_URL",
                ),
                ArchiveKind::Paper => (
                    mainkraft::backup::Kind::Paper,
                    "INTELLIGENCE_PAPER_DATABASE_URL",
                ),
            };
            let backup = mainkraft::backup::Backup::new(
                std::sync::Arc::new(mainkraft::engine::SystemClock),
                mainkraft::backup::Limits::default(),
            )?;
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()?;
            let result: serde_json::Value = runtime.block_on(async {
                if matches!(action, BackupAction::Inspect) {
                    return Ok::<_, anyhow::Error>(serde_json::to_value(
                        backup.inspect(&archive, kind).await?,
                    )?);
                }
                // No credentials in argv or output, no implicit .env loading/default target.
                let uri = std::env::var(variable)
                    .with_context(|| format!("Set {variable} explicitly"))?;
                Ok(match action {
                    BackupAction::Export => backup.export(&uri, kind, &archive).await?,
                    BackupAction::RestoreNew => {
                        backup
                            .restore_empty(
                                &uri,
                                kind,
                                ack_new_database
                                    .as_deref()
                                    .context("--ack-new-database is required")?,
                                &archive,
                            )
                            .await?
                    }
                    BackupAction::VerifyLocal => backup.verify_local(&uri, kind, &archive).await?,
                    BackupAction::Inspect => unreachable!(),
                })
            })?;
            println!("{}", serde_json::to_string_pretty(&result)?);
        }
        Command::VerifyEvidence { root, compressed } => {
            let mut count = 0usize;
            for entry in
                std::fs::read_dir(root.join(if compressed { "compressed" } else { "records" }))
                    .context("evidence folder")?
            {
                let entry = entry.context("evidence entry")?;
                let path = entry.path();
                if compressed
                    && path
                        .file_name()
                        .is_some_and(|s| s.to_string_lossy().ends_with(".json.gz"))
                {
                    let key = path
                        .file_name()
                        .and_then(|s| s.to_str())
                        .and_then(|s| s.strip_suffix(".json.gz"))
                        .context("compressed ID")?;
                    mainkraft::evidence::Store::new(
                        &root,
                        mainkraft::evidence::Family::Compressed,
                        256 * 1024 * 1024,
                    )?
                    .record(key)
                    .with_context(|| format!("compressed integrity failed: {key}"))?;
                    count += 1;
                } else if !compressed && path.extension().is_some_and(|ext| ext == "json") {
                    let key = path
                        .file_stem()
                        .and_then(|v| v.to_str())
                        .context("evidence ID")?;
                    mainkraft::report_api::read_record(&root, key)
                        .with_context(|| format!("evidence integrity failed: {key}"))?;
                    count += 1;
                }
            }
            println!(
                "{}",
                serde_json::json!({"verified_records":count,"writes":0,"execution_authority":false})
            );
        }
        Command::BenchmarkNumeric {
            fixture,
            iterations,
        } => {
            let data: Fixture =
                serde_json::from_slice(&std::fs::read(fixture)?).context("numeric fixture")?;
            // Warm up once outside the timed loop, matching the Python harness.
            for case in &data.numeric {
                for row in &case.inputs {
                    black_box(case.artifact.predict(row)?);
                }
            }
            let start = Instant::now();
            let mut checksum = 0.0;
            let mut predictions = 0usize;
            for _ in 0..iterations {
                for case in &data.numeric {
                    for row in &case.inputs {
                        let prediction = case.artifact.predict(black_box(row))?;
                        checksum +=
                            prediction.expected_net_return + prediction.probability_positive;
                        predictions += 1;
                    }
                }
            }
            let seconds = start.elapsed().as_secs_f64();
            println!(
                "{}",
                serde_json::json!({"implementation":"rust","scope":"single-row numeric inference only; not full trading service","iterations":iterations,
                "predictions":predictions,"compute_wall_seconds":seconds,"predictions_per_second":predictions as f64/seconds,"checksum":checksum})
            );
        }
    }
    Ok(())
}
