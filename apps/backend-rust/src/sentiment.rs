//! Sourced text-sentiment validation and aggregation, never return probabilities.
use crate::{
    Result, canonical,
    engine::{Clock, stamp},
    invalid,
};
use async_trait::async_trait;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::Duration,
};

pub const REVISION: &str = "e6953e30167c7fc3d1ad62f3df31617dd712fbc1";
pub const WEIGHT_HASH: &str = "8fcf4873bf5e1679c77205ce621acecdd2cd8dd5baf18e930f10428008ed48ec";
#[async_trait]
pub trait Transport: Send + Sync {
    async fn score(&self, items: &[Value]) -> Result<Value>;
}
pub struct HttpTransport {
    url: String,
    client: reqwest::Client,
}
impl HttpTransport {
    pub fn new(url: String) -> Result<Self> {
        if !matches!(
            url.as_str(),
            "http://sentiment:8300" | "http://127.0.0.1:8300"
        ) {
            return Err(invalid("invalid private sentiment service"));
        }
        Ok(Self {
            url,
            client: reqwest::Client::builder()
                .timeout(Duration::from_secs(90))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|_| invalid("sentiment client unavailable"))?,
        })
    }
}
#[async_trait]
impl Transport for HttpTransport {
    async fn score(&self, items: &[Value]) -> Result<Value> {
        let response = self
            .client
            .post(format!("{}/score", self.url))
            .json(&json!({"items":items}))
            .send()
            .await
            .map_err(|_| {
                invalid("Local financial sentiment unavailable; daily selection skipped")
            })?;
        if response.status().as_u16() != 200 {
            return Err(invalid(
                "Local financial sentiment unavailable; daily selection skipped",
            ));
        }
        response
            .json()
            .await
            .map_err(|_| invalid("invalid sentiment response JSON"))
    }
}

pub struct Input {
    pub references: BTreeMap<String, Vec<String>>,
    pub items: Vec<Value>,
}
pub fn prepare(candidates: &Value, articles: &Value) -> Result<Input> {
    let candidates = candidates
        .as_object()
        .ok_or_else(|| invalid("sentiment candidates object required"))?;
    let articles = articles
        .as_object()
        .ok_or_else(|| invalid("sentiment articles object required"))?;
    let mut references = BTreeMap::new();
    let mut ids = Vec::new();
    let mut seen = BTreeSet::new();
    for symbol in candidates.keys() {
        let refs: Vec<_> = articles
            .iter()
            .filter(|(_, a)| {
                a["symbols"]
                    .as_array()
                    .is_some_and(|ss| ss.iter().any(|s| s.as_str() == Some(symbol)))
            })
            .map(|(id, _)| id.clone())
            .take(3)
            .collect();
        for id in &refs {
            if seen.insert(id.clone()) {
                ids.push(id.clone());
            }
        }
        references.insert(symbol.clone(), refs);
    }
    let items = ids
        .into_iter()
        .map(|id| {
            let article = &articles[&id];
            let text = format!(
                "{}. {}",
                article["headline"].as_str().unwrap_or(""),
                article["summary"].as_str().unwrap_or("")
            );
            json!({"id":id,"text":text.chars().take(1500).collect::<String>()})
        })
        .collect::<Vec<_>>();
    if items.is_empty() {
        return Err(invalid("No source text for local financial sentiment"));
    }
    Ok(Input { references, items })
}

pub fn validate_and_aggregate(
    candidates: &Value,
    articles: &Value,
    source_id: &str,
    input: &Input,
    data: &Value,
    at: &str,
) -> Result<Value> {
    let model = &data["model"];
    if model["revision"] != REVISION || model["sha256"]["onnx/model_quantized.onnx"] != WEIGHT_HASH
    {
        return Err(invalid("Unapproved local model revision/hash"));
    }
    let scores = data["scores"]
        .as_array()
        .ok_or_else(|| invalid("sentiment scores missing"))?;
    let ids: BTreeSet<_> = input
        .items
        .iter()
        .filter_map(|i| i["id"].as_str())
        .collect();
    let returned: BTreeSet<_> = scores.iter().filter_map(|i| i["id"].as_str()).collect();
    if scores.len() != input.items.len() || ids != returned {
        return Err(invalid("Incomplete or duplicated sentiment output"));
    }
    let mut by_id = BTreeMap::new();
    for score in scores {
        let id = score["id"]
            .as_str()
            .ok_or_else(|| invalid("sentiment ID"))?;
        by_id.insert(id, score);
    }
    for item in &input.items {
        let id = item["id"].as_str().ok_or_else(|| invalid("input ID"))?;
        let row = by_id[id];
        let probabilities = row["scores"]
            .as_object()
            .ok_or_else(|| invalid("sentiment probabilities missing"))?;
        let expected = hex::encode(Sha256::digest(
            item["text"]
                .as_str()
                .ok_or_else(|| invalid("input text"))?
                .as_bytes(),
        ));
        if row["text_hash"] != expected
            || probabilities.len() != 3
            || ["negative", "neutral", "positive"].iter().any(|k| {
                !probabilities
                    .get(*k)
                    .and_then(Value::as_f64)
                    .is_some_and(|v| v.is_finite() && (0.0..=1.0).contains(&v))
            })
            || (probabilities
                .values()
                .map(|v| v.as_f64().unwrap_or(f64::NAN))
                .sum::<f64>()
                - 1.)
                .abs()
                > 1e-4
        {
            return Err(invalid("Invalid sentiment scores or input hash"));
        }
    }
    let mut per_symbol = Map::new();
    for symbol in candidates
        .as_object()
        .ok_or_else(|| invalid("candidates"))?
        .keys()
    {
        let refs = &input.references[symbol];
        if refs.is_empty() {
            return Err(invalid("Candidate lacks cited news"));
        }
        let mut mean = Map::new();
        for key in ["positive", "negative", "neutral"] {
            let value = refs
                .iter()
                .map(|id| {
                    by_id[id.as_str()]["scores"][key]
                        .as_f64()
                        .unwrap_or(f64::NAN)
                })
                .sum::<f64>()
                / refs.len() as f64;
            mean.insert(key.into(), json!(value));
        }
        let avoid = mean["negative"].as_f64().unwrap_or(0.) >= 0.60
            && mean["negative"].as_f64().unwrap_or(0.) - mean["positive"].as_f64().unwrap_or(0.)
                >= 0.20;
        let multiple = refs
            .iter()
            .filter(|id| {
                articles[*id]["symbols"]
                    .as_array()
                    .is_some_and(|ss| ss.len() > 1)
            })
            .count();
        per_symbol.insert(symbol.clone(),json!({"source_ids":refs,"mean_text_sentiment":mean,"assessment":if avoid{"avoid"}else{"review"},"multiple_symbol_articles":multiple,"not_return_probability":true}));
    }
    Ok(
        json!({"version":"hf-financial-sentiment-q8-v1","model":model,"news_source_id":source_id,"acquired_at":at,"articles":scores,"per_symbol":per_symbol,
        "policy":"Research context and negative-news veto only; no probability uplift"}),
    )
}

pub async fn assess(
    pool: &PgPool,
    transport: &dyn Transport,
    clock: &Arc<dyn Clock>,
    candidates: &Value,
    articles: &Value,
    source_id: &str,
) -> Result<Value> {
    let input = prepare(candidates, articles)?;
    let data = transport.score(&input.items).await?;
    let mut evidence = validate_and_aggregate(
        candidates,
        articles,
        source_id,
        &input,
        &data,
        &stamp(clock.now()),
    )?;
    let id = canonical::digest(&json!(["*", "hf_sentiment", evidence]))?;
    sqlx::query("INSERT INTO pilot_sources(id,symbol,provider,acquired_at,payload) VALUES($1,'*','hf_sentiment',$2,$3) ON CONFLICT DO NOTHING")
        .bind(&id).bind(clock.now()).bind(&evidence).execute(pool).await?;
    evidence["source_id"] = json!(id);
    Ok(evidence)
}
