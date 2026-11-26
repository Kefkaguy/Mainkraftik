//! Bounded news ranking. No broker tools, redirects, retries, or paid fallback.
use crate::{Result, canonical, engine::Clock, invalid, news};
use async_trait::async_trait;
use chrono::{NaiveDate, TimeZone};
use chrono_tz::America::New_York;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::{sync::Arc, time::Duration};

pub struct Request {
    pub body: Value,
    pub hash: String,
    pub context: news::Context,
}
pub fn prepare(
    model: &str,
    candidates: &Value,
    articles: &Value,
    sentiment: Option<&Value>,
) -> Result<Request> {
    if model.is_empty() || model.len() > 200 {
        return Err(invalid("invalid research model"));
    }
    let context = news::research_context(candidates, articles, sentiment)?;
    let mut schema: Value =
        serde_json::from_str(include_str!("../assets/news-ranking-schema.json"))?;
    schema["$defs"]["Pick"]["properties"]["symbol"]["enum"] = json!(
        candidates
            .as_object()
            .ok_or_else(|| invalid("candidate object required"))?
            .keys()
            .collect::<Vec<_>>()
    );
    schema["properties"]["picks"]["maxItems"] = json!(20);
    let body = json!({"model":model,"temperature":0,"reasoning_effort":"low","max_completion_tokens":2500,
        "messages":[{"role":"system","content":include_str!("../assets/news-system-prompt.txt")},{"role":"user","content":context.content}],
        "response_format":{"type":"json_schema","json_schema":{"name":"daily_research","strict":true,"schema":schema}}});
    Ok(Request {
        hash: canonical::digest(&body)?,
        body,
        context,
    })
}

pub struct Output {
    pub picks: Vec<news::Pick>,
    pub metadata: Value,
}
pub struct Validation {
    pub kind: &'static str,
    pub payload: Value,
}
pub struct Parsed {
    pub result: std::result::Result<Output, news::Rejected>,
    pub event: Option<Validation>,
}
pub fn parse_response(
    request: &Request,
    candidates: &Value,
    articles: &Value,
    bytes: &[u8],
) -> Parsed {
    let decoded = serde_json::from_slice::<Value>(bytes);
    let response = decoded.as_ref().ok().cloned().unwrap_or(Value::Null);
    let rejected = |message: &str, reason: &str| news::Rejected {
        message: message.into(),
        rejections: vec![json!({"reason":reason})],
    };
    let result = if decoded.is_err() {
        Err(rejected(
            "Research response was malformed",
            "malformed_provider_json",
        ))
    } else {
        (|| {
            let choice = response["choices"]
                .as_array()
                .and_then(|c| c.first())
                .filter(|c| c.is_object())
                .ok_or_else(|| {
                    rejected(
                        "Research response was malformed",
                        "malformed_model_response",
                    )
                })?;
            if choice["finish_reason"] != "stop" {
                return Err(rejected(
                    "Research response incomplete",
                    "incomplete_output",
                ));
            }
            let content = choice["message"]["content"].as_str().ok_or_else(|| {
                rejected(
                    "Research response was malformed",
                    "malformed_model_response",
                )
            })?;
            let output: Value = serde_json::from_str(content).map_err(|_| {
                rejected(
                    "Research response was malformed",
                    "malformed_model_response",
                )
            })?;
            let mut shown = serde_json::Map::new();
            if let Some(ids) = request.context.metadata["presented_article_ids"].as_array() {
                for id in ids.iter().filter_map(Value::as_str) {
                    shown.insert(id.into(), articles[id].clone());
                }
            }
            news::filter_output(
                &output,
                candidates,
                &Value::Object(shown),
                &request.context.metadata["article_ids_by_symbol"],
            )
        })()
    };
    let provenance = news::model_provenance(&response);
    let (accepted_count, rejections) = match &result {
        Ok((picks, rejections)) => (picks.len(), rejections.clone()),
        Err(e) => (0, e.rejections.clone()),
    };
    let event = if result.is_err() || !rejections.is_empty() {
        Some(Validation {
            kind: if accepted_count == 0 {
                "research_model_validation_rejected"
            } else {
                "research_model_output_filtered"
            },
            payload: json!({"prompt_version":news::PROMPT_VERSION,"request_hash":request.hash,"response_id":provenance["response_id"],"usage":provenance["usage"],"accepted_count":accepted_count,"rejections":rejections.iter().take(20).collect::<Vec<_>>()}),
        })
    } else {
        None
    };
    Parsed { result:result.map(|(picks,rejections)| {
        let metadata = json!({"model":request.body["model"],"request_hash":request.hash,"response_id":provenance["response_id"],"usage":provenance["usage"],"context":request.context.metadata,"accepted_symbols":picks.iter().map(|p|p.symbol.as_str()).collect::<Vec<_>>(),"output_rejections":rejections});
        Output { picks, metadata }
    }), event }
}

#[async_trait]
pub trait Transport: Send + Sync {
    async fn complete(&self, body: &Value) -> Result<(u16, Vec<u8>)>;
}
pub struct HttpTransport {
    client: reqwest::Client,
}
impl HttpTransport {
    pub fn new(key: &str) -> Result<Self> {
        if key.is_empty() {
            return Err(invalid("Groq credentials unavailable"));
        }
        let mut auth = reqwest::header::HeaderValue::from_str(&format!("Bearer {key}"))
            .map_err(|_| invalid("invalid Groq credential"))?;
        auth.set_sensitive(true);
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(reqwest::header::AUTHORIZATION, auth);
        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(75))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| invalid("research client unavailable"))?;
        Ok(Self { client })
    }
}
#[async_trait]
impl Transport for HttpTransport {
    async fn complete(&self, body: &Value) -> Result<(u16, Vec<u8>)> {
        let mut response = self
            .client
            .post("https://api.groq.com/openai/v1/chat/completions")
            .json(body)
            .send()
            .await
            .map_err(|_| invalid("Groq transport failed; no automatic retry"))?;
        let status = response.status().as_u16();
        if status != 200 {
            return Ok((status, Vec::new()));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| invalid("Groq response interrupted; no automatic retry"))?
        {
            if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                return Err(invalid("Groq response exceeds bounded limit"));
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok((status, bytes))
    }
}

/// Reservation commits before sending. Failed/unknown responses still consume budget.
pub async fn reserve(
    pool: &PgPool,
    session: NaiveDate,
    instant: chrono::DateTime<chrono::Utc>,
    model: &str,
    hash: &str,
) -> Result<()> {
    let day = instant.with_timezone(&New_York).date_naive();
    let midnight = New_York
        .from_local_datetime(
            &day.and_hms_opt(0, 0, 0)
                .ok_or_else(|| invalid("invalid research date"))?,
        )
        .single()
        .ok_or_else(|| invalid("ambiguous research midnight"))?;
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(753194120)")
        .execute(&mut *tx)
        .await?;
    let (daily, monthly): (i64,i64) = sqlx::query_as("SELECT count(*) FILTER(WHERE started_at >= $1),count(*) FROM pilot_groq_calls WHERE started_at>=date_trunc('month',now())")
        .bind(midnight).fetch_one(&mut *tx).await?;
    if daily >= 3 || monthly >= 60 {
        return Err(invalid(
            "Groq request budget reached; no automatic paid fallback",
        ));
    }
    sqlx::query("INSERT INTO pilot_groq_calls(session_date,payload) VALUES($1,$2)")
        .bind(session)
        .bind(json!({"model":model,"request_hash":hash,"max_completion_tokens":2500}))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}

pub struct Ranker {
    pub pool: PgPool,
    pub model: String,
    pub transport: Arc<dyn Transport>,
    pub clock: Arc<dyn Clock>,
}
impl Ranker {
    pub async fn rank(
        &self,
        session: NaiveDate,
        candidates: &Value,
        articles: &Value,
        sentiment: Option<&Value>,
    ) -> Result<Output> {
        let request = prepare(&self.model, candidates, articles, sentiment)?;
        reserve(
            &self.pool,
            session,
            self.clock.now(),
            &self.model,
            &request.hash,
        )
        .await?;
        let (status, bytes) = self.transport.complete(&request.body).await?;
        if status != 200 {
            return Err(invalid(format!("Groq HTTP {status}; no automatic retry")));
        }
        let parsed = parse_response(&request, candidates, articles, &bytes);
        if let Some(event) = parsed.event {
            sqlx::query("INSERT INTO pilot_events(kind,payload) VALUES($1,$2)")
                .bind(event.kind)
                .bind(event.payload)
                .execute(&self.pool)
                .await?;
        }
        parsed.result.map_err(|error| invalid(error.message))
    }
}
