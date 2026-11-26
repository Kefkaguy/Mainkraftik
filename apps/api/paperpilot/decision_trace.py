"""Observational gate evidence. Never grants execution permission."""

from dataclasses import dataclass, field

VERSION = "decision-trace-v1"


@dataclass
class DecisionTrace:
    context: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)

    def expect(self, keys):
        for key in keys:
            self.gates.setdefault(key, {"status": "NOT_EVALUATED", "reason": "Not reached"})
        return self

    def blocked(self, key, condition, reason):
        self.gates[key] = {"status": "FAILED" if condition else "PASSED", "reason": reason}
        return condition

    def failure(self, key, reason):
        self.blocked(key, True, reason)

    def payload(self):
        return {"version": VERSION, "context": self.context, "gates": self.gates}


def signal_trace(strategy, result, ready, role, reasons, action):
    from paperpilot.strategy import PROBABILITY_BLOCK

    trace = DecisionTrace(
        {
            "stage": "signal",
            "role": role,
            "strategy": strategy,
            "action": action,
            "policy": result.get("protocol"),
            "bundle_id": result.get("bundle_id"),
            "source_ids": result.get("source_ids", []),
        }
    )
    checks = {
        "probability": (PROBABILITY_BLOCK, "Invalid model probability"),
        "expected_net_return": ("Expected net return below minimum margin",),
        "volatility": ("Volatility exceeds pilot limit",),
        "news": ("Sourced news assessment is not bullish",),
        "sentiment": ("News sentiment veto or unavailable",),
    }
    trace.expect(["inputs", *checks, "readiness", "execution_window", "entry_risk", "order_intent", "broker"])
    usable = "expected_net_return" in result
    trace.blocked(
        "inputs", not usable, "; ".join(reasons) if not usable else "Cutoff-safe inputs and score available"
    )
    if usable:
        # Record the standard probability gate even when an approved trial
        # explicitly bypasses it; retain the separate policy authorization.
        from paperpilot.strategy import entry_reasons

        actual = entry_reasons(strategy, result, result.get("news_stance"), result.get("sentiment"))
        for key, messages in checks.items():
            failed = [r for r in actual if r in messages]
            trace.blocked(key, bool(failed), "; ".join(failed) or "Standard signal gate passed")
        if result.get("entry_policy"):
            trace.context["entry_policy"] = result["entry_policy"]
    if role == "paper":
        trace.blocked(
            "readiness",
            not ready,
            "Account and controls verified before cutoff" if ready else "Pre-cutoff readiness missing",
        )
    return trace.payload()


def legacy_signal_trace(result, action, discovery, sentiment_enabled):
    trace = DecisionTrace(
        {
            "stage": "legacy_signal",
            "action": action,
            "model": result.get("model"),
            "source_ids": result.get("source_ids", []),
        }
    )
    trace.expect(
        [
            "inputs",
            "probability",
            "gross_expected_return",
            "volatility",
            "news",
            "sentiment",
            "readiness",
            "execution_window",
            "entry_risk",
            "order_intent",
            "broker",
        ]
    )
    score = result.get("horizons", {}).get("1")
    trace.blocked("inputs", not bool(score), result.get("reason", "Input availability"))
    if score:
        trace.blocked(
            "probability",
            score["probability_positive"] < (0.70 if discovery else 0.55),
            "Legacy probability floor",
        )
        trace.blocked(
            "gross_expected_return",
            score["expected_return"] <= 0.0015,
            "Legacy gross return must exceed 15bp",
        )
        if "volatility" in result:
            trace.blocked("volatility", result["volatility"] > 0.8, "Legacy volatility ceiling 80%")
        if discovery:
            trace.blocked("news", result.get("news_stance") != "bullish", "Sourced bullish news required")
        if sentiment_enabled:
            trace.blocked(
                "sentiment",
                result.get("hf_news_assessment") != "review",
                "Negative or unavailable sentiment veto",
            )
    trace.blocked("readiness", not result.get("account_ready_before_cutoff"), "Pre-cutoff account readiness")
    return trace.payload()
