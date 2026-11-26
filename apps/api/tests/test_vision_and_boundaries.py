import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_calendar_and_predictions import make_bars

from intelligence.vision import model_status, render_chart


def test_license_and_runtime_gates():
    model = {"license_cleared": False, "runtime_verified": False}
    assert model_status(model)["message"] == "Model unavailable — licensing review required"
    model.update(license_cleared=True, license_review_reference="review-1", weight_sha256="a" * 64)
    assert model_status(model)["status"] == "blocked_runtime"
    model["runtime_verified"] = True
    assert model_status(model)["available"]


def test_render_is_reproducible_and_cutoff_safe():
    bars = make_bars()[:110]
    cutoff = bars[99]["acquired_at"] + timedelta(minutes=1)
    first = render_chart(bars, cutoff)
    for row in bars[100:]:
        row["close"] *= 100
    second = render_chart(bars, cutoff)
    assert first == second
    assert first[0].startswith(b"\x89PNG")
    assert len(first[1]["bar_ids"]) == 90


def test_render_refuses_missing_history():
    with pytest.raises(ValueError):
        render_chart(make_bars()[:89], datetime.now(UTC))


def test_no_execution_imports_or_endpoint_configuration():
    root = Path(__file__).resolve().parents[1] / "intelligence"
    forbidden = {"alpaca_trade_api", "alpaca.trading", "app.brokers", "app.services.external_execution"}
    for file in root.glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in forbidden
            if isinstance(node, ast.Import):
                assert all(n.name not in forbidden for n in node.names)
        text = file.read_text(encoding="utf-8")
        assert "paper-api.alpaca.markets" not in text
        assert "api.alpaca.markets/v2/orders" not in text
