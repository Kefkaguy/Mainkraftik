//! Native frozen tokenizer/ONNX sentiment inference. No model downloads or broker access.
use crate::{
    Result,
    api::{ApiError, ApiResult},
    invalid, sentiment,
};
use async_trait::async_trait;
use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post},
};
use ort::{
    session::{Session, SessionInputValue},
    value::Tensor,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tokenizers::{PaddingDirection, Tokenizer, TruncationParams};
use tokio::sync::Semaphore;

static RUNTIME: Mutex<Option<PathBuf>> = Mutex::new(None);
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Item {
    pub id: String,
    pub text: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Batch {
    pub items: Vec<Item>,
}
impl Batch {
    pub fn validate(&self) -> Result<()> {
        if !(1..=120).contains(&self.items.len()) {
            return Err(invalid("Invalid sentiment batch size"));
        }
        let mut seen = BTreeSet::new();
        for item in &self.items {
            if !(1..=100).contains(&item.id.chars().count())
                || !(1..=1500).contains(&item.text.chars().count())
            {
                return Err(invalid("Invalid sentiment item bounds"));
            }
            if !seen.insert(&item.id) {
                return Err(invalid("Duplicate article IDs"));
            }
        }
        Ok(())
    }
}
fn file_hash(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(hex::encode(hash.finalize()))
}
pub fn verify_artifacts(root: &Path) -> Result<Value> {
    let root = root.canonicalize()?;
    let manifest: Value = serde_json::from_slice(&std::fs::read(root.join("manifest.json"))?)?;
    if manifest["revision"] != sentiment::REVISION
        || manifest["max_tokens"] != 128
        || manifest["sha256"]["onnx/model_quantized.onnx"] != sentiment::WEIGHT_HASH
        || manifest["sha256"]["config.json"]
            != "001d0eb404b3b6bd4b51a625b425b4b80b1ba70ce1ce49a50b37278ac3a1127b"
        || manifest["sha256"]["tokenizer.json"]
            != "1f325de61b2d8f48f2580c234ef57f927ffb952ea8617a9820559ec6b7b687e5"
    {
        return Err(invalid("Unrecognized sentiment artifact manifest"));
    }
    let hashes = manifest["sha256"]
        .as_object()
        .ok_or_else(|| invalid("Invalid sentiment manifest hashes"))?;
    for (name, expected) in hashes {
        let path = root.join(name).canonicalize()?;
        if !matches!(path.parent(),Some(parent) if parent==root || parent==root.join("onnx")) {
            return Err(invalid("Unexpected artifact path"));
        }
        if Some(file_hash(&path)?.as_str()) != expected.as_str() {
            return Err(invalid("Model artifact hash mismatch"));
        }
    }
    Ok(manifest)
}

#[derive(Debug, Serialize)]
pub struct Encoding {
    pub input_ids: Vec<Vec<i64>>,
    pub attention_mask: Vec<Vec<i64>>,
    pub token_type_ids: Vec<Vec<i64>>,
}
pub struct Inference {
    manifest: Value,
    tokenizer: Tokenizer,
    session: Session,
    labels: Vec<String>,
}
impl Inference {
    pub fn load(root: &Path, dylib: &Path) -> Result<Self> {
        let manifest = verify_artifacts(root)?;
        let dylib = dylib.canonicalize()?;
        if !dylib.is_file() {
            return Err(invalid("Missing ONNX runtime library"));
        }
        let mut loaded = RUNTIME
            .lock()
            .map_err(|_| invalid("ONNX runtime initialization poisoned"))?;
        if let Some(loaded) = loaded.as_ref() {
            if loaded != &dylib {
                return Err(invalid("Cannot change loaded ONNX runtime"));
            }
        } else {
            ort::init_from(&dylib)
                .map_err(|_| invalid("ONNX runtime load failed"))?
                .commit();
            *loaded = Some(dylib);
        }
        drop(loaded);
        let mut tokenizer = Tokenizer::from_file(root.join("tokenizer.json"))
            .map_err(|_| invalid("Frozen tokenizer unavailable"))?;
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: 128,
                ..Default::default()
            }))
            .map_err(|_| invalid("Tokenizer truncation failed"))?;
        // Encode serially and batch-pad explicitly to avoid an unbounded Rayon pool.
        tokenizer.with_padding(None);
        let config: Value = serde_json::from_slice(&std::fs::read(root.join("config.json"))?)?;
        let labels: Vec<_> = (0..3)
            .filter_map(|i| {
                config["id2label"][i.to_string()]
                    .as_str()
                    .map(str::to_owned)
            })
            .collect();
        if labels.iter().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from(["negative", "neutral", "positive"])
        {
            return Err(invalid("Unrecognized sentiment label mapping"));
        }
        let session = Session::builder()
            .map_err(|_| invalid("Frozen sentiment session unavailable"))?
            .with_intra_threads(1)
            .map_err(|_| invalid("Frozen sentiment session unavailable"))?
            .with_inter_threads(1)
            .map_err(|_| invalid("Frozen sentiment session unavailable"))?
            .with_parallel_execution(false)
            .map_err(|_| invalid("Frozen sentiment session unavailable"))?
            .commit_from_file(root.join("onnx/model_quantized.onnx"))
            .map_err(|_| invalid("Frozen sentiment session unavailable"))?;
        Ok(Self {
            manifest,
            tokenizer,
            session,
            labels,
        })
    }
    pub fn health(&self) -> Value {
        json!({"status":"ok","revision":self.manifest["revision"],"purpose":"text_sentiment_only"})
    }
    pub fn encode(&self, items: &[Item]) -> Result<Encoding> {
        if items.is_empty() || items.len() > 4 {
            return Err(invalid("Invalid inference chunk"));
        }
        let mut encoded = Vec::new();
        for item in items {
            encoded.push(
                self.tokenizer
                    .encode(item.text.as_str(), true)
                    .map_err(|_| invalid("Text tokenization failed"))?,
            );
        }
        let length = encoded
            .iter()
            .map(|e| e.len())
            .max()
            .ok_or_else(|| invalid("Missing tokenization"))?;
        for row in &mut encoded {
            row.pad(length, 1, 0, "<pad>", PaddingDirection::Right);
        }
        Ok(Encoding {
            input_ids: encoded
                .iter()
                .map(|e| e.get_ids().iter().map(|v| i64::from(*v)).collect())
                .collect(),
            attention_mask: encoded
                .iter()
                .map(|e| {
                    e.get_attention_mask()
                        .iter()
                        .map(|v| i64::from(*v))
                        .collect()
                })
                .collect(),
            token_type_ids: encoded
                .iter()
                .map(|e| e.get_type_ids().iter().map(|v| i64::from(*v)).collect())
                .collect(),
        })
    }
    pub fn logits(&mut self, items: &[Item]) -> Result<Vec<[f32; 3]>> {
        let encoded = self.encode(items)?;
        let width = encoded.input_ids[0].len();
        let mut inputs: Vec<(String, SessionInputValue<'static>)> = Vec::new();
        for input in self.session.inputs() {
            let values = match input.name() {
                "input_ids" => &encoded.input_ids,
                "attention_mask" => &encoded.attention_mask,
                "token_type_ids" => &encoded.token_type_ids,
                _ => return Err(invalid("Unrecognized model input")),
            };
            let tensor = Tensor::from_array((
                [items.len(), width],
                values.iter().flatten().copied().collect::<Vec<_>>(),
            ))
            .map_err(|_| invalid("Invalid sentiment tensor"))?;
            inputs.push((input.name().into(), tensor.into()));
        }
        let output = self
            .session
            .run(inputs)
            .map_err(|_| invalid("Sentiment inference failed"))?;
        let (shape, raw) = output[0]
            .try_extract_tensor::<f32>()
            .map_err(|_| invalid("Invalid model output type"))?;
        if shape.as_ref() != [items.len() as i64, 3] || raw.iter().any(|v| !v.is_finite()) {
            return Err(invalid("Invalid model output"));
        }
        Ok(raw.chunks_exact(3).map(|r| [r[0], r[1], r[2]]).collect())
    }
    pub fn score(&mut self, batch: &Batch) -> Result<Value> {
        batch.validate()?;
        let mut scores = Vec::new();
        for items in batch.items.chunks(4) {
            for (item, raw) in items.iter().zip(self.logits(items)?) {
                let max = raw.iter().copied().fold(f32::NEG_INFINITY, f32::max);
                let exp = raw.map(|v| (v - max).exp());
                let total = exp.iter().sum::<f32>();
                let row: serde_json::Map<String, Value> = self
                    .labels
                    .iter()
                    .zip(exp)
                    .map(|(label, v)| (label.clone(), json!(f64::from(v / total))))
                    .collect();
                scores.push(json!({"id":item.id,"text_hash":hex::encode(Sha256::digest(item.text.as_bytes())),"scores":row}));
            }
        }
        Ok(
            json!({"model":self.manifest,"scores":scores,"meaning":"Text sentiment; not return probability"}),
        )
    }
}

fn validation(body: &Value) -> ApiResult<Batch> {
    let mut errors = Vec::new();
    let error = |kind: &str, loc: Value, msg: &str, input: &Value| json!({"type":kind,"loc":loc,"msg":msg,"input":input});
    let model_error = |loc, input: &Value| {
        error(
            "model_attributes_type",
            loc,
            "Input should be a valid dictionary or object to extract fields from",
            input,
        )
    };
    if !body.is_object() {
        return Err(ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            detail: json!([model_error(json!(["body"]), body)]),
        });
    }
    let mut items = Vec::new();
    match body.get("items") {
        None => errors.push(error(
            "missing",
            json!(["body", "items"]),
            "Field required",
            body,
        )),
        Some(value) => match value.as_array() {
            None => errors.push(error(
                "list_type",
                json!(["body", "items"]),
                "Input should be a valid list",
                value,
            )),
            Some(rows) => {
                if rows.len() > 120 {
                    let mut e = error(
                        "too_long",
                        json!(["body", "items"]),
                        &format!(
                            "List should have at most 120 items after validation, not {}",
                            rows.len()
                        ),
                        value,
                    );
                    e["ctx"] =
                        json!({"field_type":"List","max_length":120,"actual_length":rows.len()});
                    errors.push(e);
                } else {
                    for (index, row) in rows.iter().enumerate() {
                        if !row.is_object() {
                            errors.push(model_error(json!(["body", "items", index]), row));
                            continue;
                        }
                        for (name, maximum) in [("id", 100), ("text", 1500)] {
                            let loc = json!(["body", "items", index, name]);
                            match row.get(name) {
                                None => errors.push(error("missing", loc, "Field required", row)),
                                Some(value) => match value.as_str() {
                                    None => errors.push(error(
                                        "string_type",
                                        loc,
                                        "Input should be a valid string",
                                        value,
                                    )),
                                    Some(text) => {
                                        let size = text.chars().count();
                                        if size == 0 {
                                            let mut e = error(
                                                "string_too_short",
                                                loc,
                                                "String should have at least 1 character",
                                                value,
                                            );
                                            e["ctx"] = json!({"min_length":1});
                                            errors.push(e);
                                        } else if size > maximum {
                                            let mut e = error(
                                                "string_too_long",
                                                loc,
                                                &format!(
                                                    "String should have at most {maximum} characters"
                                                ),
                                                value,
                                            );
                                            e["ctx"] = json!({"max_length":maximum});
                                            errors.push(e);
                                        }
                                    }
                                },
                            }
                        }
                        if let Some(row) = row.as_object() {
                            for (key, value) in row {
                                if !matches!(key.as_str(), "id" | "text") {
                                    errors.push(error(
                                        "extra_forbidden",
                                        json!(["body", "items", index, key]),
                                        "Extra inputs are not permitted",
                                        value,
                                    ));
                                }
                            }
                        }
                        if let (Some(id), Some(text)) = (row["id"].as_str(), row["text"].as_str()) {
                            items.push(Item {
                                id: id.into(),
                                text: text.into(),
                            });
                        }
                    }
                    if rows.is_empty() {
                        let mut e = error(
                            "too_short",
                            json!(["body", "items"]),
                            "List should have at least 1 item after validation, not 0",
                            value,
                        );
                        e["ctx"] = json!({"field_type":"List","min_length":1,"actual_length":0});
                        errors.push(e);
                    }
                }
            }
        },
    }
    if let Some(body) = body.as_object() {
        for (key, value) in body {
            if key != "items" {
                errors.push(error(
                    "extra_forbidden",
                    json!(["body", key]),
                    "Extra inputs are not permitted",
                    value,
                ));
            }
        }
    }
    if !errors.is_empty() {
        return Err(ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            detail: json!(errors),
        });
    }
    let batch = Batch { items };
    batch
        .validate()
        .map_err(|_| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, "Duplicate article IDs"))?;
    Ok(batch)
}

/// One CPU inference job at a time. The permit lives inside the blocking task,
/// so a disconnected HTTP caller cannot allow a second overlapping inference.
pub struct Worker {
    inference: Arc<Mutex<Inference>>,
    slots: Arc<Semaphore>,
    health: Value,
}
impl Worker {
    pub fn new(inference: Inference) -> Self {
        Self {
            health: inference.health(),
            inference: Arc::new(Mutex::new(inference)),
            slots: Arc::new(Semaphore::new(1)),
        }
    }
    pub async fn score(&self, batch: Batch) -> ApiResult<Value> {
        batch.validate().map_err(|_| {
            ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, "Invalid sentiment batch")
        })?;
        let permit = self.slots.clone().try_acquire_owned().map_err(|_| {
            ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "Single inference job already running",
            )
        })?;
        let inference = self.inference.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            inference
                .lock()
                .map_err(|_| invalid("Inference worker unavailable"))?
                .score(&batch)
        })
        .await
        .map_err(|_| {
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Sentiment inference unavailable",
            )
        })?
        .map_err(|_| {
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Sentiment inference unavailable",
            )
        })
    }
    pub fn router(self: Arc<Self>) -> Router {
        Router::new()
            .route("/health", get(worker_health))
            .route("/score", post(worker_score))
            .with_state(self)
    }
}
async fn worker_health(State(worker): State<Arc<Worker>>) -> Json<Value> {
    Json(worker.health.clone())
}
async fn worker_score(
    State(worker): State<Arc<Worker>>,
    body: std::result::Result<Json<Value>, JsonRejection>,
) -> ApiResult<Json<Value>> {
    let Json(body) =
        body.map_err(|_| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, "Invalid JSON request"))?;
    Ok(Json(worker.score(validation(&body)?).await?))
}
#[async_trait]
impl sentiment::Transport for Worker {
    async fn score(&self, items: &[Value]) -> Result<Value> {
        let batch =
            validation(&json!({"items":items})).map_err(|_| invalid("Invalid sentiment batch"))?;
        Worker::score(self, batch)
            .await
            .map_err(|_| invalid("Local financial sentiment unavailable; daily selection skipped"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frozen_manifest_identity_and_external_paths_are_enforced() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/sentiment-inference-parity.json"
        ))
        .expect("fixture");
        let original = fixture["cases"][0]["expected"]["model"].clone();
        let root = tempfile::tempdir().expect("temporary model root");
        let mut tampered = original.clone();
        tampered["sha256"]["tokenizer.json"] = json!("0".repeat(64));
        std::fs::write(
            root.path().join("manifest.json"),
            serde_json::to_vec(&tampered).expect("JSON"),
        )
        .expect("manifest");
        assert!(
            verify_artifacts(root.path())
                .expect_err("changed tokenizer identity")
                .to_string()
                .contains("Unrecognized sentiment artifact manifest")
        );
        let outside = tempfile::NamedTempFile::new().expect("outside artifact");
        let mut hashes = serde_json::Map::new();
        hashes.insert(
            outside.path().to_str().expect("path").into(),
            json!("0".repeat(64)),
        );
        for (key, value) in original["sha256"].as_object().expect("hashes") {
            hashes.insert(key.clone(), value.clone());
        }
        let mut escaped = original;
        escaped["sha256"] = Value::Object(hashes);
        std::fs::write(
            root.path().join("manifest.json"),
            serde_json::to_vec(&escaped).expect("JSON"),
        )
        .expect("manifest");
        assert!(
            verify_artifacts(root.path())
                .expect_err("external artifact path")
                .to_string()
                .contains("Unexpected artifact path")
        );
    }
    #[test]
    fn python_http_validation_contract() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/sentiment-http-validation.json"
        ))
        .expect("fixture");
        for (index, case) in fixture["cases"]
            .as_array()
            .expect("cases")
            .iter()
            .enumerate()
        {
            let error = validation(&case["input"]).expect_err("invalid body");
            assert_eq!(
                error.status.as_u16(),
                case["status"].as_u64().expect("status") as u16,
                "case {index}"
            );
            assert_eq!(json!({"detail":error.detail}), case["body"], "case {index}");
        }
    }

    #[tokio::test]
    #[ignore = "requires scripts/test-rust-sentiment.py pinned artifacts/runtime"]
    async fn native_http_contract_and_cancellation_keep_single_inference_job() {
        use axum::{body::Body, http::Request};
        use http_body_util::BodyExt;
        use tower::ServiceExt;
        let root = PathBuf::from(std::env::var("RUST_SENTIMENT_MODEL_DIR").expect("model root"));
        let dylib =
            PathBuf::from(std::env::var("RUST_SENTIMENT_LIBRARY").expect("runtime library"));
        let worker = Arc::new(Worker::new(
            Inference::load(&root, &dylib).expect("inference"),
        ));
        let router = worker.clone().router();
        let health = router
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .expect("health request"),
            )
            .await
            .expect("health");
        assert_eq!(health.status(), StatusCode::OK);
        assert_eq!(
            serde_json::from_slice::<Value>(
                &health
                    .into_body()
                    .collect()
                    .await
                    .expect("health body")
                    .to_bytes()
            )
            .expect("JSON"),
            worker.health
        );
        let fixture: Value = if let Ok(path) = std::env::var("RUST_SENTIMENT_HTTP_REFERENCE") {
            serde_json::from_slice(&std::fs::read(path).expect("original HTTP reference"))
                .expect("fixture")
        } else {
            serde_json::from_str(include_str!(
                "../tests/fixtures/sentiment-http-validation.json"
            ))
            .expect("fixture")
        };
        let request = |body: &Value| {
            Request::builder()
                .method("POST")
                .uri("/score")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::to_vec(body).expect("body")))
                .expect("request")
        };
        for case in fixture["cases"].as_array().expect("cases") {
            let response = router
                .clone()
                .oneshot(request(&case["input"]))
                .await
                .expect("response");
            assert_eq!(u64::from(response.status().as_u16()), case["status"]);
            let bytes = response
                .into_body()
                .collect()
                .await
                .expect("body")
                .to_bytes();
            assert_eq!(
                serde_json::from_slice::<Value>(&bytes).expect("JSON"),
                case["body"]
            );
        }
        let body = json!({"items":[{"id":"one","text":"Company earnings increased."}]});
        let permit = worker.slots.clone().acquire_owned().await.expect("permit");
        let busy = router
            .clone()
            .oneshot(request(&body))
            .await
            .expect("busy response");
        assert_eq!(u64::from(busy.status().as_u16()), fixture["busy"]["status"]);
        assert_eq!(
            serde_json::from_slice::<Value>(
                &busy.into_body().collect().await.expect("body").to_bytes()
            )
            .expect("JSON"),
            fixture["busy"]["body"]
        );
        drop(permit);
        let response = router.oneshot(request(&body)).await.expect("score");
        assert_eq!(response.status(), StatusCode::OK);
        let value: Value = serde_json::from_slice(
            &response
                .into_body()
                .collect()
                .await
                .expect("body")
                .to_bytes(),
        )
        .expect("JSON");
        assert_eq!(value["scores"][0]["id"], "one");
        // Hold the inference mutex on a helper thread so cancellation occurs
        // after reservation but before compute, independently of CPU timing.
        let inference = worker.inference.clone();
        let (ready_sender, ready_receiver) = tokio::sync::oneshot::channel();
        let (release_sender, release_receiver) = std::sync::mpsc::channel();
        let holder = std::thread::spawn(move || {
            let _lock = inference.lock().expect("inference lock");
            ready_sender.send(()).expect("ready");
            release_receiver.recv().expect("release");
        });
        ready_receiver.await.expect("ready");
        let caller = worker.clone();
        let task = tokio::spawn(async move {
            caller
                .score(Batch {
                    items: vec![Item {
                        id: "cancelled".into(),
                        text: "Earnings increased".into(),
                    }],
                })
                .await
        });
        tokio::time::timeout(std::time::Duration::from_secs(1), async {
            while worker.slots.available_permits() != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("reserved");
        task.abort();
        match task.await {
            Err(error) => assert!(error.is_cancelled()),
            Ok(_) => panic!("caller must be cancelled"),
        }
        assert_eq!(worker.slots.available_permits(), 0);
        let denied = worker
            .score(Batch {
                items: vec![Item {
                    id: "second".into(),
                    text: "news".into(),
                }],
            })
            .await
            .expect_err("no overlapping inference");
        assert_eq!(denied.status, StatusCode::TOO_MANY_REQUESTS);
        release_sender.send(()).expect("release");
        holder.join().expect("thread");
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            while worker.slots.available_permits() != 1 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("compute finished and permit returned");
        println!("SENTIMENT_HTTP_VALIDATION_HEALTH_SCORE_AND_BUSY_PARITY=PASS");
        println!("CLIENT_CANCELLATION_CANNOT_OVERLAP_INFERENCE=PASS");
    }
}
