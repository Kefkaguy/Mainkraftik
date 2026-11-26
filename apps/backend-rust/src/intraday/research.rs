//! Predeclared hourly development experiment. Old exposed audit history is not
//! selection data; future confirmation remains inaccessible to model selection.
use super::{
    allocation::{Costs, Risk},
    data::{Dataset, QuoteSample},
    features::{self, Candle},
    training::Sample,
};
use crate::{
    Result,
    calendar::Calendar,
    canonical, invalid,
    market::{AlpacaData, DataApi, DataCredentials},
};
use chrono::{DateTime, Duration, NaiveDate, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Protocol {
    pub source_sha256: String,
    pub search: Value,
    pub version: u32,
    pub declared_at: DateTime<Utc>,
    pub development_start: NaiveDate,
    pub development_end: NaiveDate,
    pub confirmation_not_before: DateTime<Utc>,
    pub capital: f64,
    pub train_sessions: usize,
    pub calibration_sessions: usize,
    pub test_sessions: usize,
    pub maximum_configurations: usize,
    pub minimum_confirmation_sessions: usize,
    pub minimum_closed_trades: usize,
    pub risk: Risk,
    pub costs: Costs,
    pub symbols: Vec<String>,
}
impl Protocol {
    pub fn new(at: DateTime<Utc>) -> Result<Self> {
        Self::new_with_feed(at, "iex")
    }
    pub fn new_with_feed(at: DateTime<Utc>, feed: &str) -> Result<Self> {
        if !["iex", "sip"].contains(&feed) {
            return Err(invalid("Explicit IEX or SIP research feed required"));
        }
        Ok(Self {
            source_sha256: implementation_hash(),
            search: search_specification(feed),
            version: 1,
            declared_at: at,
            development_start: NaiveDate::from_ymd_opt(2025, 2, 3)
                .ok_or_else(|| invalid("Protocol date"))?,
            development_end: NaiveDate::from_ymd_opt(2025, 4, 2)
                .ok_or_else(|| invalid("Protocol date"))?,
            confirmation_not_before: at,
            capital: 1000.,
            train_sessions: 20,
            calibration_sessions: 5,
            test_sessions: 5,
            maximum_configurations: 128,
            minimum_confirmation_sessions: 60,
            minimum_closed_trades: 30,
            risk: Risk::default(),
            costs: Costs::default(),
            symbols: ["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
        })
    }
    pub fn validate(&self) -> Result<()> {
        let protected =
            NaiveDate::from_ymd_opt(2025, 4, 3).ok_or_else(|| invalid("Protected date"))?;
        if self.version != 1
            || self.source_sha256 != implementation_hash()
            || !matches!(self.search["candle_feed"].as_str(), Some("iex" | "sip"))
            || self.search
                != search_specification(self.search["candle_feed"].as_str().unwrap_or(""))
            || self.development_start >= self.development_end
            || self.development_end >= protected
            || self.confirmation_not_before < self.declared_at
            || self.capital != 1000.
            || serde_json::to_value(self.risk)? != serde_json::to_value(Risk::default())?
            || serde_json::to_value(self.costs)? != serde_json::to_value(Costs::default())?
            || self.train_sessions < 20
            || self.calibration_sessions < 5
            || self.test_sessions < 5
            || self.maximum_configurations > 128
            || self.minimum_confirmation_sessions < 60
            || self.minimum_closed_trades < 30
        {
            return Err(invalid(
                "Intraday declaration violates preserved audit/risk boundaries",
            ));
        }
        Ok(())
    }
}
#[cfg(test)]
mod protocol_tests {
    use super::*;

    #[test]
    fn explicit_feed_preserves_search_risk_and_protected_period() -> Result<()> {
        for feed in ["iex", "sip"] {
            let mut p = Protocol::new_with_feed(Utc::now(), feed)?;
            p.validate()?;
            assert_eq!(p.search["candle_feed"], feed);
            p.risk.gross_fraction = 2.;
            assert!(p.validate().is_err());
            p.risk = Risk::default();
            p.costs.slippage_bps = 0.;
            assert!(p.validate().is_err());
            p.costs = Costs::default();
            p.development_end = NaiveDate::from_ymd_opt(2025, 4, 3).unwrap();
            assert!(p.validate().is_err());
        }
        assert!(Protocol::new_with_feed(Utc::now(), "delayed_sip").is_err());
        Ok(())
    }
}
fn implementation_hash() -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    for source in [
        include_bytes!("research.rs").as_slice(),
        include_bytes!("study.rs").as_slice(),
        include_bytes!("features.rs").as_slice(),
        include_bytes!("training.rs").as_slice(),
        include_bytes!("models.rs").as_slice(),
        include_bytes!("allocation.rs").as_slice(),
        include_bytes!("evaluation.rs").as_slice(),
        include_bytes!("engine.rs").as_slice(),
        include_bytes!("data.rs").as_slice(),
        include_bytes!("learning.rs").as_slice(),
        include_bytes!("../market.rs").as_slice(),
        include_bytes!("../../Cargo.lock").as_slice(),
    ] {
        h.update(source);
    }
    hex::encode(h.finalize())
}
fn search_specification(feed: &str) -> Value {
    json!({"candle_feed":feed,"execution_quote_feed":"iex","families":["ridge","logistic","histogram_gradient_boosting"],"alpha":[1.,10.],"feature_groups":["returns","price_volume_market"],
        "rebalance_minutes":[5,15,30,60],"probability_weighting":[false,true],"adaptive_alpha":[1.,10.,100.],
        "adaptive_modes":["all_history","rolling_256","rolling_1024","recency_60m","recency_240m","regime_256"],
        "execution":{"latency_seconds":1,"order_ttl_seconds":60,"participation_of_displayed_size":0.01,"fill_fraction":0.5,"quote_grid_second":16},
        "label":"60 completed one-minute close-to-close bars; executable returns measured separately",
        "slippage_stress_bps":[1.,2.,5.,10.],"bootstrap":{"samples":10000,"block_sessions":5,"familywise_comparisons":128},
        "new_short_entries":"blocked_without_historical_borrow_availability","prior_audit":"2025-04-03 through declaration excluded"})
}
pub fn save(directory: &Path, kind: &str, value: &Value) -> Result<PathBuf> {
    if !["protocol", "dataset", "report", "models"].contains(&kind) {
        return Err(invalid("Invalid intraday artifact kind"));
    }
    std::fs::create_dir_all(directory)?;
    let bytes = canonical::canonical(value)?;
    let id = canonical::digest(value)?;
    let path = directory.join(format!("{kind}-{id}.json"));
    let mut staged = tempfile::NamedTempFile::new_in(directory)?;
    staged.write_all(bytes.as_bytes())?;
    staged.as_file().sync_all()?;
    if let Err(error) = staged.persist_noclobber(&path) {
        if error.error.kind() != std::io::ErrorKind::AlreadyExists {
            return Err(error.error.into());
        }
        if std::fs::read(&path)? != bytes.as_bytes() {
            return Err(invalid("Immutable intraday artifact mismatch"));
        }
    }
    Ok(path)
}
pub fn load<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T> {
    if std::fs::metadata(path)?.len() > 256 * 1024 * 1024 {
        return Err(invalid("Intraday artifact resource boundary"));
    }
    let value: Value = serde_json::from_slice(&std::fs::read(path)?)?;
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or_else(|| invalid("Intraday artifact filename"))?;
    let (kind, id) = stem
        .split_once('-')
        .ok_or_else(|| invalid("Content-addressed intraday artifact required"))?;
    if !["protocol", "dataset", "report", "models"].contains(&kind)
        || canonical::digest(&value)? != id
    {
        return Err(invalid("Intraday artifact content identity mismatch"));
    }
    Ok(serde_json::from_value(value)?)
}
pub async fn collect(
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    feed: String,
    quote_feed: String,
    symbols: &[String],
    include_quotes: bool,
) -> Result<Dataset> {
    if symbols.len() > 8 || end - start > Duration::days(7) || end <= start {
        return Err(invalid(
            "Bounded collection requires at most 8 symbols and 7 days",
        ));
    }
    let credentials = DataCredentials {
        key: std::env::var("INTELLIGENCE_ALPACA_KEY")
            .map_err(|_| invalid("Market data key unavailable"))?,
        secret: std::env::var("INTELLIGENCE_ALPACA_SECRET")
            .map_err(|_| invalid("Market data secret unavailable"))?,
        feed: feed.clone(),
    };
    let provider = AlpacaData::new(credentials)?;
    let mut candles = Vec::new();
    let mut quotes = Vec::new();
    let mut failures = Vec::new();
    for symbol in symbols {
        for minutes in [1, 5] {
            match provider.minute_bars(symbol,minutes,start,end,true).await {
                Ok(mut rows)=>candles.append(&mut rows),Err(e)=>failures.push(json!({"symbol":symbol,"kind":"candles","minutes":minutes,"feed":feed,"reason":e.to_string()})),
            }
        }
        if include_quotes {
            match provider.historical_quote_samples(symbol,start,end,&quote_feed).await {
            Ok(mut rows)=>quotes.append(&mut rows),Err(e)=>failures.push(json!({"symbol":symbol,"kind":"historical_quotes","feed":quote_feed,"reason":e.to_string()})),
        }
            // Explicit current entitlement/freshness check; never backdate it into history.
            match provider.latest_quote(symbol).await {
            Ok(q)=>{let now=Utc::now();let stamp=q["t"].as_str().and_then(|s|s.parse::<DateTime<Utc>>().ok());
                failures.push(json!({"symbol":symbol,"kind":"current_iex_quote_permission","http_authorized":true,"fresh_within_15_seconds":stamp.is_some_and(|t|t<=now&&now-t<=Duration::seconds(15)),"source_time":stamp,"observed_at":now,"used_in_historical_features":false}));},
            Err(e)=>failures.push(json!({"symbol":symbol,"kind":"current_iex_quote_permission","http_authorized":false,"reason":e.to_string()})),
        }
        }
    }
    Ok(Dataset{version:1,collected_at:Utc::now(),candle_feed:feed,quote_feed,start,end,candles,quotes,news:Vec::new(),failures,
        data_assumptions:vec!["Historical candle availability is reconstructed as close + 1 second, not observed prospectively".into(),
        "Historical quotes retain real collection time; replay explicitly assumes one-second publication latency".into(),
        "Quote sizes are shares from 2025-11-03; older round lots converted per provider specification".into(),
        "IEX quotes are venue evidence, not consolidated NBBO or actual broker fills".into(),
        "No timestamp-valid archived news/sentiment supplied: news features remain missing, never backdated".into()]})
}
pub(crate) fn replay_quote(sample: &QuoteSample) -> crate::features::Quote {
    let mut q = sample.quote.clone();
    if sample.historical_reconstruction {
        q.acquired_at = q.source_time + Duration::seconds(1);
    }
    q
}
pub type QuoteIndex = BTreeMap<(DateTime<Utc>, String), QuoteSample>;
pub fn panel(
    calendar: &Calendar,
    data: &Dataset,
    protocol: &Protocol,
) -> Result<(Vec<Sample>, QuoteIndex, usize)> {
    protocol.validate()?;
    let mut quotes = BTreeMap::new();
    for sample in &data.quotes {
        let day = sample
            .quote
            .source_time
            .with_timezone(&New_York)
            .date_naive();
        if day < protocol.development_start || day > protocol.development_end {
            continue;
        }
        let stamp = Utc
            .timestamp_opt(
                sample.quote.source_time.timestamp().div_euclid(60) * 60 + 16,
                0,
            )
            .single()
            .ok_or_else(|| invalid("Quote time"))?;
        if sample.quote.source_time + Duration::seconds(1) <= stamp
            && sample.quote.source_time >= stamp - Duration::seconds(15)
        {
            quotes.insert((stamp, sample.symbol.clone()), sample.clone());
        }
    }
    let mut indexed = BTreeMap::<String, Vec<Candle>>::new();
    let mut close = BTreeMap::new();
    let mut unique = BTreeMap::<(String, DateTime<Utc>, u8), &Candle>::new();
    for c in &data.candles {
        let key = (c.symbol.clone(), c.start, c.minutes);
        if let Some(old) = unique.get(&key) {
            if [old.open, old.high, old.low, old.close, old.volume]
                != [c.open, c.high, c.low, c.close, c.volume]
                || old.vwap != c.vwap
                || old.feed != c.feed
            {
                return Err(invalid(
                    "Conflicting historical candle revision; select an explicit vintage",
                ));
            }
        } else {
            unique.insert(key, c);
        }
    }
    for c in unique.into_values() {
        c.validate()?;
        if c.start.with_timezone(&New_York).date_naive() < protocol.development_start
            || c.start.with_timezone(&New_York).date_naive() > protocol.development_end
        {
            continue;
        }
        if c.minutes == 1 {
            close.insert((c.symbol.clone(), c.end()), c.close);
        }
        indexed.entry(c.symbol.clone()).or_default().push(c.clone());
    }
    let mut result = Vec::new();
    let mut skipped = 0;
    for symbol in &protocol.symbols {
        let Some(own) = indexed.get(symbol) else {
            continue;
        };
        for bar in own
            .iter()
            .filter(|c| c.minutes == 1 && c.end().timestamp().rem_euclid(300) == 0)
        {
            let at = bar.end() + Duration::seconds(16);
            let target = at + Duration::minutes(60);
            let day = at.with_timezone(&New_York).date_naive();
            let Some(session) = calendar.session(day) else {
                continue;
            };
            if target > session.close {
                continue;
            }
            let Some(future) = close.get(&(symbol.clone(), bar.end() + Duration::minutes(60)))
            else {
                continue;
            };
            let mut context = Vec::new();
            for name in [symbol.as_str(), "SPY", "XLK"] {
                if let Some(rows) = indexed.get(name) {
                    context.extend(
                        rows.iter()
                            .filter(|r| r.start >= at - Duration::days(10) && r.end() <= at),
                    );
                }
            }
            let quote = quotes.get(&(at, symbol.clone())).map(replay_quote);
            match features::build_refs(
                calendar,
                &context,
                &data.news,
                symbol,
                at,
                quote.as_ref(),
                true,
            ) {
                Ok(features) => result.push(Sample {
                    features,
                    target_at: target,
                    known_at: target,
                    actual_return: future / bar.close - 1.,
                }),
                Err(_) => skipped += 1,
            }
        }
    }
    result.sort_by(|a, b| {
        a.features
            .at
            .cmp(&b.features.at)
            .then(a.features.symbol.cmp(&b.features.symbol))
    });
    Ok((result, quotes, skipped))
}
pub fn forecast_scores(rows: &[(super::models::Forecast, f64)]) -> Value {
    if rows.is_empty() {
        return json!({"n":0});
    }
    let n = rows.len() as f64;
    let mut bins = Vec::new();
    for bin in 0..10 {
        let points: Vec<_> = rows
            .iter()
            .filter(|(p, _)| ((p.probability_up * 10.).floor() as usize).min(9) == bin)
            .collect();
        if !points.is_empty() {
            bins.push(json!({"count":points.len(),"predicted":points.iter().map(|(p,_)|p.probability_up).sum::<f64>()/points.len() as f64,"observed":points.iter().filter(|(_,y)|*y>0.).count() as f64/points.len() as f64}));
        }
    }
    json!({"n":rows.len(),"mae":rows.iter().map(|(p,y)|(p.expected_return-y).abs()).sum::<f64>()/n,
        "brier":rows.iter().map(|(p,y)|(p.probability_up-if *y>0.{1.}else{0.}).powi(2)).sum::<f64>()/n,
        "directional_accuracy":rows.iter().filter(|(p,y)|(p.probability_up>=0.5)==(*y>0.)).count() as f64/n,
        "interval_coverage":rows.iter().filter(|(p,y)|*y>=p.lower&&*y<=p.upper).count() as f64/n,"calibration":bins})
}
pub fn run(calendar: &Calendar, data: &Dataset, protocol: &Protocol) -> Result<Value> {
    super::study::run(calendar, data, protocol)
}
