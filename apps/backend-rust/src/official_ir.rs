//! Allowlisted official IR acquisition and immutable publication enrichment.
use crate::{
    Error, Result, canonical,
    engine::{Clock, stamp},
    information::{information_stamp, persist_information, tags},
    ingestion::store_document,
    invalid,
    ir_html::Document,
    ir_robots::Robots,
    research_providers::Delay,
};
use async_trait::async_trait;
use chrono::{DateTime, NaiveDate, Utc};
use futures::StreamExt;
use regex::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::{
    collections::BTreeMap,
    sync::{Arc, LazyLock},
    time::Duration,
};
use tokio::sync::Semaphore;
pub const PAGE_BYTES: usize = 3 * 1024 * 1024;
pub struct Page {
    pub url: String,
    pub status: u16,
    pub location: Option<String>,
    pub content: Vec<u8>,
    pub charset: Option<String>,
}

impl Page {
    pub fn text(&self) -> String {
        let label = self
            .charset
            .as_deref()
            .unwrap_or("utf-8")
            .to_ascii_lowercase();
        if matches!(
            label.as_str(),
            "iso-8859-1" | "latin1" | "latin-1" | "latin_1"
        ) {
            return self.content.iter().map(|b| char::from(*b)).collect();
        }
        let encoding =
            encoding_rs::Encoding::for_label(label.as_bytes()).unwrap_or(encoding_rs::UTF_8);
        let decoded = encoding
            .decode_without_bom_handling(&self.content)
            .0
            .into_owned();
        if encoding == encoding_rs::WINDOWS_1252 {
            decoded
                .chars()
                .map(|c| {
                    if matches!(c, '\u{81}' | '\u{8d}' | '\u{8f}' | '\u{90}' | '\u{9d}') {
                        '\u{fffd}'
                    } else {
                        c
                    }
                })
                .collect()
        } else {
            decoded
        }
    }
}
#[async_trait]
pub trait Transport: Send + Sync {
    async fn get(&self, url: &str, user_agent: &str) -> Result<Page>;
    fn session(&self) -> Result<Option<Arc<dyn Transport>>> {
        Ok(None)
    }
}
pub fn permitted(url: &str) -> bool {
    url::Url::parse(url).is_ok_and(|u| {
        u.scheme() == "https"
            && matches!(
                u.host_str(),
                Some(
                    "investor.axon.com"
                        | "www.motorolasolutions.com"
                        | "newsroom.motorolasolutions.com"
                )
            )
            && u.username().is_empty()
            && u.password().is_none()
            && u.port().is_none_or(|p| p == 443)
    })
}
type ClientFactory = Arc<dyn Fn() -> Result<reqwest::Client> + Send + Sync>;
pub struct NativeTransport {
    client: reqwest::Client,
    factory: ClientFactory,
}
impl NativeTransport {
    pub fn new() -> Result<Self> {
        let factory: ClientFactory = Arc::new(|| {
            reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(30))
                .cookie_provider(Arc::new(crate::ir_cookies::Jar::default()))
                .build()
                .map_err(|_| invalid("Official IR client unavailable"))
        });
        Ok(Self {
            client: factory()?,
            factory,
        })
    }
}
#[async_trait]
impl Transport for NativeTransport {
    fn session(&self) -> Result<Option<Arc<dyn Transport>>> {
        Ok(Some(Arc::new(Self {
            client: (self.factory)()?,
            factory: self.factory.clone(),
        })))
    }
    async fn get(&self, url: &str, user_agent: &str) -> Result<Page> {
        if !permitted(url) {
            return Err(invalid("Official IR host is not permitted"));
        }
        let response = self
            .client
            .get(url)
            .header("user-agent", user_agent)
            .send()
            .await
            .map_err(|_| invalid("Official IR connection unavailable"))?;
        let status = response.status().as_u16();
        let final_url = response.url().to_string();
        let location = response
            .headers()
            .get("location")
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        let charset = response
            .headers()
            .get("content-type")
            .and_then(|h| h.to_str().ok())
            .and_then(|s| {
                s.split(';').skip(1).find_map(|s| {
                    s.trim()
                        .split_once('=')
                        .filter(|(k, _)| k.eq_ignore_ascii_case("charset"))
                        .map(|(_, v)| v.trim_matches(['\'', '"']).to_owned())
                })
            });
        if matches!(status, 301 | 302 | 307 | 308) {
            return Ok(Page {
                url: final_url,
                status,
                location,
                content: Vec::new(),
                charset,
            });
        }
        if !matches!(status, 200 | 404)
            || response
                .content_length()
                .is_some_and(|n| n > PAGE_BYTES as u64)
        {
            return Err(invalid(
                "Official IR page unavailable or exceeds size budget",
            ));
        }
        let mut content = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(part) = stream.next().await {
            let part = part.map_err(|_| invalid("Official IR connection unavailable"))?;
            if content.len().saturating_add(part.len()) > PAGE_BYTES {
                return Err(invalid(
                    "Official IR page unavailable or exceeds size budget",
                ));
            }
            content.extend_from_slice(&part);
        }
        Ok(Page {
            url: final_url,
            status,
            location,
            content,
            charset,
        })
    }
}
#[derive(Default)]
struct Parts {
    scheme: Option<String>,
    host: Option<String>,
    path: String,
    query: Option<String>,
    fragment: Option<String>,
}
impl Parts {
    fn parse(input: &str) -> Self {
        let input = input
            .trim_start_matches(|c| c <= '\u{20}')
            .replace(['\t', '\r', '\n'], "");
        let mut rest = input.as_str();
        let mut p = Self::default();
        if let Some(i) = rest.find(':') {
            let scheme = &rest[..i];
            if scheme
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphabetic)
                && scheme
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b))
            {
                p.scheme = Some(scheme.to_ascii_lowercase());
                rest = &rest[i + 1..];
            }
        }
        if let Some(tail) = rest.strip_prefix("//") {
            let end = tail.find(['/', '?', '#']).unwrap_or(tail.len());
            p.host = Some(tail[..end].into());
            rest = &tail[end..];
        }
        if let Some((head, fragment)) = rest.split_once('#') {
            p.fragment = Some(fragment.into());
            rest = head;
        }
        if let Some((head, query)) = rest.split_once('?') {
            p.query = Some(query.into());
            rest = head;
        }
        p.path = rest.into();
        p
    }
    fn render(&self) -> String {
        let mut s = String::new();
        if let Some(scheme) = &self.scheme {
            s.push_str(scheme);
            s.push(':');
        }
        if let Some(host) = &self.host {
            s.push_str("//");
            s.push_str(host);
            if !self.path.is_empty() && !self.path.starts_with('/') {
                s.push('/');
            }
        }
        s.push_str(&self.path);
        if let Some(query) = &self.query {
            s.push('?');
            s.push_str(query);
        }
        if let Some(fragment) = &self.fragment {
            s.push('#');
            s.push_str(fragment);
        }
        s
    }
}
/// Python 3.14 urljoin representation: retain raw characters, explicit default
/// ports and empty query/fragment markers in immutable publication identities.
pub fn join(base: &str, target: &str) -> Result<String> {
    if base.is_empty() {
        return Ok(target.into());
    }
    if target.is_empty() {
        return Ok(base.into());
    }
    let b = Parts::parse(base);
    let mut t = Parts::parse(target);
    if t.scheme.is_none() {
        t.scheme = b.scheme.clone();
    }
    if t.scheme != b.scheme
        || t.scheme
            .as_deref()
            .is_some_and(|s| !matches!(s, "http" | "https" | "ftp" | "file" | "ws" | "wss"))
    {
        return Ok(target.into());
    }
    if t.host.as_deref().is_some_and(|s| !s.is_empty()) {
        return Ok(t.render());
    }
    t.host = b.host;
    if t.path.is_empty() {
        t.path = b.path;
        if t.query.is_none() {
            t.query = b.query;
            if t.fragment.is_none() {
                t.fragment = b.fragment;
            }
        }
        return Ok(t.render());
    }
    let mut base_parts: Vec<_> = b.path.split('/').collect();
    if base_parts.last() != Some(&"") {
        base_parts.pop();
    }
    let segments: Vec<_> = if t.path.starts_with('/') {
        t.path.split('/').collect()
    } else {
        base_parts.extend(t.path.split('/'));
        let n = base_parts.len();
        base_parts
            .into_iter()
            .enumerate()
            .filter_map(|(i, s)| (i == 0 || i == n - 1 || !s.is_empty()).then_some(s))
            .collect()
    };
    let mut resolved = Vec::new();
    for s in &segments {
        match *s {
            ".." => {
                resolved.pop();
            }
            "." => {}
            other => resolved.push(other),
        }
    }
    if matches!(segments.last(), Some(&"." | &"..")) {
        resolved.push("");
    }
    let path = resolved.join("/");
    t.path = if path.is_empty() { "/".into() } else { path };
    Ok(t.render())
}
static HINT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"/(20\d\d-\d\d-\d\d)-").expect("constant regex"));
pub fn listing_links(symbol: &str, listing: &str, html: &str) -> Result<Vec<(String, String)>> {
    let document = Document::parse(html)?;
    let mut links: Vec<(String, String)> = Vec::new();
    for node in document.elements("a") {
        let Some(href) = document.attr(node, "href") else {
            continue;
        };
        let url = join(listing, href)?;
        let relevant = if symbol == "AXON" {
            HINT.is_match(&url)
        } else {
            url.contains("/newsroom/press-releases/")
        };
        let host = url::Url::parse(&url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned));
        if relevant
            && matches!(
                host.as_deref(),
                Some(
                    "investor.axon.com"
                        | "www.motorolasolutions.com"
                        | "newsroom.motorolasolutions.com"
                )
            )
            && !url.ends_with("/de.html")
        {
            let title = document.text(node, true);
            if let Some((_, old)) = links.iter_mut().find(|(key, _)| *key == url) {
                *old = title;
            } else {
                links.push((url, title));
            }
        }
    }
    links.truncate(12);
    Ok(links)
}
pub fn article_metadata(
    symbol: &str,
    url: &str,
    listing_title: &str,
    html: &str,
    acquired: DateTime<Utc>,
) -> Result<Value> {
    let document = Document::parse(html)?;
    let heading = document
        .elements("meta")
        .find(|i| document.attr(*i, "property") == Some("og:title"))
        .or_else(|| document.elements("h1").next());
    let title = heading
        .map(|i| {
            document
                .attr(i, "content")
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .unwrap_or_else(|| document.text(i, true))
        })
        .unwrap_or_else(|| listing_title.to_owned());
    let original = document
        .elements("meta")
        .find(|i| document.attr(*i, "name") == Some("press_release_publish_date"))
        .and_then(|i| document.attr(i, "content"))
        .filter(|s| !s.is_empty())
        .map(str::to_owned);
    let hint = HINT
        .captures(url)
        .and_then(|c| c.get(1).map(|m| m.as_str().to_owned()));
    let created = if let Some(original) = &original {
        information_stamp(original).map_err(Error::from)?
    } else if let Some(day) = &hint {
        crate::calendar::local_time(
            day.parse::<NaiveDate>()
                .map_err(|_| invalid("Invalid publication date"))?,
            0,
            0,
        )?
    } else {
        acquired
    };
    let mut flags = tags(&title);
    let evidence = document.elements("p").find_map(|i| {
        let raw = document.text(i, false);
        (raw.chars().count() < 700 && !tags(&raw)["guidance_direction"].is_null())
            .then(|| document.text(i, true))
    });
    if let Some(evidence) = evidence.as_ref().filter(|s| !s.is_empty()) {
        flags["guidance_direction"] = tags(evidence)["guidance_direction"].clone();
    }
    Ok(
        json!({"external_id":url,"symbols":[symbol],"created_at":stamp(created),"updated_at":stamp(acquired.max(created)),"available_at":stamp(acquired.max(created)),"headline":title,"url":url,"source":"official_ir","flags":flags,"publication_original":original.as_ref().or(hint.as_ref()),"creation_timestamp_basis":if original.is_some(){"Official publication timestamp"}else if hint.is_some(){"Date-only URL lower bound; availability is acquisition"}else{"First observation; original publication unavailable"},"guidance_evidence_hash":evidence.as_ref().filter(|s|!s.is_empty()).map(|s|canonical::digest(&json!(s))).transpose()?,"original_update_timestamp":Value::Null,"availability_basis":"Observed acquisition; original publication/edit time unverified"}),
    )
}
pub fn publication_metadata(html: &str) -> Result<Option<(String, DateTime<Utc>)>> {
    let document = Document::parse(html)?;
    for i in document.elements("meta") {
        let key = document
            .attr(i, "name")
            .filter(|s| !s.is_empty())
            .or_else(|| document.attr(i, "property"))
            .unwrap_or("")
            .to_lowercase();
        if matches!(
            key.as_str(),
            "press_release_publish_date" | "article:published_time" | "datepublished" | "pubdate"
        ) {
            let value = document.attr(i, "content").unwrap_or("");
            match information_stamp(value) {
                Ok(time) => return Ok(Some((value.to_owned(), time))),
                Err(error) if matches!(error.kind, "ValueError" | "ProviderError") => {}
                Err(error) => return Err(error.into()),
            }
        }
    }
    Ok(None)
}
pub struct OfficialIr {
    pool: PgPool,
    transport: Arc<dyn Transport>,
    clock: Arc<dyn Clock>,
    delay: Arc<dyn Delay>,
    user_agent: String,
    html_workers: Arc<Semaphore>,
    requests: Arc<Semaphore>,
}
impl OfficialIr {
    pub fn new(
        pool: PgPool,
        transport: Arc<dyn Transport>,
        clock: Arc<dyn Clock>,
        delay: Arc<dyn Delay>,
        user_agent: String,
    ) -> Self {
        Self {
            pool,
            transport,
            clock,
            delay,
            user_agent,
            html_workers: Arc::new(Semaphore::new(2)),
            requests: Arc::new(Semaphore::new(2)),
        }
    }
    pub async fn get(&self, url: &str, enrichment: bool) -> Result<Page> {
        let mut url = url.to_owned();
        let agent = if self.user_agent.is_empty() && !enrichment {
            "KefTrade personal research"
        } else {
            &self.user_agent
        };
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|_| invalid("Official IR requests unavailable"))?;
        for _ in 0..4 {
            if !permitted(&url) {
                return Err(invalid("Official IR host is not permitted"));
            }
            let response = self.transport.get(&url, agent).await?;
            if matches!(response.status, 301 | 302 | 307 | 308) {
                url = join(&url, response.location.as_deref().unwrap_or(""))?;
                continue;
            }
            if !matches!(response.status, 200 | 404) || response.content.len() > PAGE_BYTES {
                return Err(invalid(
                    "Official IR page unavailable or exceeds size budget",
                ));
            }
            return Ok(response);
        }
        Err(invalid("Official IR redirect budget exceeded"))
    }
    async fn parse<T: Send + 'static>(
        &self,
        operation: impl FnOnce() -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let permit = self
            .html_workers
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| invalid("Official HTML parser unavailable"))?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            operation()
        })
        .await
        .map_err(|_| invalid("Official HTML parser unavailable"))?
    }
    pub async fn ingest(&self) -> Result<Value> {
        self.scoped()?.ingest_inner().await
    }
    fn scoped(&self) -> Result<Self> {
        Ok(Self {
            pool: self.pool.clone(),
            transport: self
                .transport
                .session()?
                .unwrap_or_else(|| self.transport.clone()),
            clock: self.clock.clone(),
            delay: self.delay.clone(),
            user_agent: self.user_agent.clone(),
            html_workers: self.html_workers.clone(),
            requests: self.requests.clone(),
        })
    }
    async fn ingest_inner(&self) -> Result<Value> {
        let mut results = Vec::new();
        for (symbol, listing) in [
            ("AXON", "https://investor.axon.com/press-releases/"),
            ("MSI", "https://www.motorolasolutions.com/newsroom.html"),
        ] {
            let result = self.ingest_listing(symbol, listing).await;
            results.push(match result{Ok(value)=>value,Err(Error::Invalid(_)|Error::Json(_))=>json!({"symbol":symbol,"status":"unavailable","reason":"Official IR access or robots restriction; no substitute feed"}),Err(e)=>return Err(e)});
        }
        Ok(json!(results))
    }
    async fn ingest_listing(&self, symbol: &'static str, listing: &'static str) -> Result<Value> {
        let robots = self.get(&join(listing, "/robots.txt")?, false).await?;
        let parser = Robots::parse(
            if robots.status == 200 {
                robots.text()
            } else {
                String::new()
            }
            .as_str(),
        );
        if robots.status == 200 && !parser.can_fetch(listing) {
            return Err(invalid("Official listing disallowed by robots"));
        }
        let response = self.get(listing, false).await?;
        if response.status != 200 {
            return Err(invalid("Official IR listing missing"));
        }
        // Original listing-observation timestamp retained for migration parity.
        let acquired = self.clock.now();
        let html = response.text();
        let links = self
            .parse(move || listing_links(symbol, listing, &html))
            .await?;
        let mut metadata = Vec::new();
        for (url, title) in links {
            if robots.status == 200 && !parser.can_fetch(&url) {
                continue;
            }
            let page = self.get(&url, false).await?;
            if page.status != 200 {
                continue;
            }
            self.delay.wait(250).await;
            let html = page.text();
            metadata.push(
                self.parse(move || article_metadata(symbol, &url, &title, &html, acquired))
                    .await?,
            );
        }
        let mut tx = self.pool.begin().await?;
        let source=store_document(&mut tx,"official_ir_metadata",&response.url,&json!({"page_hash":hex::encode(Sha256::digest(&response.content)),"announcements":metadata}),acquired).await?;
        for row in &metadata {
            persist_information(&mut tx, "official_ir", row, source, acquired).await?;
        }
        tx.commit().await?;
        Ok(
            json!({"symbol":symbol,"status":"complete","announcements":metadata.len(),"historical_eligible":false}),
        )
    }
    pub async fn enrich(&self) -> Result<Value> {
        self.scoped()?.enrich_inner().await
    }
    async fn enrich_inner(&self) -> Result<Value> {
        let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT DISTINCT ON(external_id) * FROM information_versions WHERE provider='official_ir' ORDER BY external_id,acquired_at DESC) r").fetch_all(&self.pool).await?;
        let mut permissions = BTreeMap::new();
        let mut count = 0;
        for row in rows {
            match self.enrich_row(&row, &mut permissions).await {
                Ok(true) => count += 1,
                Ok(false) | Err(Error::Invalid(_) | Error::Json(_)) => {}
                Err(e) => return Err(e),
            }
        }
        Ok(json!({"exact_publication_records":count,"historical_training_eligible":false}))
    }
    async fn enrich_row(
        &self,
        row: &Value,
        permissions: &mut BTreeMap<String, (u16, Robots)>,
    ) -> Result<bool> {
        let url = row["payload"]["url"]
            .as_str()
            .ok_or_else(|| invalid("Missing official publication URL"))?;
        let host = url::Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned))
            .ok_or_else(|| invalid("Invalid official publication URL"))?;
        if !permissions.contains_key(&host) {
            let robots = self.get(&join(url, "/robots.txt")?, true).await?;
            permissions.insert(
                host.clone(),
                (
                    robots.status,
                    Robots::parse(
                        if robots.status == 200 {
                            robots.text()
                        } else {
                            String::new()
                        }
                        .as_str(),
                    ),
                ),
            );
        }
        let (status, parser) = &permissions[&host];
        if *status == 200 && !parser.can_fetch(url) {
            return Ok(false);
        }
        let response = self.get(url, true).await?;
        if response.status != 200 {
            return Ok(false);
        }
        let html = response.text();
        let parse_html = html.clone();
        let Some((original, published)) = self
            .parse(move || publication_metadata(&parse_html))
            .await?
        else {
            return Ok(false);
        };
        let acquired = self.clock.now();
        let mut metadata = row["payload"].clone();
        for (key,value) in json!({"created_at":stamp(published),"updated_at":stamp(acquired.max(published)),"available_at":stamp(acquired.max(published)),"publication_original":original,"creation_timestamp_basis":"Exact official publication metadata","availability_basis":"Observed acquisition; edit history unverified","source_adapter_version":"official-publication-v1"}).as_object().expect("object"){metadata[key]=value.clone();}
        let mut tx = self.pool.begin().await?;
        let source=store_document(&mut tx,"official_ir_publication_metadata",url,&json!({"publication_original":original,"page_hash":canonical::digest(&json!(html))?,"adapter":"official-publication-v1"}),acquired).await?;
        persist_information(&mut tx, "official_ir", &metadata, source, acquired).await?;
        tx.commit().await?;
        self.delay.wait(250).await;
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    #[ignore = "requires scripts/test-rust-ir-http.py loopback TLS oracle"]
    async fn loopback_tls_http_cookies_compression_and_limits_match_python() {
        let path = std::path::PathBuf::from(std::env::var("RUST_IR_TLS_ORACLE").expect("oracle"));
        let fixture: Value =
            serde_json::from_slice(&std::fs::read(path).expect("oracle")).expect("JSON");
        let address: std::net::SocketAddr = fixture["address"]
            .as_str()
            .expect("address")
            .parse()
            .expect("socket");
        assert!(address.ip().is_loopback() && address.port() > 1024);
        let ca = std::fs::read(fixture["ca_file"].as_str().expect("CA")).expect("CA");
        assert_eq!(hex::encode(Sha256::digest(&ca)), fixture["ca_sha256"]);
        let root = reqwest::Certificate::from_pem(&ca).expect("test CA");
        let factory: ClientFactory = Arc::new(move || {
            let mut builder = reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(4))
                .redirect(reqwest::redirect::Policy::none())
                .cookie_provider(Arc::new(crate::ir_cookies::Jar::default()))
                .add_root_certificate(root.clone())
                .default_headers({
                    let mut h = reqwest::header::HeaderMap::new();
                    h.insert(
                        "x-fixture-client",
                        reqwest::header::HeaderValue::from_static("rust"),
                    );
                    h
                });
            for host in [
                "investor.axon.com",
                "www.motorolasolutions.com",
                "newsroom.motorolasolutions.com",
                "evil.invalid",
            ] {
                builder = builder.resolve(host, address);
            }
            builder
                .build()
                .map_err(|_| invalid("test client unavailable"))
        });
        let transport = Arc::new(NativeTransport {
            client: factory().expect("client"),
            factory,
        });
        let pool = sqlx::postgres::PgPoolOptions::new()
            .connect_lazy("postgresql://fixture:fixture@127.0.0.1:5543/intelligence_test_no_io")
            .expect("lazy, never connected");
        let ir = OfficialIr::new(
            pool,
            transport,
            Arc::new(TestClock),
            Arc::new(crate::research_providers::TokioDelay),
            "fixture-ir-test".into(),
        );
        for group in fixture["groups"].as_array().expect("groups") {
            let scoped = ir.scoped().expect("fresh cookie session");
            for case in group.as_array().expect("group") {
                let actual = scoped.get(case["url"].as_str().expect("URL"), false).await;
                if let Some(expected) = case.get("error") {
                    assert!(
                        matches!(actual,Err(Error::Invalid(reason)) if reason==expected.as_str().expect("error")),
                        "{}",
                        case["url"]
                    );
                } else {
                    let page = actual.unwrap_or_else(|e| panic!("{}: {e}", case["url"]));
                    let result = json!({"url":page.url,"status":page.status,"sha256":hex::encode(Sha256::digest(&page.content)),"text":if page.content.len()<=1024{Some(page.text())}else{None}});
                    assert_eq!(result, case["result"], "{}", case["url"]);
                }
            }
        }
        println!("NATIVE_REAL_TLS_RESPONSE_BYTES_AND_PER_WORKFLOW_COOKIE_SESSIONS=PASS");
    }
    use std::sync::{
        Condvar, Mutex,
        atomic::{AtomicUsize, Ordering},
    };
    struct NoHttp;
    #[async_trait]
    impl Transport for NoHttp {
        async fn get(&self, _: &str, _: &str) -> Result<Page> {
            panic!("no network in parser ownership test")
        }
    }
    struct TestClock;
    impl Clock for TestClock {
        fn now(&self) -> DateTime<Utc> {
            "2026-11-20T06:00:00Z".parse().expect("time")
        }
    }
    struct Active(Arc<AtomicUsize>);
    struct ReleaseOnDrop(Arc<(Mutex<bool>, Condvar)>);
    impl Drop for ReleaseOnDrop {
        fn drop(&mut self) {
            let (lock, cond) = &*self.0;
            *lock.lock().unwrap_or_else(|e| e.into_inner()) = true;
            cond.notify_all();
        }
    }
    impl Drop for Active {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn cancelled_callers_retain_two_parser_slots_until_blocking_work_finishes() {
        let pool = sqlx::postgres::PgPoolOptions::new()
            .connect_lazy("postgresql://fixture:fixture@127.0.0.1:5543/intelligence_test_no_io")
            .expect("lazy pool, no connection");
        let ir = Arc::new(OfficialIr::new(
            pool,
            Arc::new(NoHttp),
            Arc::new(TestClock),
            Arc::new(crate::research_providers::TokioDelay),
            String::new(),
        ));
        let release = Arc::new((Mutex::new(false), Condvar::new()));
        let _release_on_failure = ReleaseOnDrop(release.clone());
        let entered = Arc::new(Semaphore::new(0));
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let mut tasks = Vec::new();
        for _ in 0..12 {
            let ir = ir.clone();
            let release = release.clone();
            let entered = entered.clone();
            let active = active.clone();
            let peak = peak.clone();
            tasks.push(tokio::spawn(async move {
                ir.parse(move || {
                    let n = active.fetch_add(1, Ordering::SeqCst) + 1;
                    let _active = Active(active);
                    peak.fetch_max(n, Ordering::SeqCst);
                    entered.add_permits(1);
                    let (lock, cond) = &*release;
                    let mut ready = lock.lock().expect("gate");
                    while !*ready {
                        ready = cond.wait(ready).expect("gate");
                    }
                    Ok(())
                })
                .await
            }));
        }
        tokio::time::timeout(Duration::from_secs(3), entered.acquire_many(2))
            .await
            .expect("two workers")
            .expect("permits")
            .forget();
        for task in &tasks {
            task.abort();
        }
        for task in tasks {
            assert!(task.await.expect_err("cancelled").is_cancelled());
        }
        assert_eq!(active.load(Ordering::SeqCst), 2);
        let later = ir.clone();
        let entered_later = entered.clone();
        let task = tokio::spawn(async move {
            later
                .parse(move || {
                    entered_later.add_permits(1);
                    Ok(())
                })
                .await
        });
        assert!(
            tokio::time::timeout(Duration::from_millis(30), entered.acquire())
                .await
                .is_err(),
            "cancelled HTTP callers cannot release active CPU slots"
        );
        let (lock, cond) = &*release;
        *lock.lock().expect("gate") = true;
        cond.notify_all();
        task.await.expect("later parser").expect("result");
        tokio::time::timeout(Duration::from_secs(3), async {
            while active.load(Ordering::SeqCst) != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("workers finish");
        assert_eq!(peak.load(Ordering::SeqCst), 2);
    }
}
