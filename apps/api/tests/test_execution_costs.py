import pytest
from test_paperpilot import risk_inputs

from paperpilot.config import settings
from paperpilot.risk import entry_permission, execution_cost_evidence


def test_high_direction_probability_does_not_cover_negative_net_edge(monkeypatch):
    monkeypatch.setattr(settings, "paper_cost_gate_enabled", True)
    args = risk_inputs()
    args["signal"]["probability_positive"] = 0.99
    assert not entry_permission(**args)[0]  # 20bp gross -10bp spread -10bp allowance
    args["signal"]["expected_return"] = 0.004
    assert entry_permission(**args)[0]
    evidence = execution_cost_evidence(args["signal"], args["quote"])
    assert evidence["expected_net_return"] == pytest.approx(0.002)
    assert evidence["passes"] is True


@pytest.mark.parametrize("field", ["expected_return", "probability_positive", "volatility"])
def test_nonfinite_forecasts_cannot_authorize_entry(field):
    args = risk_inputs()
    args["signal"][field] = float("nan")
    assert not entry_permission(**args)[0]


def test_wider_spread_can_erase_a_previous_cost_margin(monkeypatch):
    monkeypatch.setattr(settings, "paper_cost_gate_enabled", True)
    args = risk_inputs()
    args["signal"]["expected_return"] = 0.004
    assert entry_permission(**args)[0]
    args["quote"]["ask"] = 100.4
    assert not entry_permission(**args)[0]
