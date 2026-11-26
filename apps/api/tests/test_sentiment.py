import hashlib
import json

import httpx
import pytest
from test_paperpilot import risk_inputs

from paperpilot import sentiment
from paperpilot.config import settings
from paperpilot.risk import entry_permission


def response_body(items):
    return {
        "model": {
            "revision": sentiment.REVISION,
            "sha256": {"onnx/model_quantized.onnx": sentiment.WEIGHT_HASH},
        },
        "scores": [
            {
                "id": item["id"],
                "text_hash": hashlib.sha256(item["text"].encode()).hexdigest(),
                "scores": {"negative": 0.9, "neutral": 0.08, "positive": 0.02},
            }
            for item in items
        ],
    }


def test_sentiment_preserves_source_identity_and_cannot_assign_win_probability(monkeypatch):
    stored = []
    monkeypatch.setattr(sentiment, "store", lambda *args: stored.append(args) or "sentiment-evidence")
    articles = {"n1": {"headline": "Synthetic negative-news test fixture", "symbols": ["NVDA", "AMD"]}}
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=response_body(json.loads(r.content)["items"]))
        )
    ) as client:
        result = sentiment.assess({"NVDA": {}, "AMD": {}}, articles, "original-news", client)
    assert result["source_id"] == "sentiment-evidence"
    assert result["news_source_id"] == "original-news"
    assert len(result["articles"]) == 1  # shared article scored once
    assert result["per_symbol"]["NVDA"]["assessment"] == "avoid"
    assert result["per_symbol"]["NVDA"]["multiple_symbol_articles"] == 1
    assert result["per_symbol"]["AMD"]["not_return_probability"] is True
    assert stored[0][1] == "hf_sentiment"


@pytest.mark.parametrize("corruption", ["revision", "hash", "text", "nan", "missing", "duplicate"])
def test_malformed_or_unpinned_sentiment_never_reaches_research(monkeypatch, corruption):
    def handler(request):
        data = response_body(json.loads(request.content)["items"])
        if corruption == "revision":
            data["model"]["revision"] = "unapproved"
        if corruption == "hash":
            data["model"]["sha256"]["onnx/model_quantized.onnx"] = "unapproved"
        if corruption == "text":
            data["scores"][0]["text_hash"] = "different-input"
        if corruption == "nan":
            data["scores"][0]["scores"]["positive"] = 2.0
        if corruption == "missing":
            data["scores"] = []
        if corruption == "duplicate":
            data["scores"] *= 2
        return httpx.Response(200, json=data)

    monkeypatch.setattr(sentiment, "store", lambda *args: pytest.fail("Invalid evidence was stored"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        sentiment.assess({"NVDA": {}}, {"n1": {"headline": "Fixture", "symbols": ["NVDA"]}}, "news", client)


def test_local_model_is_only_a_veto_and_cannot_lower_numerical_threshold(monkeypatch):
    from paperpilot import risk

    monkeypatch.setattr(settings, "paper_discovery_enabled", True)
    monkeypatch.setattr(settings, "paper_sentiment_enabled", True)
    monkeypatch.setattr(risk, "current_symbols", lambda *args: ("AMD",))
    args = risk_inputs()
    args["signal"].update(news_stance="bullish", hf_news_assessment="review", probability_positive=0.54)
    assert not entry_permission(**args)[0]
    args["signal"]["probability_positive"] = 0.70
    assert entry_permission(**args)[0]
    for value in ("avoid", None):
        args["signal"]["hf_news_assessment"] = value
        assert not entry_permission(**args)[0]
