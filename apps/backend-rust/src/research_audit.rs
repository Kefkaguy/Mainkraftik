//! Reconstruct saved historical heads. Never fit, select or promote a model.
use crate::{
    Result,
    calendar::Calendar,
    canonical, financials, invalid, monitoring,
    research_evaluation::build_panel,
    research_training::{Heads, Limits, Trainer},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::{collections::BTreeMap, sync::Arc};
use uuid::Uuid;

pub struct Auditor {
    pool: PgPool,
    calendar: Arc<Calendar>,
    cpu: Arc<Trainer>,
}
impl Auditor {
    pub fn new(pool: PgPool, calendar: Arc<Calendar>) -> Self {
        Self {
            pool,
            calendar,
            cpu: Trainer::new(Limits::default()),
        }
    }
    pub async fn run(&self) -> Result<Value> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            .execute(&mut *tx)
            .await?;
        let total: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM evaluation_reports WHERE model_id='technical-v1'",
        )
        .fetch_one(&mut *tx)
        .await?;
        let originals:Vec<(Uuid,String,String,Value)>=sqlx::query_as("SELECT DISTINCT ON(symbol) id,symbol,dataset_id,report FROM evaluation_reports WHERE model_id='technical-v1' ORDER BY symbol,created_at DESC").fetch_all(&mut *tx).await?;
        let mut snapshots = Vec::new();
        for (id, symbol, dataset, report) in originals {
            let manifest: Value =
                sqlx::query_scalar("SELECT payload FROM dataset_manifests WHERE id=$1")
                    .bind(&dataset)
                    .fetch_one(&mut *tx)
                    .await?;
            let ids: Vec<i64> = serde_json::from_value(manifest["bar_ids"].clone())?;
            if ids.len() > 40_000 {
                return Err(invalid("Historical audit resource boundary"));
            }
            let bars:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r)||jsonb_build_object('open',r.open::text,'close',r.close::text) FROM (SELECT * FROM daily_bars WHERE id=ANY($1) ORDER BY symbol,session_date) r").bind(&ids).fetch_all(&mut *tx).await?;
            let expected: BTreeMap<_, _> = ids
                .iter()
                .zip(
                    manifest["hashes"]
                        .as_array()
                        .ok_or_else(|| invalid("Frozen audit dataset identity mismatch"))?,
                )
                .map(|(id, hash)| (*id, hash))
                .collect();
            if manifest["hashes"].as_array().map_or(0, Vec::len) != ids.len()
                || ids.len() != expected.len()
                || bars.len() != ids.len()
                || !bars.iter().all(|row| {
                    row["id"]
                        .as_i64()
                        .and_then(|id| expected.get(&id))
                        .is_some_and(|hash| **hash == row["content_hash"])
                })
            {
                return Err(invalid("Frozen audit dataset identity mismatch"));
            }
            snapshots.push(json!({"id":id,"symbol":symbol,"report":report,"bars":bars.iter().filter(|row|row["symbol"]==symbol).collect::<Vec<_>>(),"spy":bars.iter().filter(|row|row["symbol"]=="SPY").collect::<Vec<_>>() }));
        }
        tx.commit().await?;
        let calendar = self.calendar.clone();
        let mut report = self
            .cpu
            .compute(move || reconstruct(&calendar, total, &snapshots))
            .await?;
        // Floating reductions can differ from NumPy by roundoff. New native audits
        // have their own explicit producer and content ID; no Python ID is forged.
        report["native_runtime"] = producer();
        let id = canonical::digest(&report)?;
        sqlx::query("INSERT INTO research_audits(id,report) VALUES($1,$2) ON CONFLICT DO NOTHING")
            .bind(&id)
            .bind(&report)
            .execute(&self.pool)
            .await?;
        report["id"] = json!(id);
        Ok(report)
    }
}
pub fn reconstruct(calendar: &Calendar, total: i64, originals: &[Value]) -> Result<Value> {
    let mut summaries = Vec::new();
    for original in originals {
        let own = original["bars"]
            .as_array()
            .ok_or_else(|| invalid("Missing frozen audit bars"))?;
        let spy = original["spy"]
            .as_array()
            .ok_or_else(|| invalid("Missing frozen audit benchmark"))?;
        let panel = build_panel(calendar, own, spy)?;
        let horizons = match original["report"].get("horizons") {
            None => &[][..],
            Some(value) => value
                .as_array()
                .ok_or_else(|| invalid("Invalid saved audit horizons"))?
                .as_slice(),
        };
        for h in horizons {
            if !h.is_object() {
                return Err(invalid("Invalid saved audit head"));
            }
            if h["fitted_parameters"].is_null()
                || h["fitted_parameters"]
                    .as_object()
                    .is_some_and(|p| p.is_empty())
            {
                continue;
            }
            let horizon = h["horizon"]
                .as_u64()
                .filter(|h| matches!(h, 1 | 5))
                .ok_or_else(|| invalid("Invalid saved audit horizon"))?
                as usize;
            let train_end = financials::date(&h["partitions"]["train_end"])?;
            let test_start = financials::date(&h["partitions"]["evaluation_start"])?;
            let mut train = Vec::new();
            let mut test = Vec::new();
            for row in &panel {
                if row.index + horizon >= own.len() {
                    continue;
                }
                let y = financials::number(&own[row.index + horizon]["close"])?
                    / financials::number(&own[row.index + 1]["open"])?
                    - 1.;
                if row.date <= train_end {
                    train.push((row.x.clone(), y));
                }
                if row.date >= test_start {
                    test.push((row.x.clone(), y));
                }
            }
            let model: Heads = serde_json::from_value(h["fitted_parameters"].clone())?;
            let score = |rows: &[(Vec<f64>, f64)]| -> Result<Value> {
                let x: Vec<_> = rows.iter().map(|r| r.0.clone()).collect();
                let y: Vec<_> = rows.iter().map(|r| r.1).collect();
                let (expected, p) = model.predict(&x)?;
                monitoring::metrics(&y, &expected, &p, None)
            };
            let actual = score(&test)?;
            let expected = financials::number(&h["candidate"]["mae"])?;
            // Exact original numpy.isclose rtol, including its default absolute tolerance.
            if (financials::number(&actual["mae"])? - expected).abs()
                > 1e-8 + 1e-10 * expected.abs()
            {
                return Err(invalid(
                    "Original prediction reconstruction does not reconcile",
                ));
            }
            summaries.push(json!({"report_id":original["id"],"symbol":original["symbol"],"horizon":h["horizon"],"original_status":h["status"],"test":actual,"train":score(&train)?,"baseline":h["baseline"],"comparisons":h["comparisons"],"interval_coverage":h["interval_coverage"],"evaluation_dates":h["evaluation_dates"],"non_overlapping_horizons":h["non_overlapping_horizon_count"],"blocks":h["approximate_20_session_blocks"],"partitions":h["partitions"],"diagnosis":"Point estimates worsen on MSI; AXON improvements are small relative to dependence-aware uncertainty. No corrected interval establishes improvement."}));
        }
    }
    Ok(
        json!({"original_reports_preserved":total,"reconstructed_latest_heads":summaries,"future_input_leakage":"No future open in inputs; training-only scaling and contained purged labels verified in code/tests","methodological_limits":["Inspected holdout is exposed, even if code hashes create new dataset identities","Original logistic probabilities were not calibrated on calibration data","Fixed residual intervals undercovered in observed evaluation","Mean baseline is the declared reference; median is an additional MAE diagnostic","Four-comparison intervals do not correct lifetime selection across stocks or candidates","Historical bars reconstruct current split-adjusted history, not a point-in-time archive"],"do_not_repeat":"No hyperparameter tuning or new candidate fitting on the original evaluation period"}),
    )
}
fn producer() -> Value {
    let sources = [
        (
            "research_audit.rs",
            include_bytes!("research_audit.rs").as_slice(),
        ),
        (
            "research_evaluation.rs",
            include_bytes!("research_evaluation.rs").as_slice(),
        ),
        (
            "research_training.rs",
            include_bytes!("research_training.rs").as_slice(),
        ),
        ("monitoring.rs", include_bytes!("monitoring.rs").as_slice()),
        ("models.rs", include_bytes!("models.rs").as_slice()),
        ("statistics.rs", include_bytes!("statistics.rs").as_slice()),
        ("Cargo.lock", include_bytes!("../Cargo.lock").as_slice()),
    ];
    let hashes: BTreeMap<_, _> = sources
        .into_iter()
        .map(|(name, bytes)| (name, hex::encode(Sha256::digest(bytes))))
        .collect();
    json!({"format":1,"implementation":"rust","version":env!("CARGO_PKG_VERSION"),"source_sha256":hashes,"new_model_fitting":false,"original_python_runtime_used":false,"identity_semantics":"New native reconstruction has its own producer and content ID; existing Python audits are preserved"})
}
