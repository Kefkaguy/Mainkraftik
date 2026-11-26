import json
from datetime import UTC, datetime

import httpx
import pytest

from paperpilot import discovery
from paperpilot.config import settings


def inputs(size=40):
    candidates = {}
    articles = {}
    sentiment = {"per_symbol": {}}
    for i in range(size):
        symbol = "S" + chr(65 + i // 26) + chr(65 + i % 26)
        candidates[symbol] = {"price": 100.123, "dollar_volume_20": 50_000_000}
        sentiment["per_symbol"][symbol] = {
            "mean_text_sentiment": {"negative": 0.1111111, "neutral": 0.3333333, "positive": 0.5555556},
            "assessment": "review",
            "multiple_symbol_articles": 0,
            "source_ids": ["not-prompt-input"],
            "private_unused_value": "must-not-be-forwarded",
        }
        for j in range(3):
            articles[f"{i:02}-{j}"] = {
                "symbols": [symbol],
                "headline": f"{symbol} source {j}: " + "Profit guidance revenue " * 35,
                "summary": "Reported source detail " * 40,
            }
    return candidates, articles, sentiment


def test_all_forty_candidates_and_three_sources_survive_the_prompt_budget():
    candidates, articles, sentiment = inputs()
    content, audit = discovery.research_context(candidates, articles, sentiment)
    doc = json.loads(content)
    assert len(content) <= 12000
    assert [s["s"] for s in doc["stocks"]] == list(candidates)
    assert all(len(s["refs"]) == 3 for s in doc["stocks"])
    assert len(doc["news"]) == 120
    assert audit["candidate_count_omitted"] == 0
    assert audit["headlines_truncated"] == 120
    assert all(n["h"].endswith("...") for n in doc["news"])
    assert audit["presented_article_ids"] == list(articles)
    assert audit["headline_character_limit"] >= 8
    assert "must-not-be-forwarded" not in content
    assert "not-prompt-input" not in content
    assert all(s["tone"] == [0.111, 0.333, 0.556] for s in doc["stocks"])


def test_shared_news_is_presented_once_and_every_citation_remains_attached():
    candidates, _, _ = inputs(2)
    symbols = list(candidates)
    articles = {"shared": {"symbols": symbols, "headline": "A source mentioning both companies"}}
    content, audit = discovery.research_context(candidates, articles)
    doc = json.loads(content)
    assert len(doc["news"]) == 1
    assert all(s["refs"] == ["shared"] for s in doc["stocks"])
    assert audit["article_ids_by_symbol"] == {s: ["shared"] for s in symbols}


def test_unicode_and_escaped_input_is_bounded_without_omitting_a_stock():
    candidates, articles, sentiment = inputs()
    for value in articles.values():
        value["headline"] = '收购"\\\n' * 200
        value["summary"] = "利益" * 500
    content, audit = discovery.research_context(candidates, articles, sentiment)
    assert len(content) <= 12000
    assert len(json.loads(content)["stocks"]) == 40
    assert len(audit["presented_symbols"]) == 40
    assert audit["candidate_count_omitted"] == 0


def test_input_overflow_fails_instead_of_silently_deleting_candidates():
    with pytest.raises(ValueError, match="one to forty"):
        discovery.research_context(*inputs(41)[:2])
    with pytest.raises(ValueError, match="source references"):
        discovery.research_context({"NVDA": {"price": 100, "dollar_volume_20": 1}}, {})


def response(pick):
    return httpx.Response(
        200,
        json={
            "id": "mock",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"picks": [pick]})}}],
        },
    )


def test_ranking_can_choose_last_candidate_and_records_exact_presented_context(monkeypatch):
    candidates, articles, sentiment = inputs()
    last = list(candidates)[-1]
    monkeypatch.setattr(settings, "groq_key", "test-key-not-for-prompt")
    reservations = []
    monkeypatch.setattr(discovery, "reserve_call", lambda *args: reservations.append(args))

    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        assert len(context["stocks"]) == 40
        assert len(body["messages"][1]["content"]) <= 12000
        assert "tools" not in body
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["properties"]["picks"]["maxItems"] == 20
        assert "test-key-not-for-prompt" not in body["messages"][1]["content"]
        return response(
            {"symbol": last, "source_ids": ["39-2"], "stance": "mixed", "rationale": "Visible cited source."}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        picks, metadata = discovery.rank(candidates, articles, datetime.now(UTC).date(), client, sentiment)
    assert picks[0]["symbol"] == last
    assert metadata["context"]["presented_symbols"] == list(candidates)
    assert metadata["context"]["version"] == "balanced-news-context-v4.2"
    assert len(reservations) == 1


def test_ranking_rejects_real_but_unpresented_stock_citations(monkeypatch):
    candidates, _, _ = inputs(2)
    first, second = list(candidates)
    articles = {f"a{i}": {"symbols": [first], "headline": "First stock source"} for i in range(3)}
    articles["shared-later"] = {"symbols": [first, second], "headline": "A later shared source"}
    monkeypatch.setattr(settings, "groq_key", "test")
    monkeypatch.setattr(discovery, "reserve_call", lambda *args: None)
    monkeypatch.setattr(discovery, "event", lambda *args: None)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: response(
                {
                    "symbol": first,
                    "source_ids": ["shared-later"],
                    "stance": "bullish",
                    "rationale": "Not presented as first stock evidence.",
                }
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="no valid independent"):
            discovery.rank(candidates, articles, datetime.now(UTC).date(), client)
