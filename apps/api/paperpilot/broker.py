"""The only execution client: immutable paper host, finite routes, no POST retry."""

import re
from decimal import Decimal, InvalidOperation

import httpx

from paperpilot.config import PAPER_BASE, current_symbols, managed_symbols, settings
from paperpilot.risk import scaled_buy_authorized


class BrokerUnavailable(ValueError):
    pass


class PaperBroker:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=12, follow_redirects=False)

    def request(self, method, path, **kwargs):
        if settings.paper_base_url != PAPER_BASE or not settings.paper_key or not settings.paper_secret:
            raise BrokerUnavailable("Dedicated paper credentials missing or endpoint not allowed")
        allowed = (
            path
            in {
                "/v2/account",
                "/v2/clock",
                "/v2/calendar",
                "/v2/positions",
                "/v2/orders",
                "/v2/orders:by_client_order_id",
                "/v2/account/activities",
                "/v2/assets",
            }
            or re.fullmatch(r"/v2/assets/[A-Z]+", path)
            or re.fullmatch(r"/v2/orders/[a-fA-F0-9-]{36}", path)
        )
        if not allowed or method not in {"GET", "POST", "DELETE"}:
            raise BrokerUnavailable("Paper route not allowed")
        scaled_buy = False
        if method == "POST":
            body = kwargs.get("json", {})
            if set(body) - {"symbol", "side", "type", "time_in_force", "client_order_id", "notional", "qty"}:
                raise BrokerUnavailable("Unsupported order fields")
            try:
                value = Decimal(str(body.get("notional") if body.get("side") == "buy" else body.get("qty")))
                if (
                    not value.is_finite()
                    or value <= 0
                    or (body.get("side") == "buy" and "qty" in body)
                    or (body.get("side") == "sell" and "notional" in body)
                ):
                    raise BrokerUnavailable("Order amount violates cash stock policy")
            except (InvalidOperation, TypeError):
                raise BrokerUnavailable("Invalid paper order amount") from None
            if (
                path != "/v2/orders"
                or body.get("symbol")
                not in (current_symbols() if body.get("side") == "buy" else managed_symbols())
                or body.get("side") not in {"buy", "sell"}
                or body.get("type") != "market"
                or body.get("time_in_force") != "day"
                or not body.get("client_order_id", "").startswith("kti4-")
            ):
                raise BrokerUnavailable("Order violates paper-only stock policy")
            if body["side"] == "buy" and value > 100:
                scaled_buy = scaled_buy_authorized(body, value)
                if not scaled_buy:
                    raise BrokerUnavailable("Scaled buy lacks a current authorized Swing intent")
        if method == "DELETE" and not re.fullmatch(r"/v2/orders/[a-fA-F0-9-]{36}", path):
            raise BrokerUnavailable("Bulk cancellation prohibited")
        if method in {"POST", "DELETE"}:
            account = self.account()
            if (
                not settings.paper_account_id
                or not settings.paper_forbidden_account_id
                or not account
                or account.get("id") != settings.paper_account_id
                or account.get("id") == settings.paper_forbidden_account_id
            ):
                raise BrokerUnavailable("Mutation refused: dedicated account identity not verified")
            if method == "POST":
                # Observe open reservations before positions and cash. A fill
                # between calls is therefore reserved or reflected in holdings;
                # the last cash read cannot authorize borrowing from stale cash.
                open_orders = self.orders()
                positions = self.positions()
                account = self.account()
                verify_account(account, positions, open_orders)
                if len(open_orders) >= 500:
                    raise BrokerUnavailable("Order pagination incomplete; mutation blocked")
                symbol = body["symbol"]
                if any(o["symbol"] == symbol for o in open_orders):
                    raise BrokerUnavailable("Existing symbol order must reconcile before a new order")
                owned = next((p for p in positions if p["symbol"] == symbol), None)
                if body["side"] == "sell":
                    available = (
                        Decimal(str(owned.get("qty_available", owned.get("qty", 0)))) if owned else Decimal(0)
                    )
                    if not owned or owned.get("side") != "long" or value > available:
                        raise BrokerUnavailable("Sell exceeds currently available long inventory")
                else:
                    count = {p["symbol"] for p in positions if Decimal(str(p.get("qty", 0))) != 0} | {
                        o["symbol"] for o in open_orders if o["side"] == "buy"
                    }
                    if len(count) >= (20 if settings.paper_discovery_enabled else 6):
                        raise BrokerUnavailable("Concurrent position limit reached")
                    if owned and Decimal(str(owned.get("qty", 0))) != 0:
                        raise BrokerUnavailable("Existing position; no additional purchase")
                    if any(Decimal(str(p["market_value"])) > 100 for p in positions):
                        raise BrokerUnavailable("Existing stock exposure breach blocks new purchases")
                    pending_buys = [o for o in open_orders if o["side"] == "buy"]
                    if any(o.get("notional") is None for o in pending_buys):
                        raise BrokerUnavailable("Unknown buy reservation blocks new purchases")
                    reserve = sum(Decimal(str(o["notional"])) for o in pending_buys)
                    exposure = sum(Decimal(str(p["market_value"])) for p in positions)
                    cash = min(
                        Decimal(str(account.get("cash", 0))), Decimal(str(account.get("buying_power", 0)))
                    )
                    if scaled_buy and (positions or open_orders):
                        raise BrokerUnavailable("Scaled Swing trial requires flat broker inventory")
                    if cash - reserve < value or (not scaled_buy and exposure + reserve + value > 600):
                        raise BrokerUnavailable("Fresh cash/reservations/exposure gate blocks purchase")
        if scaled_buy and not scaled_buy_authorized(body, value):
            raise BrokerUnavailable("Scaled Swing entry window or authorization expired before submission")
        headers = {"APCA-API-KEY-ID": settings.paper_key, "APCA-API-SECRET-KEY": settings.paper_secret}
        try:
            result = self.client.request(method, PAPER_BASE + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise BrokerUnavailable(
                f"{type(exc).__name__}: acknowledgement unknown; reconcile client ID, never blindly retry"
            ) from None
        if result.status_code == 404:
            return None
        if result.status_code not in {200, 201, 204}:
            raise BrokerUnavailable(f"Paper API HTTP {result.status_code}; no automatic resubmission")
        return result.json() if result.content else {}

    def account(self):
        return self.request("GET", "/v2/account")

    def clock(self):
        return self.request("GET", "/v2/clock")

    def positions(self):
        return self.request("GET", "/v2/positions")

    def orders(self, status="open"):
        return self.request("GET", "/v2/orders", params={"status": status, "limit": 500, "direction": "asc"})

    def by_client(self, client_id):
        return self.request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id})

    def submit(self, body):
        return self.request("POST", "/v2/orders", json=body)

    def cancel(self, order):
        if not order.get("client_order_id", "").startswith("kti4-"):
            raise BrokerUnavailable("Refusing to cancel an unrelated order")
        return self.request("DELETE", "/v2/orders/" + order["id"])


def verify_account(account, positions, orders):
    if not settings.paper_account_id or not settings.paper_forbidden_account_id:
        raise BrokerUnavailable("Pin both dedicated and old paper account IDs before activation")
    if (
        account.get("id") != settings.paper_account_id
        or account.get("id") == settings.paper_forbidden_account_id
    ):
        raise BrokerUnavailable("Paper account identity is not isolated from old trader")
    if settings.paper_key == settings.alpaca_key:
        raise BrokerUnavailable(
            "Execution credentials must be separate from existing market-data credentials"
        )
    if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
        raise BrokerUnavailable("Paper account is not active for trading")
    if float(account.get("cash", 0)) < 0:
        raise BrokerUnavailable("Negative cash: borrowing is prohibited")
    if any(p.get("symbol") not in managed_symbols() or p.get("side") != "long" for p in positions):
        raise BrokerUnavailable("Unexpected or short position: dedicated account isolation failed")
    if any(not o.get("client_order_id", "").startswith("kti4-") for o in orders):
        raise BrokerUnavailable("Unrelated account orders detected")
    return True
