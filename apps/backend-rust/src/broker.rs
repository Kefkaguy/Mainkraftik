//! A literal PAPER host and typed operations. Test brokers implement the trait;
//! an alternate HTTP execution endpoint cannot be configured, even for tests.
use async_trait::async_trait;
use reqwest::{
    Client, Method,
    header::{HeaderMap, HeaderValue},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::time::Duration;
use uuid::Uuid;

pub const PAPER_HOST: &str = "https://paper-api.alpaca.markets";

pub struct AccountConfig {
    pub id: String,
    pub account_number: Option<String>,
    pub key: String,
    pub secret: String,
    pub label: String,
    pub adaptive: bool,
}
impl std::fmt::Debug for AccountConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AccountConfig")
            .field("id", &self.id)
            .field("credentials", &"[redacted]")
            .finish()
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BrokerError {
    #[error("paper account identity/permissions rejected")]
    Identity,
    #[error("invalid paper operation")]
    Invalid,
    #[error("paper broker explicitly rejected order: {0}")]
    Rejected(String),
    #[error("paper acknowledgement unavailable; reconcile by client ID")]
    Unknown,
    #[error("paper data unavailable")]
    Unavailable,
    #[error("paper execution gate has not passed")]
    NotPermitted,
}
pub type BrokerResult<T> = std::result::Result<T, BrokerError>;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct OrderBody {
    pub symbol: String,
    pub side: String,
    pub qty: String,
    #[serde(rename = "type")]
    pub order_type: String,
    pub time_in_force: String,
    pub client_order_id: String,
}

#[async_trait]
pub trait BrokerApi: Send + Sync {
    async fn active_equities(&self) -> BrokerResult<Vec<Value>> {
        Err(BrokerError::Unavailable)
    }
    async fn account(&self) -> BrokerResult<Value>;
    async fn clock(&self) -> BrokerResult<Value>;
    async fn positions(&self) -> BrokerResult<Vec<Value>>;
    async fn orders(&self) -> BrokerResult<Vec<Value>>;
    async fn asset(&self, symbol: &str) -> BrokerResult<Value>;
    async fn by_client(&self, key: &str) -> BrokerResult<Option<Value>>;
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value>;
    async fn cancel(&self, id: Uuid) -> BrokerResult<()>;
}

// Intentionally no public constructor: the cutover verifier will be the only
// module that can mint a permit once all migration gates are implemented.
pub struct ExecutionPermit {
    _private: (),
}

pub struct PaperBroker {
    config: AccountConfig,
    client: Client,
    permit: Option<ExecutionPermit>,
}
impl PaperBroker {
    pub(crate) fn with_cutover(
        config: AccountConfig,
        _: &crate::runtime::VerifiedCutover,
    ) -> BrokerResult<Self> {
        let mut broker = Self::new(config)?;
        broker.permit = Some(ExecutionPermit { _private: () });
        Ok(broker)
    }
    pub fn new(config: AccountConfig) -> BrokerResult<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| BrokerError::Unavailable)?;
        Ok(Self {
            config,
            client,
            permit: None,
        })
    }
    fn headers(&self) -> BrokerResult<HeaderMap> {
        let mut headers = HeaderMap::new();
        for (name, value) in [
            ("apca-api-key-id", &self.config.key),
            ("apca-api-secret-key", &self.config.secret),
        ] {
            let mut value = HeaderValue::from_str(value).map_err(|_| BrokerError::Invalid)?;
            value.set_sensitive(true);
            headers.insert(name, value);
        }
        Ok(headers)
    }
    async fn request(
        &self,
        method: Method,
        path: &str,
        query: &[(&str, &str)],
        body: Option<&OrderBody>,
    ) -> BrokerResult<Option<Value>> {
        let mut request = self
            .client
            .request(method.clone(), format!("{PAPER_HOST}{path}"))
            .headers(self.headers()?)
            .query(query);
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request.send().await.map_err(|_| {
            if method == Method::POST {
                BrokerError::Unknown
            } else {
                BrokerError::Unavailable
            }
        })?;
        let status = response.status();
        if status.as_u16() == 404 && path == "/v2/orders:by_client_order_id" {
            return Ok(None);
        }
        if method == Method::POST && status.is_client_error() {
            let data: Value = response.json().await.unwrap_or(Value::Null);
            let mut message = data["message"]
                .as_str()
                .unwrap_or("")
                .chars()
                .take(300)
                .collect::<String>();
            for secret in [&self.config.key, &self.config.secret] {
                if !secret.is_empty() {
                    message = message.replace(secret, "[redacted]");
                }
            }
            return Err(BrokerError::Rejected(format!(
                "HTTP {}: {message}",
                status.as_u16()
            )));
        }
        if !status.is_success() {
            return Err(if method == Method::POST {
                BrokerError::Unknown
            } else {
                BrokerError::Unavailable
            });
        }
        let bytes = response.bytes().await.map_err(|_| BrokerError::Unknown)?;
        if bytes.is_empty() {
            return Ok(Some(json!({})));
        }
        serde_json::from_slice(&bytes).map(Some).map_err(|_| {
            if method == Method::POST {
                BrokerError::Unknown
            } else {
                BrokerError::Unavailable
            }
        })
    }
    async fn get(&self, path: &str) -> BrokerResult<Value> {
        self.request(Method::GET, path, &[], None)
            .await?
            .ok_or(BrokerError::Unavailable)
    }
    async fn mutation_account(&self) -> BrokerResult<()> {
        if self.permit.is_none() {
            return Err(BrokerError::NotPermitted);
        }
        let account = self.account().await?;
        if account["status"] != "ACTIVE"
            || account["trading_blocked"].as_bool() == Some(true)
            || account["account_blocked"].as_bool() == Some(true)
        {
            return Err(BrokerError::Identity);
        }
        Ok(())
    }
}

pub fn valid_symbol(symbol: &str) -> bool {
    !symbol.is_empty()
        && symbol.len() <= 10
        && symbol
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'.' || b == b'-')
}

#[async_trait]
impl BrokerApi for PaperBroker {
    async fn active_equities(&self) -> BrokerResult<Vec<Value>> {
        self.request(
            Method::GET,
            "/v2/assets",
            &[("status", "active"), ("asset_class", "us_equity")],
            None,
        )
        .await?
        .and_then(|v| v.as_array().cloned())
        .ok_or(BrokerError::Unavailable)
    }
    async fn account(&self) -> BrokerResult<Value> {
        let account = self.get("/v2/account").await?;
        if account["id"].as_str() != Some(&self.config.id)
            || self
                .config
                .account_number
                .as_ref()
                .is_some_and(|n| account["account_number"].as_str() != Some(n))
        {
            return Err(BrokerError::Identity);
        }
        Ok(account)
    }
    async fn clock(&self) -> BrokerResult<Value> {
        self.get("/v2/clock").await
    }
    async fn positions(&self) -> BrokerResult<Vec<Value>> {
        serde_json::from_value(self.get("/v2/positions").await?)
            .map_err(|_| BrokerError::Unavailable)
    }
    async fn orders(&self) -> BrokerResult<Vec<Value>> {
        serde_json::from_value(
            self.request(
                Method::GET,
                "/v2/orders",
                &[("status", "open"), ("limit", "500")],
                None,
            )
            .await?
            .ok_or(BrokerError::Unavailable)?,
        )
        .map_err(|_| BrokerError::Unavailable)
    }
    async fn asset(&self, symbol: &str) -> BrokerResult<Value> {
        if !valid_symbol(symbol) {
            return Err(BrokerError::Invalid);
        }
        self.get(&format!("/v2/assets/{symbol}")).await
    }
    async fn by_client(&self, key: &str) -> BrokerResult<Option<Value>> {
        self.request(
            Method::GET,
            "/v2/orders:by_client_order_id",
            &[("client_order_id", key)],
            None,
        )
        .await
    }
    async fn submit(&self, body: &OrderBody) -> BrokerResult<Value> {
        use bigdecimal::{BigDecimal, Zero};
        use std::str::FromStr;
        if !valid_symbol(&body.symbol)
            || !matches!(body.side.as_str(), "buy" | "sell")
            || body.order_type != "market"
            || body.time_in_force != "day"
            || !body.client_order_id.starts_with("mkx-")
            || BigDecimal::from_str(&body.qty).map_or(true, |n| n <= BigDecimal::zero())
        {
            return Err(BrokerError::Invalid);
        }
        self.mutation_account().await?;
        self.request(Method::POST, "/v2/orders", &[], Some(body))
            .await?
            .ok_or(BrokerError::Unknown)
    }
    async fn cancel(&self, id: Uuid) -> BrokerResult<()> {
        self.mutation_account().await?;
        self.request(Method::DELETE, &format!("/v2/orders/{id}"), &[], None)
            .await?;
        Ok(())
    }
}

pub fn terminal(state: &str) -> bool {
    matches!(
        state,
        "filled" | "canceled" | "expired" | "rejected" | "replaced"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn incomplete_migration_cannot_submit_or_cancel() {
        let b = PaperBroker::new(AccountConfig {
            id: "pinned".into(),
            account_number: None,
            key: "test".into(),
            secret: "test".into(),
            label: "test".into(),
            adaptive: false,
        })
        .expect("client");
        let order = OrderBody {
            symbol: "AMD".into(),
            side: "buy".into(),
            qty: "1".into(),
            order_type: "market".into(),
            time_in_force: "day".into(),
            client_order_id: "mkx-test".into(),
        };
        assert!(matches!(
            b.submit(&order).await,
            Err(BrokerError::NotPermitted)
        ));
        assert!(matches!(
            b.cancel(Uuid::nil()).await,
            Err(BrokerError::NotPermitted)
        ));
        assert!(!valid_symbol("https://api.alpaca.markets"));
        assert!(!valid_symbol("../orders"));
    }
}
