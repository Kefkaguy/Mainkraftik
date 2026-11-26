from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]
PAPER_BASE = "https://paper-api.alpaca.markets"
SYMBOLS = ("AMD", "MSFT", "GOOG", "AMZN", "TSM", "META")


class PaperSettings(BaseSettings):
    paper_experiment_enabled: bool = False
    paper_experiment_accounts_file: str = ""
    paper_mainkraft_enabled: bool = False
    paper_swing_trial_start: date | None = None
    paper_swing_trial_notional: Literal[25, 800] = 25

    @field_validator("paper_swing_trial_notional", mode="before")
    @classmethod
    def read_trial_notional(cls, value):
        # Environment settings arrive as text. Preserve the strict allowlist.
        if isinstance(value, str) and value in {"25", "800"}:
            return int(value)
        return value

    paper_cost_gate_enabled: bool = False
    paper_market_shadow_enabled: bool = False
    paper_sentiment_enabled: bool = False
    paper_sentiment_url: str = "http://sentiment:8300"
    paper_discovery_enabled: bool = False
    groq_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    paper_key: str = ""
    paper_secret: str = ""
    paper_base_url: str = PAPER_BASE
    paper_account_id: str = ""
    paper_forbidden_account_id: str = ""
    paper_database_url: str = "postgresql://intelligence:intelligence@127.0.0.1:5543/intelligence_paper"
    paper_execution_permitted: bool = False
    paper_daily_loss_limit: float = Field(default=12, gt=0, le=12)
    paper_order_timeout_seconds: int = Field(default=90, ge=5, le=90)
    paper_internal_token: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""
    feed: str = "sip"
    sec_user_agent: str = ""
    model_config = SettingsConfigDict(env_prefix="INTELLIGENCE_", env_file=ROOT / ".env", extra="ignore")


settings = PaperSettings()


def current_symbols(session=None):
    if not settings.paper_discovery_enabled:
        return SYMBOLS
    from paperpilot.universe import selected_symbols

    return tuple(selected_symbols(session))


def managed_symbols():
    if not settings.paper_discovery_enabled:
        return SYMBOLS
    from paperpilot.db import connect

    with connect() as conn:
        owned = [r["symbol"] for r in conn.execute("SELECT DISTINCT symbol FROM pilot_intents").fetchall()]
    return tuple(sorted(set(owned) | set(current_symbols())))


def quote_symbols():
    if not settings.paper_discovery_enabled:
        return (*SYMBOLS, "SPY")
    from paperpilot.db import connect

    with connect() as conn:
        first = conn.execute("SELECT payload FROM pilot_benchmarks ORDER BY id LIMIT 1").fetchone()
    initial = set(first["payload"]["inception_marks"]) if first else set()
    return tuple(sorted(set(managed_symbols()) | initial | {"SPY"}))
