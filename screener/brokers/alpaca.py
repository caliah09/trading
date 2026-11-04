"""Alpaca adapter.

> **Untested against the live API.** This was written from Alpaca's documented
> REST shape, but the sandbox this project was built in cannot reach
> `alpaca.markets`, so no call here has ever executed. Run it against the paper
> endpoint and check the first order carefully before trusting it. The option
> symbol format in particular is worth verifying: Alpaca uses OCC-style
> symbols, which is also what the option chain already provides, but that
> alignment has not been confirmed end to end.

Credentials are read from the environment and never logged:

    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY
"""

from __future__ import annotations

import logging
import os
import uuid

from screener.brokers.base import BrokerError, OrderRequest, OrderResult

log = logging.getLogger(__name__)

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"

#: Alpaca rejects anything but day/gtc on most order types.
_ALLOWED_TIF = {"day", "gtc", "opg", "cls", "ioc", "fok"}


class AlpacaBroker:
    """Minimal REST adapter for Alpaca's /v2/orders endpoint."""

    name = "alpaca"

    def __init__(self, paper: bool = True, timeout: float = 20.0) -> None:
        self.is_paper = paper
        self.timeout = timeout
        self.host = PAPER_HOST if paper else LIVE_HOST

        key = os.environ.get("ALPACA_API_KEY_ID")
        secret = os.environ.get("ALPACA_API_SECRET_KEY")
        if not key or not secret:
            raise BrokerError(
                "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY must be set in the "
                "environment. This tool never reads credentials from a file or an "
                "argument."
            )
        # Held only in memory, never logged or serialised.
        self._headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Content-Type": "application/json",
        }

    # -- internals ---------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        import requests  # imported lazily so the dry-run path needs no HTTP stack

        url = f"{self.host}{path}"
        try:
            response = requests.request(
                method, url, json=payload, headers=self._headers, timeout=self.timeout
            )
        except Exception as exc:
            raise BrokerError(f"Could not reach Alpaca at {url}: {exc}") from exc

        if response.status_code >= 400:
            # Alpaca puts a human-readable reason in the body; surface it rather
            # than a bare status code. The body never contains our credentials.
            raise BrokerError(
                f"Alpaca rejected {method} {path} with {response.status_code}: "
                f"{response.text[:400]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"Alpaca returned a non-JSON response: {exc}") from exc

    # -- Broker protocol ---------------------------------------------------

    def account(self) -> dict:
        data = self._request("GET", "/v2/account")
        return {
            "broker": self.name,
            "paper": self.is_paper,
            "account_number": data.get("account_number"),
            "status": data.get("status"),
            "buying_power": data.get("buying_power"),
            "cash": data.get("cash"),
            "equity": data.get("equity"),
            "options_approved_level": data.get("options_approved_level"),
        }

    def submit(self, request: OrderRequest) -> OrderResult:
        request.validate()
        if request.time_in_force not in _ALLOWED_TIF:
            raise BrokerError(
                f"time_in_force {request.time_in_force!r} is not one Alpaca accepts "
                f"({sorted(_ALLOWED_TIF)})."
            )

        payload: dict = {
            "symbol": request.symbol,
            "qty": str(request.quantity),
            "side": request.side,
            "type": request.order_type,
            "time_in_force": request.time_in_force,
            "client_order_id": request.client_order_id or f"screener-{uuid.uuid4().hex[:16]}",
        }
        if request.order_type == "limit":
            payload["limit_price"] = str(request.limit_price)

        log.info("Submitting to Alpaca (%s): %s %s %s",
                 "paper" if self.is_paper else "LIVE",
                 request.side, request.quantity, request.symbol)

        data = self._request("POST", "/v2/orders", payload)
        return OrderResult(
            placed=True,
            status=str(data.get("status", "unknown")),
            symbol=request.symbol,
            broker_order_id=data.get("id"),
            message=f"Accepted as {data.get('status')}",
            raw=data,
        )
