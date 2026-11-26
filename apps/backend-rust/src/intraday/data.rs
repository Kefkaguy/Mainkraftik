use super::features::{Candle, News};
use crate::features::Quote;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CurrentSnapshot {
    pub observed_at: DateTime<Utc>,
    pub inputs: Vec<super::features::FeatureRow>,
    pub quotes: BTreeMap<String, Quote>,
    pub failures: Vec<serde_json::Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QuoteSample {
    pub symbol: String,
    pub quote: Quote,
    pub bid_shares: f64,
    pub ask_shares: f64,
    pub historical_reconstruction: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Dataset {
    pub version: u32,
    pub collected_at: DateTime<Utc>,
    pub candle_feed: String,
    pub quote_feed: String,
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub candles: Vec<Candle>,
    pub quotes: Vec<QuoteSample>,
    pub news: Vec<News>,
    pub failures: Vec<serde_json::Value>,
    pub data_assumptions: Vec<String>,
}
