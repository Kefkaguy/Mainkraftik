from datetime import UTC, date, datetime

import httpx
import pytest

from intelligence.config import settings
from intelligence.providers import ProviderError, fetch_bars, get_json


def test_provider_host_allowlist_blocks_trading_and_unknown_hosts():
    with httpx.Client() as client:
        with pytest.raises(ProviderError, match="not permitted"):
            get_json(client, "https://paper-api.alpaca.markets/v2/orders")
        with pytest.raises(ProviderError, match="not permitted"):
            get_json(client, "https://example.invalid/redirect")


def test_denied_sip_access_does_not_fall_back_to_iex(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_key", "fixture-not-a-key")
    monkeypatch.setattr(settings, "alpaca_secret", "fixture-not-a-secret")
    monkeypatch.setattr(settings, "feed", "sip")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, json={"message": "subscription denied"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="HTTP 403"):
            fetch_bars("MSI", date(2025, 1, 1), datetime(2025, 2, 1, tzinfo=UTC), date(2025, 2, 1), client)
    assert len(requests) == 1 and requests[0].url.params["feed"] == "sip"


def test_repeated_pagination_token_refuses_partial_history(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_key", "fixture-not-a-key")
    monkeypatch.setattr(settings, "alpaca_secret", "fixture-not-a-secret")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"bars": [], "next_page_token": "repeated"})
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ProviderError, match="Repeated pagination"):
            fetch_bars("MSI", date(2025, 1, 1), datetime(2025, 2, 1, tzinfo=UTC), date(2025, 2, 1), client)
