//! Strict sourced-news context/ranking contract; model prose has no order tools.
use crate::{Result, canonical, invalid};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub const PROMPT_VERSION: &str = "balanced-news-context-v4.2";
pub const PROMPT_CHAR_BUDGET: usize = 12000;
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Pick {
    pub symbol: String,
    pub source_ids: Vec<String>,
    pub stance: Stance,
    pub rationale: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Stance {
    Bullish,
    Mixed,
    Bearish,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Ranking {
    pub picks: Vec<Pick>,
}

pub struct Context {
    pub content: String,
    pub metadata: Value,
}
fn py_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}
fn compact(value: &str) -> String {
    value
        .split(py_space)
        .filter(|v| !v.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
fn clip(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.into();
    }
    if limit >= 4 {
        format!(
            "{}...",
            value
                .chars()
                .take(limit - 3)
                .collect::<String>()
                .trim_end_matches(py_space)
        )
    } else {
        value.chars().take(limit).collect()
    }
}
fn references_symbol(article: &Value, symbol: &str) -> bool {
    article["symbols"]
        .as_array()
        .is_some_and(|symbols| symbols.iter().any(|s| s.as_str() == Some(symbol)))
}
fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}
fn number(value: &Value) -> Result<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|v| v.parse().ok()))
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("news numeric value"))
}
fn rounded(value: f64, precision: usize) -> Result<f64> {
    format!("{value:.precision$}")
        .parse()
        .map_err(|_| invalid("news rounded value"))
}

pub fn research_context(
    candidates: &Value,
    articles: &Value,
    sentiment: Option<&Value>,
) -> Result<Context> {
    let candidates = candidates
        .as_object()
        .ok_or_else(|| invalid("candidate object required"))?;
    let articles = articles
        .as_object()
        .ok_or_else(|| invalid("article object required"))?;
    if !(1..=40).contains(&candidates.len()) {
        return Err(invalid("Research context requires one to forty candidates"));
    }
    let mut references = BTreeMap::<String, Vec<String>>::new();
    let mut eligible = Map::new();
    let mut texts = BTreeMap::new();
    for symbol in candidates.keys() {
        let available: Vec<_> = articles
            .iter()
            .filter(|(_, a)| {
                references_symbol(a, symbol)
                    && (!text(&a["headline"]).trim_matches(py_space).is_empty()
                        || !text(&a["summary"]).trim_matches(py_space).is_empty())
            })
            .map(|(id, _)| id.clone())
            .collect();
        let ids: Vec<_> = available.iter().take(3).cloned().collect();
        if ids.is_empty() || ids.iter().any(|id| id.chars().count() > 100) {
            return Err(invalid("Candidate lacks bounded source references"));
        }
        eligible.insert(symbol.clone(), json!(available.len()));
        for id in &ids {
            let a = &articles[id];
            let headline = if !text(&a["headline"]).is_empty() {
                text(&a["headline"])
            } else {
                text(&a["summary"])
            };
            texts.insert(
                id.clone(),
                (compact(headline), compact(text(&a["summary"]))),
            );
        }
        references.insert(symbol.clone(), ids);
    }
    let render = |per_stock: usize, headline: usize, summary: usize| -> Result<String> {
        let mut stocks = Vec::new();
        let mut included = Vec::new();
        let mut seen = BTreeSet::new();
        for (symbol, liquidity) in candidates {
            let refs: Vec<_> = references[symbol].iter().take(per_stock).cloned().collect();
            for id in &refs {
                if seen.insert(id.clone()) {
                    included.push(id.clone());
                }
            }
            let dv = rounded(number(&liquidity["dollar_volume_20"])?, 0)?;
            if dv < i64::MIN as f64 || dv > i64::MAX as f64 {
                return Err(invalid("dollar volume outside supported integer range"));
            }
            let mut row = json!({"s":symbol,"p":rounded(number(&liquidity["price"])?,2)?,"dv20":dv as i64,"refs":refs});
            if let Some(sentiment) = sentiment {
                let a = &sentiment["per_symbol"][symbol];
                let scores = &a["mean_text_sentiment"];
                row["tone"] = json!([
                    rounded(number(&scores["negative"])?, 3)?,
                    rounded(number(&scores["neutral"])?, 3)?,
                    rounded(number(&scores["positive"])?, 3)?
                ]);
                row["avoid"] = json!(a["assessment"] == "avoid");
                row["multi"] = a["multiple_symbol_articles"].clone();
            }
            stocks.push(row);
        }
        let news: Vec<_> = included
            .iter()
            .map(|id| {
                let (h, s) = &texts[id];
                let mut row = json!({"id":id,"h":clip(h,headline)});
                if summary > 0 {
                    row["summary"] = json!(clip(s, summary));
                }
                row
            })
            .collect();
        canonical::canonical(&json!({"stocks":stocks,"news":news}))
    };
    let mut per_stock = 3;
    while per_stock > 1 && render(per_stock, 8, 0)?.len() > PROMPT_CHAR_BUDGET {
        per_stock -= 1;
    }
    if render(per_stock, 8, 0)?.len() > PROMPT_CHAR_BUDGET {
        return Err(invalid(
            "Cannot represent every candidate within research context budget",
        ));
    }
    let largest = |upper: usize, head: Option<usize>| -> Result<usize> {
        let (mut low, mut high) = (0, upper);
        while low < high {
            let mid = (low + high).div_ceil(2);
            let content = if let Some(head) = head {
                render(per_stock, head, mid)?
            } else {
                render(per_stock, mid, 0)?
            };
            if content.len() <= PROMPT_CHAR_BUDGET {
                low = mid;
            } else {
                high = mid - 1;
            }
        }
        Ok(low)
    };
    let headline = largest(160, None)?;
    let summary = largest(90, Some(headline))?;
    let content = render(per_stock, headline, summary)?;
    let mut ids = Vec::new();
    let mut seen = BTreeSet::new();
    let mut presented = Map::new();
    for symbol in candidates.keys() {
        let refs: Vec<_> = references[symbol].iter().take(per_stock).cloned().collect();
        for id in &refs {
            if seen.insert(id.clone()) {
                ids.push(id.clone());
            }
        }
        presented.insert(symbol.clone(), json!(refs));
    }
    let metadata = json!({"version":PROMPT_VERSION,"characters":content.len(),"character_budget":PROMPT_CHAR_BUDGET,
        "presented_symbols":candidates.keys().collect::<Vec<_>>(),"presented_article_ids":ids,"article_ids_by_symbol":presented,"eligible_article_count_by_symbol":eligible,
        "headlines_per_stock_limit":per_stock,"headline_character_limit":headline,"summary_character_limit":summary,"text_truncation_marker":"...","candidate_count_omitted":0,
        "source_references_omitted":references.values().map(|r|r.len()-r.len().min(per_stock)).sum::<usize>(),
        "headlines_truncated":ids.iter().filter(|id|texts[*id].0.chars().count()>headline).count(),
        "summaries_truncated":ids.iter().filter(|id|texts[*id].1.chars().count()>summary).count()});
    Ok(Context { content, metadata })
}

#[derive(Debug)]
pub struct Rejected {
    pub message: String,
    pub rejections: Vec<Value>,
}
pub fn filter_output(
    output: &Value,
    candidates: &Value,
    articles: &Value,
    presented: &Value,
) -> std::result::Result<(Vec<Pick>, Vec<Value>), Rejected> {
    let ranking: Ranking = serde_json::from_value(output.clone()).map_err(|_| Rejected {
        message: "Research output failed strict schema validation".into(),
        rejections: vec![json!({"reason":"strict_schema_invalid"})],
    })?;
    if ranking.picks.len() > 20 {
        return Err(Rejected {
            message: "Research exceeded 20-stock cap".into(),
            rejections: vec![json!({"reason":"pick_cap_exceeded"})],
        });
    }
    let mut counts = BTreeMap::new();
    for pick in &ranking.picks {
        *counts.entry(pick.symbol.clone()).or_insert(0) += 1;
    }
    let mut accepted = Vec::new();
    let mut rejected = Vec::new();
    for (index, pick) in ranking.picks.into_iter().enumerate() {
        let known = candidates.get(&pick.symbol).is_some();
        let reason = if counts[&pick.symbol] > 1 {
            Some("duplicate_symbol_all_occurrences_discarded")
        } else if !known {
            Some("unknown_symbol")
        } else if pick.source_ids.is_empty()
            || pick.source_ids.len() > 3
            || pick.rationale.chars().count() > 500
        {
            Some("citation_or_rationale_bounds")
        } else if pick.source_ids.iter().any(|id| {
            articles
                .get(id)
                .is_none_or(|a| !references_symbol(a, &pick.symbol))
                || !presented[&pick.symbol]
                    .as_array()
                    .is_some_and(|ids| ids.iter().any(|v| v.as_str() == Some(id)))
        }) {
            Some("citation_not_presented_for_symbol")
        } else {
            None
        };
        if let Some(reason) = reason {
            let hash = canonical::digest(&json!(pick.symbol)).map_err(|_| Rejected {
                message: "invalid symbol identity".into(),
                rejections: vec![],
            })?;
            rejected.push(json!({"index":index,"symbol":if known{Some(pick.symbol)}else{None},"symbol_hash":&hash[..16],"reason":reason}));
        } else {
            accepted.push(pick);
        }
    }
    if !output["picks"].as_array().is_some_and(|p| p.is_empty()) && accepted.is_empty() {
        return Err(Rejected {
            message: "Research output had no valid independent picks".into(),
            rejections: rejected,
        });
    }
    Ok((accepted, rejected))
}

pub fn model_provenance(response: &Value) -> Value {
    let id = response["id"].as_str().filter(|s| {
        s.strip_prefix("chatcmpl-").is_some_and(|tail| {
            !tail.is_empty()
                && tail.len() <= 120
                && tail
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        })
    });
    let mut usage = Map::new();
    for key in [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "queue_time",
        "prompt_time",
        "completion_time",
        "total_time",
    ] {
        let value = &response["usage"][key];
        if value.is_number() && value.as_f64().is_some_and(|v| (0.0..=1e12).contains(&v)) {
            usage.insert(key.into(), value.clone());
        }
    }
    json!({"response_id":id,"usage":usage})
}
