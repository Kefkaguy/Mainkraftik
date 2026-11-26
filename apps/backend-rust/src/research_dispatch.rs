//! Actual native research composition. Only validated dependency chains are wired.
//! No brokerage transport, training fallback, deployment entry point or model promotion.
use crate::{
    Error, Result,
    forecasting::Forecaster,
    information::Information,
    ingestion::{Ingester, PRICE_PROJECTION, ResourceCheck, iteration_symbols},
    jobs::{Dispatch, DispatchError},
    official_ir::OfficialIr,
};
use async_trait::async_trait;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::Arc;
use uuid::Uuid;

pub struct NativeDispatch {
    pool: PgPool,
    resources: Arc<dyn ResourceCheck>,
    ingestion: Arc<Ingester>,
    information: Arc<Information>,
    publications: Arc<OfficialIr>,
    forecasts: Arc<Forecaster>,
    evaluation: crate::research_evaluation_store::Evaluator,
    audit: crate::research_audit::Auditor,
}
impl NativeDispatch {
    pub fn new(
        pool: PgPool,
        resources: Arc<dyn ResourceCheck>,
        ingestion: Arc<Ingester>,
        information: Arc<Information>,
        publications: Arc<OfficialIr>,
        forecasts: Arc<Forecaster>,
    ) -> Self {
        Self {
            evaluation: forecasts.evaluator(),
            audit: forecasts.auditor(),
            pool,
            resources,
            ingestion,
            information,
            publications,
            forecasts,
        }
    }
    pub fn implemented(kind: &str) -> bool {
        matches!(
            kind,
            "refresh" | "information" | "resolve" | "issue" | "monitor" | "evaluate" | "audit"
        )
    }
    async fn information(&self) -> Result<Value> {
        let mut result = self
            .information
            .refresh(&self.publications, &self.ingestion, false)
            .await?;
        result["publication_metadata"] = self.publications.enrich().await?;
        Ok(result)
    }
    async fn symbols(&self, payload: &Value) -> Result<Value> {
        if !payload.is_object() {
            return Err(Error::Reference {
                kind: "AttributeError",
                message: "Refresh payload has no get method".into(),
            });
        }
        let mut tx = self.pool.begin().await?;
        let input = if let Some(value) = payload.get("symbols").filter(|v| truthy(v)) {
            value.clone()
        } else {
            let mut value: Value = sqlx::query_scalar(
                "SELECT symbols FROM watchlist_versions ORDER BY id DESC LIMIT 1",
            )
            .fetch_one(&mut *tx)
            .await?;
            value
                .as_array_mut()
                .ok_or_else(|| crate::invalid("Invalid watchlist symbols"))?
                .push(json!("SPY"));
            value
        };
        let known: Vec<String> = sqlx::query_scalar("SELECT symbol FROM securities")
            .fetch_all(&mut *tx)
            .await?;
        tx.commit().await?;
        let symbols = iteration_symbols(&input)?;
        if symbols.len() > 26 || !symbols.iter().all(|s| known.contains(s)) {
            return Err(crate::invalid(
                "Refresh symbols must belong to the verified securities registry",
            ));
        }
        Ok(input)
    }
    async fn core(&self, id: Uuid, kind: &str, payload: &Value) -> Result<Value> {
        self.resources.check(PRICE_PROJECTION).await?;
        match kind {
            "refresh"=>{
                let symbols=self.symbols(payload).await?;
                let mut result=self.ingestion.resume_input(id,&symbols).await?;
                match self.information().await {
                    Ok(information)=>{
                        if information["news"]["status"]!="complete"{result["status"]=json!("partial");}
                        result["information"]=information;
                    },
                    Err(_)=>{
                        result["information"]=json!({"status":"unavailable","reason":"Information refresh failed; reference data preserved"});
                        result["status"]=json!("partial");
                    },
                }
                result["outcomes"]=self.forecasts.resolve().await?;
                result["detection_outcomes"]=self.forecasts.resolve_detections().await?;
                Ok(result)
            },
            "information"=>self.information().await,
            "resolve"=>Ok(json!({"forecasts":self.forecasts.resolve().await?,"detections":self.forecasts.resolve_detections().await?})),
            "issue"=>{
                let mut result=self.forecasts.issue().await?;
                result["shadow"]=match self.forecasts.issue_existing_enrollment().await {
                    Ok(value)=>value,
                    Err(error)=>json!({"status":"skipped","reason":format!("{}: frozen shadow failed; reference retained",reference_kind(&error))}),
                };
                Ok(result)
            },
            "monitor"=>{
                let mut result=self.forecasts.monitor().await?;
                result["shadow"]=crate::prospective::progress_and_verdict(&self.pool,true).await?;
                Ok(result)
            },
            "evaluate"=>{
                let symbol=payload.get("symbol").ok_or_else(||Error::Reference{kind:"KeyError",message:"Evaluation symbol missing".into()})?.as_str().ok_or_else(||crate::invalid("Invalid evaluation symbol"))?;
                self.evaluation.run(symbol).await
            },
            "audit"=>self.audit.run().await,
            other if crate::jobs::supported(other)=>Err(Error::Reference{kind:"NativeWorkflowUnavailable",message:"Remaining native workflow dependencies are not validated; execution unavailable".into()}),
            _=>Err(crate::invalid("Unsupported job")),
        }
    }
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}
fn dispatch_error(error: Error) -> DispatchError {
    match error {
        Error::Invalid(message) => DispatchError::Value(message),
        other => DispatchError::Unexpected(reference_kind(&other)),
    }
}
fn reference_kind(error: &Error) -> &'static str {
    match error {
        Error::Invalid(_) => "ValueError",
        Error::Reference { kind, .. } => kind,
        Error::Database(error) => match error.as_database_error().and_then(|e| e.code()).as_deref()
        {
            Some("P0001") => "RaiseException",
            Some("23505") => "UniqueViolation",
            Some("23514") => "CheckViolation",
            Some("23503") => "ForeignKeyViolation",
            _ => "DatabaseError",
        },
        Error::Io(_) => "OSError",
        Error::Json(_) => "JSONDecodeError",
        Error::Broker(_) => "BrokerError",
    }
}
#[async_trait]
impl Dispatch for NativeDispatch {
    async fn execute(
        &self,
        kind: &str,
        payload: &Value,
    ) -> std::result::Result<Value, DispatchError> {
        self.execute_job(Uuid::new_v4(), kind, payload).await
    }
    async fn execute_job(
        &self,
        id: Uuid,
        kind: &str,
        payload: &Value,
    ) -> std::result::Result<Value, DispatchError> {
        self.core(id, kind, payload).await.map_err(dispatch_error)
    }
}
