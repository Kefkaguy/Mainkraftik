//! Native migration of Mainkraft. Incomplete services must never be deployed.
pub mod allocation;
pub mod api;
pub mod backup;
pub mod broker;
pub mod calendar;
pub mod canonical;
mod chart_font;
pub mod charts;
pub mod db;
pub mod discovery;
pub mod engine;
pub mod evidence;
pub mod features;
pub mod financials;
pub mod forecasting;
pub mod information;
pub mod ingestion;
pub mod intraday;
mod ir_cookies;
pub mod ir_html;
pub mod ir_robots;
pub mod jobs;
pub mod learning;
pub mod market;
pub mod models;
pub mod monitoring;
pub mod multistrategy;
pub mod news;
#[cfg(feature = "sentiment-inference")]
mod numpy_exp;
pub mod numpy_rng;
pub mod official_ir;
pub mod paper_api;
pub mod point_features;
pub mod prospective;
pub mod python_set;
mod python_time;
pub mod ranking;
pub mod report_api;
pub mod research_api;
pub mod research_audit;
pub mod research_dispatch;
pub mod research_evaluation;
pub mod research_evaluation_store;
pub mod research_providers;
pub mod research_training;
pub mod runtime;
pub mod sentiment;
#[cfg(feature = "sentiment-inference")]
pub mod sentiment_worker;
pub mod service;
pub mod statistics;
pub mod write_api;
mod ziggurat;

use thiserror::Error;

#[derive(Debug, Error)]
pub enum Error {
    #[error("invalid input: {0}")]
    Invalid(String),
    #[error("{kind}: {message}")]
    Reference { kind: &'static str, message: String },
    #[error("model/data JSON is invalid")]
    Json(#[from] serde_json::Error),
    #[error("I/O failed")]
    Io(#[from] std::io::Error),
    #[error("database operation failed")]
    Database(#[from] sqlx::Error),
    #[error("{0}")]
    Broker(#[from] broker::BrokerError),
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn invalid(message: impl Into<String>) -> Error {
    Error::Invalid(message.into())
}
