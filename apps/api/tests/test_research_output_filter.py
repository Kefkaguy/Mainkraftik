import json
from datetime import UTC, datetime

import httpx
import pytest

from paperpilot import discovery
from paperpilot.config import settings

CANDIDATES = {s: {"price": 100, "dollar_volume_20": 50_000_000} for s in ("AMD", "NVDA")}
ARTICLES = {
    "a": {"symbols": ["AMD"], "headline": "AMD synthetic fixture"},
    "n": {"symbols": ["NVDA"], "headline": "NVDA synthetic fixture"},
}
REFS = {"AMD": ["a"], "NVDA": ["n"]}


def pick(symbol="AMD", ref="a", stance="bullish"):
    return {"symbol": symbol, "source_ids": [ref], "stance": stance, "rationale": "Synthetic source fixture."}


def test_unknown_and_bad_citations_do_not_discard_unrelated_valid_picks():
    accepted, rejected = discovery.filter_research_output(
        {"picks": [pick("UNKNOWN"), pick("AMD", "n"), pick("NVDA", "n")]}, CANDIDATES, ARTICLES, REFS
    )
    assert [p["symbol"] for p in accepted] == ["NVDA"]
    assert [p["reason"] for p in rejected] == ["unknown_symbol", "citation_not_presented_for_symbol"]


def test_conflicting_duplicates_discard_every_occurrence_of_that_symbol():
    accepted, rejected = discovery.filter_research_output(
        {"picks": [pick(), pick(stance="bearish"), pick("NVDA", "n")]}, CANDIDATES, ARTICLES, REFS
    )
    assert [p["symbol"] for p in accepted] == ["NVDA"]
    assert len(rejected) == 2
    assert all(p["reason"] == "duplicate_symbol_all_occurrences_discarded" for p in rejected)


def test_all_invalid_output_fails_but_explicit_empty_shortlist_is_valid():
    with pytest.raises(discovery.ResearchOutputRejected, match="no valid independent") as error:
        discovery.filter_research_output({"picks": [pick("UNKNOWN")]}, CANDIDATES, ARTICLES, REFS)
    assert error.value.rejections[0]["reason"] == "unknown_symbol"
    assert discovery.filter_research_output({"picks": []}, CANDIDATES, ARTICLES, REFS) == ([], [])


def test_cap_and_strict_schema_are_not_relaxed_to_salvage_output():
    with pytest.raises(ValueError, match="20-stock"):
        discovery.filter_research_output({"picks": [pick()] * 21}, CANDIDATES, ARTICLES, REFS)
    with pytest.raises(ValueError, match="strict schema"):
        discovery.filter_research_output(
            {"picks": [{**pick(), "confidence": 0.99}]}, CANDIDATES, ARTICLES, REFS
        )


def run_rank(monkeypatch, output, records, captured):
    monkeypatch.setattr(settings, "groq_key", "not-for-prompt-or-audit")
    monkeypatch.setattr(discovery, "reserve_call", lambda *args: None)
    monkeypatch.setattr(discovery, "event", lambda *args: records.append(args))

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-filter-test",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "unsafe_extra": "not-for-prompt-or-audit",
                },
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return discovery.rank(CANDIDATES, ARTICLES, datetime.now(UTC).date(), client)


def test_dynamic_enum_uses_only_presented_candidates_and_never_mutates_model_schema(monkeypatch):
    original = discovery.Ranking.model_json_schema()
    records, captured = [], []
    accepted, meta = run_rank(monkeypatch, {"picks": [pick("UNKNOWN"), pick("NVDA", "n")]}, records, captured)
    enum = captured[0]["response_format"]["json_schema"]["schema"]["$defs"]["Pick"]["properties"]["symbol"][
        "enum"
    ]
    assert enum == ["AMD", "NVDA"]
    assert discovery.Ranking.model_json_schema() == original
    assert [p["symbol"] for p in accepted] == meta["accepted_symbols"] == ["NVDA"]
    assert meta["output_rejections"][0]["reason"] == "unknown_symbol"
    assert records[0][0] == "research_model_output_filtered"
    assert "not-for-prompt-or-audit" not in json.dumps(records)
    assert "not-for-prompt-or-audit" not in json.dumps(meta)


def test_rejected_model_output_is_audited_before_all_invalid_failure(monkeypatch):
    records, captured = [], []
    with pytest.raises(discovery.ResearchOutputRejected):
        run_rank(monkeypatch, {"picks": [pick("UNKNOWN")]}, records, captured)
    event, evidence = records[0]
    assert event == "research_model_validation_rejected"
    assert evidence["accepted_count"] == 0
    assert evidence["response_id"] == "chatcmpl-filter-test"
    assert evidence["usage"]["total_tokens"] == 30
    assert len(evidence["request_hash"]) == 64
    assert "UNKNOWN" not in json.dumps(evidence)
    assert "not-for-prompt-or-audit" not in json.dumps(evidence)
