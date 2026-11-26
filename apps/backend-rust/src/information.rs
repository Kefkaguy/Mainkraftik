//! Private, immutable research news and filing metadata; no article body retention.
use crate::{
    Error, Result, canonical,
    engine::{Clock, stamp},
    financials,
    ingestion::{ResourceCheck, store_document},
    invalid,
    research_providers::{self, Providers},
};
use chrono::{DateTime, Duration, Utc};
use regex::Regex;
use serde_json::{Value, json};
use sqlx::{PgConnection, PgPool};
use std::{
    collections::BTreeSet,
    sync::{Arc, LazyLock},
};
use uuid::Uuid;
pub const NEWS_URL: &str = "https://data.alpaca.markets/v1beta1/news";
fn py_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}
static STEMS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"not|no|unchanged from prior",
        r"raises?|raised|increases?|increased",
        r"lowers?|lowered|reduces?|reduced",
        r"reaffirms?|reaffirmed|maintains?",
        r"guidance|outlook",
        r"earnings|financial results|reports?.{0,35}revenue",
        r"contract|selected|selects|award|awarded",
        r"acqui(?:re|res|red|sition)|merger",
    ]
    .iter()
    .map(|s| {
        Regex::new(&format!(
            r"(?:^|[^\p{{L}}\p{{N}}_])({s})(?:$|[^\p{{L}}\p{{N}}_])"
        ))
        .expect("constant regex")
    })
    .collect()
});
fn contains(text: &str, index: usize) -> bool {
    STEMS[index].is_match(text)
}
pub fn tags(text: &str) -> Value {
    let text = text
        .split(py_space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let mut guidance = None;
    if !contains(&text, 0) {
        for (index, direction) in [(1, 1), (2, -1), (3, 0)] {
            let mut offset = 0;
            let mut found = false;
            while let Some(capture) = STEMS[index].captures_at(&text, offset) {
                let first = capture.get(1).expect("stem");
                // Only a 55-character gap plus the target and its boundary can
                // match. Bound each scan rather than repeatedly scanning a page.
                let tail = &text[first.end()..];
                let end = tail.char_indices().nth(64).map_or(tail.len(), |(i, _)| i);
                if let Some(target) = STEMS[4].captures(&tail[..end]) {
                    let last = target.get(1).expect("target");
                    if tail[..last.start()].chars().count() <= 55 {
                        found = true;
                        break;
                    }
                }
                offset = first.end();
            }
            if found {
                guidance = Some(direction);
                break;
            }
        }
    }
    json!({"earnings":contains(&text,5),"contract":contains(&text,6),"acquisition":contains(&text,7),"guidance_direction":guidance})
}
#[derive(Debug)]
pub struct MetadataError {
    pub kind: &'static str,
    pub message: String,
}
impl From<MetadataError> for Error {
    fn from(error: MetadataError) -> Self {
        match error.kind {
            "ValueError" | "ProviderError" | "KeyError" => invalid(error.message),
            kind => Error::Reference {
                kind,
                message: error.message,
            },
        }
    }
}
fn fail(kind: &'static str, message: impl Into<String>) -> MetadataError {
    MetadataError {
        kind,
        message: message.into(),
    }
}
pub fn information_stamp(text: &str) -> std::result::Result<DateTime<Utc>, MetadataError> {
    crate::python_time::information_stamp(text)
}
fn field<'a>(row: &'a Value, key: &str) -> std::result::Result<&'a Value, MetadataError> {
    row.get(key)
        .ok_or_else(|| fail("KeyError", format!("'{key}'")))
}
fn text(value: &Value) -> std::result::Result<String, MetadataError> {
    Ok(match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(_) => canonical::canonical(value)
            .map_err(|_| fail("ValueError", "Invalid metadata number"))?,
        _ => return Err(fail("TypeError", "Unsupported metadata value")),
    })
}
pub fn news_metadata(row: &Value) -> std::result::Result<Value, MetadataError> {
    let created = information_stamp(
        field(row, "created_at")?
            .as_str()
            .ok_or_else(|| fail("TypeError", "Invalid creation timestamp"))?,
    )?;
    let updated = information_stamp(
        field(row, "updated_at")?
            .as_str()
            .ok_or_else(|| fail("TypeError", "Invalid update timestamp"))?,
    )?;
    if updated < created {
        return Err(fail("ProviderError", "Article update precedes creation"));
    }
    let title = text(row.get("headline").unwrap_or(&json!("")))?
        .chars()
        .take(1000)
        .collect::<String>();
    let mut symbols = BTreeSet::new();
    if let Some(rows) = row.get("symbols") {
        for symbol in rows
            .as_array()
            .ok_or_else(|| fail("TypeError", "Invalid article symbols"))?
        {
            symbols.insert(
                symbol
                    .as_str()
                    .ok_or_else(|| fail("TypeError", "Invalid article symbol"))?
                    .to_owned(),
            );
        }
    }
    Ok(
        json!({"external_id":text(field(row,"id")?)?,"created_at":stamp(created),"updated_at":stamp(updated),"available_at":stamp(updated),"symbols":symbols,"source":row["source"],"author":row["author"],"url":row["url"],"headline":title,"flags":tags(&title),"original_payload_hash":canonical::digest(row).map_err(|_|fail("ValueError","Invalid metadata identity"))?,"availability_basis":"Provider latest-version update timestamp; historical archive is reconstructed"}),
    )
}
pub async fn persist_information(
    connection: &mut PgConnection,
    provider: &str,
    metadata: &Value,
    source: Uuid,
    acquired: DateTime<Utc>,
) -> Result<String> {
    let identity = if provider == "official_ir" {
        canonical::digest(&json!([
            provider,
            metadata["external_id"],
            metadata["headline"],
            metadata["flags"],
            metadata["publication_original"]
        ]))?
    } else {
        let mut payload = metadata
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("Invalid information metadata"))?;
        payload
            .entry("provider".to_owned())
            .or_insert_with(|| json!(provider));
        canonical::digest(&json!(payload))?
    };
    sqlx::query("INSERT INTO information_versions VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT DO NOTHING").bind(&identity).bind(provider).bind(metadata["external_id"].as_str()).bind(&metadata["symbols"]).bind(financials::timestamp(&metadata["created_at"])?).bind(financials::timestamp(&metadata["updated_at"])?).bind(financials::timestamp(&metadata["available_at"])?).bind(acquired).bind(source).bind(metadata).execute(connection).await?;
    Ok(identity)
}
pub struct Information {
    pool: PgPool,
    providers: Arc<Providers>,
    clock: Arc<dyn Clock>,
    resources: Arc<dyn ResourceCheck>,
    delay: Arc<dyn research_providers::Delay>,
}
impl Information {
    pub async fn refresh(
        &self,
        ir: &crate::official_ir::OfficialIr,
        markets: &crate::ingestion::Ingester,
        backfill: bool,
    ) -> Result<Value> {
        let start = if backfill {
            None
        } else {
            Some(self.clock.now() - Duration::days(45))
        };
        let news = self.news(start, None).await?;
        let filings = self.filings().await?;
        let official = ir.ingest().await?;
        let mut results = Vec::new();
        for symbol in ["XLK", "XLI"] {
            results.push(match markets.prices(symbol).await {
                Ok(value) => value,
                Err(Error::Invalid(reason)) => {
                    json!({"symbol":symbol,"status":"unavailable","reason":reason})
                }
                Err(e) => return Err(e),
            });
        }
        Ok(json!({"news":news,"filings":filings,"official_ir":official,"markets":results}))
    }
    pub fn new(
        pool: PgPool,
        providers: Arc<Providers>,
        clock: Arc<dyn Clock>,
        resources: Arc<dyn ResourceCheck>,
        delay: Arc<dyn research_providers::Delay>,
    ) -> Self {
        Self {
            pool,
            providers,
            clock,
            resources,
            delay,
        }
    }
    pub async fn news(
        &self,
        start: Option<DateTime<Utc>>,
        end: Option<DateTime<Utc>>,
    ) -> Result<Value> {
        let start = start.unwrap_or_else(|| {
            DateTime::parse_from_rfc3339("2021-07-01T00:00:00Z")
                .expect("constant date")
                .with_timezone(&Utc)
        });
        let end = end.unwrap_or_else(|| self.clock.now() - Duration::minutes(20));
        if start >= end {
            return Err(invalid("An ordered aware news interval is required"));
        }
        self.resources.check(64 * 1024 * 1024).await?;
        let mut seen = BTreeSet::new();
        let mut count = 0;
        let mut pages = 0;
        let mut error = None;
        let mut token = None;
        for index in 0..600 {
            let payload = match self
                .providers
                .research_news_page(start, end, token.as_deref())
                .await
            {
                Ok(value) => value,
                Err(Error::Invalid(_)) => {
                    error = Some("ProviderError");
                    break;
                }
                Err(Error::Json(_)) => {
                    error = Some("JSONDecodeError");
                    break;
                }
                Err(e) => return Err(e),
            };
            let acquired = self.clock.now();
            let rows = match payload.get("news") {
                None => &[][..],
                Some(Value::Array(rows)) => rows.as_slice(),
                _ => return Err(invalid("Unsupported news payload type")),
            };
            let metadata = rows
                .iter()
                .map(news_metadata)
                .collect::<std::result::Result<Vec<_>, _>>();
            let metadata = match metadata {
                Ok(rows) => rows,
                Err(e) if matches!(e.kind, "ValueError" | "ProviderError" | "KeyError") => {
                    error = Some(e.kind);
                    break;
                }
                Err(e) => return Err(e.into()),
            };
            let mut tx = self.pool.begin().await?;
            let source=store_document(&mut tx,"alpaca_news_metadata",NEWS_URL,&json!({"request":research_providers::research_news_query(start,end),"articles":metadata,"page_hash":canonical::digest(&payload)?}),acquired).await?;
            for row in &metadata {
                persist_information(&mut tx, "alpaca_news", row, source, acquired).await?;
            }
            tx.commit().await?;
            count += metadata.len();
            pages += 1;
            token = payload["next_page_token"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_owned);
            let Some(next) = &token else {
                break;
            };
            if !seen.insert(next.clone()) {
                error = Some("ProviderError");
                break;
            }
            self.delay.wait(120).await;
            if index == 599 {
                error = Some("ProviderError");
            }
        }
        let status = if error.is_none() {
            "complete"
        } else if pages > 0 {
            "partial"
        } else {
            "unavailable"
        };
        let error = error.map(|kind| format!("{kind}: news ingestion incomplete"));
        sqlx::query("INSERT INTO information_fetches VALUES($1,'alpaca_news',$2,$3,$4,$5,$6,$7)")
            .bind(Uuid::new_v4())
            .bind(json!(["MSI", "AXON"]))
            .bind(start)
            .bind(end)
            .bind(self.clock.now())
            .bind(status)
            .bind(json!({"rows":count,"pages":pages,"error":error}))
            .execute(&self.pool)
            .await?;
        Ok(
            json!({"provider":"alpaca_news","status":status,"articles_returned":count,"pages":pages,"error":error}),
        )
    }
    pub async fn filings(&self) -> Result<Value> {
        let mut tx = self.pool.begin().await?;
        let sources:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(s) FROM source_documents s WHERE provider='sec_submissions' ORDER BY acquired_at").fetch_all(&mut *tx).await?;
        let mut count = 0;
        for source in sources {
            let payload = &source["payload"];
            let symbol = payload["tickers"].as_array().and_then(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .find(|s| matches!(*s, "MSI" | "AXON"))
            });
            let Some(symbol) = symbol else {
                continue;
            };
            let recent = &payload["filings"]["recent"];
            if let Some(accessions) = recent["accessionNumber"].as_array() {
                for (i, accession) in accessions.iter().enumerate() {
                    let form = recent["form"][i]
                        .as_str()
                        .ok_or_else(|| invalid("Invalid filing form array"))?;
                    if !matches!(
                        form,
                        "8-K" | "8-K/A" | "10-K" | "10-K/A" | "10-Q" | "10-Q/A"
                    ) {
                        continue;
                    }
                    let accepted = recent["acceptanceDateTime"]
                        .as_array()
                        .filter(|a| !a.is_empty())
                        .map(|a| {
                            a.get(i)
                                .and_then(Value::as_str)
                                .ok_or_else(|| invalid("Invalid filing acceptance array"))
                        })
                        .transpose()?
                        .unwrap_or("");
                    let filed = recent["filingDate"][i]
                        .as_str()
                        .ok_or_else(|| invalid("Missing filing date"))?;
                    let published = if !accepted.is_empty() {
                        information_stamp(accepted).map_err(Error::from)?
                    } else {
                        let text = research_providers::filing_availability(filed, "", &json!({}))?;
                        DateTime::parse_from_rfc3339(&text)
                            .map_err(|_| invalid("Invalid filing timestamp"))?
                            .with_timezone(&Utc)
                    };
                    let items = recent["items"]
                        .as_array()
                        .filter(|a| !a.is_empty())
                        .map(|a| {
                            a.get(i)
                                .and_then(Value::as_str)
                                .ok_or_else(|| invalid("Invalid filing items array"))
                        })
                        .transpose()?
                        .unwrap_or("");
                    let data = json!({"accession":accession,"form":form,"items":items,"published_at":stamp(published),"filed_date":filed,"acceptance_original":accepted,"earnings_filing":form.starts_with("8-K") && items.contains("2.02"),"availability_basis":if accepted.is_empty(){"Conservative next midnight after filing date"}else{"SEC acceptance"}});
                    let identity = canonical::digest(&json!([symbol, data]))?;
                    sqlx::query("INSERT INTO filing_events VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING").bind(identity).bind(symbol).bind(accession.as_str()).bind(form).bind(items).bind(published).bind(financials::timestamp(&source["acquired_at"])?).bind(source["id"].as_str().ok_or_else(||invalid("Missing submission source"))?.parse::<Uuid>().map_err(|_|invalid("Invalid submission source"))?).bind(data).execute(&mut *tx).await?;
                    count += 1;
                }
            }
        }
        tx.commit().await?;
        Ok(
            json!({"status":"complete","filing_versions_seen":count,"coverage":"Archived recent-submission arrays; older archive files not assumed complete"}),
        )
    }
}
