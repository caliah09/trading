"""A broker that places nothing.

The default. It validates the request exactly as a real adapter would and
reports what *would* have been sent, which is what you want for every run
except the one where you actually mean it.
"""

from __future__ import annotations

import logging

from screener.brokers.base import OrderRequest, OrderResult

log = logging.getLogger(__name__)


class DryRunBroker:
    """Validates and logs orders without contacting anything."""

    name = "dry-run"
    is_paper = True

    def __init__(self) -> None:
        self.submitted: list[OrderRequest] = []

    def account(self) -> dict:
        return {
            "broker": self.name,
            "paper": True,
            "note": "No account is connected. Nothing can be placed from here.",
        }

    def submit(self, request: OrderRequest) -> OrderResult:
        request.validate()
        self.submitted.append(request)

        price = (f"limit {request.limit_price:,.2f}" if request.limit_price is not None
                 else "market")
        message = (f"Would {request.side} {request.quantity:g} {request.symbol} "
                   f"({request.asset_class}) at {price}, {request.time_in_force}.")
        log.info("DRY RUN: %s", message)
        return OrderResult(
            placed=False,
            status="simulated",
            symbol=request.symbol,
            message=message,
        )
