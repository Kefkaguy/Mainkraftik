"""Pin and audit a CPU news model independently of numerical trading probabilities."""

import hashlib
import math

import httpx

from intelligence.util import now
from paperpilot.config import settings
from paperpilot.data import store

REVISION = "e6953e30167c7fc3d1ad62f3df31617dd712fbc1"
WEIGHT_HASH = "8fcf4873bf5e1679c77205ce621acecdd2cd8dd5baf18e930f10428008ed48ec"


def assess(candidates, articles, source_id, client=None):
    references = {
        s: [ref for ref, a in articles.items() if s in (a.get("symbols") or [])][:3] for s in candidates
    }
    ids = list(dict.fromkeys(ref for refs in references.values() for ref in refs))
    items = [
        {
            "id": ref,
            "text": ((articles[ref].get("headline") or "") + ". " + (articles[ref].get("summary") or ""))[
                :1500
            ],
        }
        for ref in ids
    ]
    if not items:
        raise ValueError("No source text for local financial sentiment")
    own = client is None
    client = client or httpx.Client(timeout=90, follow_redirects=False)
    try:
        response = client.post(settings.paper_sentiment_url + "/score", json={"items": items})
        if response.status_code != 200:
            raise ValueError("Local financial sentiment unavailable; daily selection skipped")
        data = response.json()
    finally:
        if own:
            client.close()
    model = data["model"]
    if (
        model.get("revision") != REVISION
        or model.get("sha256", {}).get("onnx/model_quantized.onnx") != WEIGHT_HASH
    ):
        raise ValueError("Unapproved local model revision/hash")
    scores = data["scores"]
    if len(scores) != len(items) or {r["id"] for r in scores} != set(ids):
        raise ValueError("Incomplete or duplicated sentiment output")
    by_id = {r["id"]: r for r in scores}
    for item in items:
        r = by_id[item["id"]]
        p = r["scores"]
        if (
            r.get("text_hash") != hashlib.sha256(item["text"].encode()).hexdigest()
            or set(p) != {"negative", "neutral", "positive"}
            or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in p.values())
            or abs(sum(p.values()) - 1) > 1e-4
        ):
            raise ValueError("Invalid sentiment scores or input hash")
    per_symbol = {}
    for symbol, refs in references.items():
        if not refs:
            raise ValueError("Candidate lacks cited news")
        mean = {
            k: sum(by_id[r]["scores"][k] for r in refs) / len(refs)
            for k in ("positive", "negative", "neutral")
        }
        avoid = mean["negative"] >= 0.60 and mean["negative"] - mean["positive"] >= 0.20
        per_symbol[symbol] = {
            "source_ids": refs,
            "mean_text_sentiment": mean,
            "assessment": "avoid" if avoid else "review",
            "multiple_symbol_articles": sum(len(articles[r].get("symbols") or []) > 1 for r in refs),
            "not_return_probability": True,
        }
    evidence = {
        "version": "hf-financial-sentiment-q8-v1",
        "model": model,
        "news_source_id": source_id,
        "acquired_at": now(),
        "articles": scores,
        "per_symbol": per_symbol,
        "policy": "Research context and negative-news veto only; no probability uplift",
    }
    identity = store("*", "hf_sentiment", evidence)
    return {"source_id": identity, **evidence}
