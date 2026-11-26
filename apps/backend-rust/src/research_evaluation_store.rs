//! Immutable evaluation persistence; sealed holdouts and existing reports are never refitted.
use crate::{
    Result,
    calendar::Calendar,
    canonical, invalid,
    research_evaluation::{evaluate, protocol},
    research_training::{Limits, Trainer},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::{collections::BTreeMap, sync::Arc};
use uuid::Uuid;
pub struct Evaluator {
    pool: PgPool,
    calendar: Arc<Calendar>,
    feed: String,
    trainer: Arc<Trainer>,
}
impl Evaluator {
    pub fn new(pool: PgPool, calendar: Arc<Calendar>, feed: String) -> Self {
        Self {
            pool,
            calendar,
            feed,
            trainer: Trainer::new(Limits::default()),
        }
    }
    pub async fn run(&self, symbol: &str) -> Result<Value> {
        let mut c = self.pool.acquire().await?;
        let sealed: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM research_plans)")
            .fetch_one(&mut *c)
            .await?;
        if sealed {
            let existing:Option<(Uuid,Value)>=sqlx::query_as("SELECT id,report FROM evaluation_reports WHERE model_id='technical-v1' AND symbol=$1 ORDER BY created_at DESC LIMIT 1").bind(symbol).fetch_optional(&mut *c).await?;
            let Some((id, report)) = existing else {
                return Err(invalid(
                    "Original holdout is sealed; use the bounded development research ledger",
                ));
            };
            sqlx::query("INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES($1,$2,'sealed_holdout_read_only')").bind(Uuid::new_v4()).bind(id).execute(&mut *c).await?;
            return merged(
                json!({"id":id,"reused_frozen_report":true,"holdout_sealed":true}),
                report,
            );
        }
        let query = "WITH basis AS (SELECT max(adjustment_asof) d FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split') SELECT to_jsonb(r)||jsonb_build_object('open',r.open::text,'close',r.close::text) FROM (SELECT DISTINCT ON(session_date) * FROM daily_bars WHERE symbol=$1 AND feed=$2 AND adjustment='split' AND adjustment_asof=(SELECT d FROM basis) ORDER BY session_date,acquired_at DESC,id DESC) r";
        let bars: Vec<Value> = sqlx::query_scalar(query)
            .bind(symbol)
            .bind(&self.feed)
            .fetch_all(&mut *c)
            .await?;
        let spy: Vec<Value> = sqlx::query_scalar(query)
            .bind("SPY")
            .bind(&self.feed)
            .fetch_all(&mut *c)
            .await?;
        let original: Value =
            serde_json::from_str(include_str!("../assets/research-evaluation-identity.json"))?;
        let identity = original["legacy_protocol_implementation_hash"]
            .as_str()
            .ok_or_else(|| invalid("Missing frozen protocol identity"))?;
        let combined: Vec<_> = bars.iter().chain(&spy).collect();
        let manifest = json!({"kind":"historical_evaluation","bar_ids":combined.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"hashes":combined.iter().map(|r|r["content_hash"].clone()).collect::<Vec<_>>(),"feed":self.feed,"adjustment":"split","adjustment_asof":combined.last().map(|r|r["adjustment_asof"].clone()),"availability_basis":"Archived acquisition times for live forecasts; reconstructed exchange availability for historical evaluation","symbol":symbol,"protocol":protocol(),"implementation_hash":identity});
        let dataset = canonical::digest(&manifest)?;
        sqlx::query("INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES($1,'historical_evaluation',$2,'split',$3) ON CONFLICT DO NOTHING").bind(&dataset).bind(&self.feed).bind(manifest).execute(&mut *c).await?;
        let existing:Option<(Uuid,Value)>=sqlx::query_as("SELECT id,report FROM evaluation_reports WHERE model_id='technical-v1' AND dataset_id=$1").bind(&dataset).fetch_optional(&mut *c).await?;
        if let Some((id, report)) = existing {
            sqlx::query("INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES($1,$2,'read_existing_frozen_report')").bind(Uuid::new_v4()).bind(id).execute(&mut *c).await?;
            return merged(json!({"id":id,"reused_frozen_report":true}), report);
        }
        drop(c);
        let calendar = self.calendar.clone();
        let name = symbol.to_owned();
        let mut report = self
            .trainer
            .compute(move || evaluate(&calendar, &bars, &spy, &name))
            .await?;
        report["provenance"] = json!({"implementation_hash":identity,"dataset_id":dataset,"evaluation_kind":"historical_not_prospective","native_runtime":runtime_identity()});
        let mut tx = self.pool.begin().await?;
        // Linearize publication against a newly sealed research plan.
        sqlx::query("LOCK TABLE research_plans IN SHARE MODE")
            .execute(&mut *tx)
            .await?;
        let now_sealed: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM research_plans)")
            .fetch_one(&mut *tx)
            .await?;
        if now_sealed {
            return Err(invalid(
                "Original holdout became sealed; native result was not published",
            ));
        }
        sqlx::query("INSERT INTO evaluation_reports(id,model_id,symbol,dataset_id,status,protocol,report) VALUES($1,'technical-v1',$2,$3,$4,$5,$6) ON CONFLICT(model_id,dataset_id) DO NOTHING").bind(Uuid::new_v4()).bind(symbol).bind(&dataset).bind(report["status"].as_str()).bind(protocol()).bind(&report).execute(&mut *tx).await?;
        let (id,saved):(Uuid,Value)=sqlx::query_as("SELECT id,report FROM evaluation_reports WHERE model_id='technical-v1' AND dataset_id=$1").bind(&dataset).fetch_one(&mut *tx).await?;
        sqlx::query("INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES($1,$2,'fixed_protocol_evaluation')").bind(Uuid::new_v4()).bind(id).execute(&mut *tx).await?;
        tx.commit().await?;
        merged(json!({"id":id}), saved)
    }
}
fn merged(mut prefix: Value, report: Value) -> Result<Value> {
    let p = prefix
        .as_object_mut()
        .ok_or_else(|| invalid("Invalid evaluation response"))?;
    for (key, value) in report
        .as_object()
        .ok_or_else(|| invalid("Invalid frozen report"))?
    {
        p.insert(key.clone(), value.clone());
    }
    Ok(prefix)
}
fn runtime_identity() -> Value {
    let sources = [
        (
            "research_evaluation_store.rs",
            include_bytes!("research_evaluation_store.rs").as_slice(),
        ),
        (
            "research_evaluation.rs",
            include_bytes!("research_evaluation.rs").as_slice(),
        ),
        (
            "research_training.rs",
            include_bytes!("research_training.rs").as_slice(),
        ),
        ("statistics.rs", include_bytes!("statistics.rs").as_slice()),
        ("numpy_rng.rs", include_bytes!("numpy_rng.rs").as_slice()),
        ("monitoring.rs", include_bytes!("monitoring.rs").as_slice()),
        ("Cargo.lock", include_bytes!("../Cargo.lock").as_slice()),
    ];
    let hashes: BTreeMap<_, _> = sources
        .into_iter()
        .map(|(name, bytes)| (name, hex::encode(Sha256::digest(bytes))))
        .collect();
    json!({"format":1,"implementation":"rust","version":env!("CARGO_PKG_VERSION"),"source_sha256":hashes,"cache_identity_semantics":"Outer implementation_hash is the original protocol/cache identity; native hashes identify the code producing this new report","original_python_runtime_used":false})
}
