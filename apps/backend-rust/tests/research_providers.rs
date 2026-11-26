use async_trait::async_trait;
use mainkraft::{
    Error, Result,
    calendar::Calendar,
    research_providers::{self as p, Credentials, Delay, Providers, Request, Response, Transport},
};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};
fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/research-providers.json")).expect("frozen oracle")
}
fn reason(error: Error) -> String {
    match error {
        Error::Invalid(message) => message,
        other => panic!("Unexpected error {other}"),
    }
}
#[test]
fn research_decimal_validation_and_sec_filing_identities_match_python() {
    let fixture = fixture();
    let calendar = Calendar::embedded().expect("calendar");
    for case in fixture["normalization"].as_array().expect("cases") {
        let actual = p::normalize_bars(
            &calendar,
            "MSI",
            case["raw"].as_array().expect("raw"),
            case["feed"].as_str().expect("feed"),
            case["asof"].as_str().expect("asof").parse().expect("date"),
            case["acquired"]
                .as_str()
                .expect("time")
                .parse()
                .expect("time"),
        );
        if let Some(error) = case.get("error") {
            assert_eq!(
                actual.err().map(reason).as_deref(),
                error.as_str(),
                "{}",
                case["name"]
            );
        } else {
            let rows = actual.unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
            assert_eq!(
                rows.iter().map(|r| r.record.clone()).collect::<Vec<_>>(),
                *case["rows"].as_array().expect("rows"),
                "{}",
                case["name"]
            );
            if case["name"] == "exact_decimals" {
                assert_eq!(
                    rows[1].amounts[0].to_string(),
                    "100.00000000000000000002",
                    "NUMERIC persistence must retain precision beyond f64"
                );
            }
        }
    }
    for case in fixture["availability"].as_array().expect("availability") {
        assert_eq!(
            p::filing_availability(
                case["filed"].as_str().expect("filed"),
                case["accession"].as_str().expect("accession"),
                &case["submissions"]
            )
            .expect("availability"),
            case["result"]
        );
    }
    for case in fixture["facts"].as_array().expect("facts") {
        let actual = p::normalize_facts(
            "MSI",
            &case["facts"],
            &case["submissions"],
            case["acquired"]
                .as_str()
                .expect("time")
                .parse()
                .expect("time"),
        )
        .expect("facts");
        assert_eq!(
            actual,
            *case["rows"].as_array().expect("rows"),
            "all values, source URLs and canonical hashes must match"
        );
    }
    let edge: Value = serde_json::from_str(include_str!(
        "fixtures/research-provider-decimal-edges.json"
    ))
    .expect("edges");
    let rows = p::normalize_bars(
        &calendar,
        "MSI",
        edge["raw"].as_array().expect("raw"),
        "sip",
        edge["asof"].as_str().expect("date").parse().expect("date"),
        edge["acquired"]
            .as_str()
            .expect("time")
            .parse()
            .expect("time"),
    )
    .expect("negative zeros");
    assert_eq!(
        rows.iter().map(|r| r.record.clone()).collect::<Vec<_>>(),
        *edge["rows"].as_array().expect("rows")
    );
    assert!(rows.iter().all(|r| {
        r.record["volume"]
            .as_f64()
            .expect("volume")
            .is_sign_negative()
    }));
}
struct FakeTransport {
    responses: Mutex<VecDeque<Value>>,
    calls: Mutex<Vec<Value>>,
    timeouts: Mutex<Vec<u64>>,
}
#[async_trait]
impl Transport for FakeTransport {
    async fn send(&self, request: &Request) -> Result<Response> {
        self.calls
            .lock()
            .expect("calls")
            .push(json!({"url":request.url,"query":request.query,"headers":request.headers}));
        self.timeouts
            .lock()
            .expect("timeouts")
            .push(request.timeout_seconds);
        let next = self
            .responses
            .lock()
            .expect("responses")
            .pop_front()
            .expect("no extra requests");
        if next.is_string() {
            return Err(Error::Invalid(
                "Provider connection failed (ConnectError); no data substituted".into(),
            ));
        }
        Ok(Response {
            status: next["status"].as_u64().expect("status") as u16,
            payload: next.get("payload").cloned().unwrap_or_else(|| json!({})),
        })
    }
}
#[derive(Default)]
struct FakeDelay(Mutex<Vec<u64>>);
#[async_trait]
impl Delay for FakeDelay {
    async fn wait(&self, milliseconds: u64) {
        self.0.lock().expect("delays").push(milliseconds);
    }
}
#[tokio::test]
async fn research_provider_request_pagination_retry_and_no_substitution_match_python() {
    for case in fixture()["wire"].as_array().expect("wire") {
        let fake = Arc::new(FakeTransport {
            responses: Mutex::new(
                case["responses"]
                    .as_array()
                    .expect("responses")
                    .iter()
                    .cloned()
                    .collect(),
            ),
            calls: Mutex::new(Vec::new()),
            timeouts: Mutex::new(Vec::new()),
        });
        let delay = Arc::new(FakeDelay::default());
        let c = &case["overrides"];
        let providers = Providers::new(
            Credentials {
                key: c["alpaca_key"].as_str().unwrap_or("fixture-key").into(),
                secret: "fixture-secret".into(),
                feed: "sip".into(),
                sec_user_agent: c["sec_user_agent"]
                    .as_str()
                    .unwrap_or("Mainkraft fixture fixture@example.invalid")
                    .into(),
            },
            fake.clone(),
            delay.clone(),
        )
        .expect("provider");
        let a = &case["args"];
        let symbol = a["symbol"].as_str().expect("symbol");
        let start = a["start"].as_str().expect("start").parse().expect("date");
        let end = a["end"].as_str().expect("end").parse().expect("time");
        let asof = a["asof"].as_str().expect("asof").parse().expect("date");
        let actual = match case["operation"].as_str().expect("operation") {
            "bars" => providers
                .fetch_bars(symbol, start, end, asof)
                .await
                .map(|(bars, payload, url)| json!([bars, payload, url])),
            "actions" => providers
                .fetch_actions(symbol, start, asof)
                .await
                .map(|(payload, url)| json!([payload, url])),
            "sec" => providers
                .fetch_sec(a["cik"].as_str().expect("CIK"))
                .await
                .map(|(facts, submissions, facts_url, submissions_url)| {
                    json!([facts, submissions, facts_url, submissions_url])
                }),
            "company" => providers.lookup_company(symbol).await,
            _ => panic!("unknown operation"),
        };
        if case.get("error").is_some() {
            assert_eq!(
                actual.err().map(reason).as_deref(),
                case["error"].as_str(),
                "{}",
                case["name"]
            );
        } else {
            assert_eq!(
                actual.unwrap_or_else(|e| panic!("{}: {e}", case["name"])),
                case["result"],
                "{}",
                case["name"]
            );
        }
        assert_eq!(
            *fake.calls.lock().expect("calls"),
            *case["requests"].as_array().expect("requests"),
            "request {}",
            case["name"]
        );
        assert_eq!(
            json!(*delay.0.lock().expect("delays")),
            case["delays"],
            "delays {}",
            case["name"]
        );
        let expected_timeout = match case["operation"].as_str().expect("operation") {
            "sec" => 60,
            "company" => 30,
            _ => 45,
        };
        assert!(
            fake.timeouts
                .lock()
                .expect("timeouts")
                .iter()
                .all(|n| *n == expected_timeout)
        );
        assert!(
            fake.responses.lock().expect("responses").is_empty(),
            "all expected requests must run"
        );
    }
}

struct BlockingTransport {
    entered: tokio::sync::Semaphore,
    release: tokio::sync::Semaphore,
    active: std::sync::atomic::AtomicUsize,
    peak: std::sync::atomic::AtomicUsize,
}
struct Active<'a>(&'a std::sync::atomic::AtomicUsize);
impl Drop for Active<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
    }
}
#[async_trait]
impl Transport for BlockingTransport {
    async fn send(&self, _: &Request) -> Result<Response> {
        use std::sync::atomic::Ordering::SeqCst;
        let now = self.active.fetch_add(1, SeqCst) + 1;
        let _active = Active(&self.active);
        self.peak.fetch_max(now, SeqCst);
        self.entered.add_permits(1);
        self.release.acquire().await.expect("release").forget();
        Ok(Response {
            status: 200,
            payload: json!({}),
        })
    }
}
#[tokio::test]
async fn research_requests_remain_bounded_and_cancelled_requests_release_capacity() {
    use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
    let transport = Arc::new(BlockingTransport {
        entered: tokio::sync::Semaphore::new(0),
        release: tokio::sync::Semaphore::new(0),
        active: AtomicUsize::new(0),
        peak: AtomicUsize::new(0),
    });
    let providers = Arc::new(
        Providers::new(
            Credentials {
                key: "fixture-key".into(),
                secret: "fixture-secret".into(),
                feed: "sip".into(),
                sec_user_agent: "fixture@example.invalid".into(),
            },
            transport.clone(),
            Arc::new(FakeDelay::default()),
        )
        .expect("provider"),
    );
    let mut tasks = Vec::new();
    for _ in 0..12 {
        let providers = providers.clone();
        tasks.push(tokio::spawn(async move {
            providers
                .fetch_bars(
                    "MSI",
                    "2026-01-01".parse().expect("date"),
                    "2026-11-19T06:00:00Z".parse().expect("time"),
                    "2026-11-18".parse().expect("date"),
                )
                .await
        }));
    }
    tokio::time::timeout(
        std::time::Duration::from_secs(5),
        transport.entered.acquire_many(4),
    )
    .await
    .expect("four requests enter")
    .expect("permits")
    .forget();
    assert_eq!(transport.active.load(SeqCst), 4);
    assert_eq!(transport.peak.load(SeqCst), 4);
    // Abort and await every caller; no admitted HTTP future or permit may survive.
    for task in &tasks {
        task.abort();
    }
    for task in tasks {
        assert!(task.await.expect_err("cancelled").is_cancelled());
    }
    assert_eq!(transport.active.load(SeqCst), 0);
    transport.release.add_permits(1);
    providers
        .fetch_bars(
            "MSI",
            "2026-01-01".parse().expect("date"),
            "2026-11-19T06:00:00Z".parse().expect("time"),
            "2026-11-18".parse().expect("date"),
        )
        .await
        .expect("capacity recovered");
    assert_eq!(transport.active.load(SeqCst), 0);
    assert_eq!(transport.peak.load(SeqCst), 4);
}
