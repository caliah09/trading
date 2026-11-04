"""Broker adapter interface.

Execution is deliberately the thinnest, most boring part of this project.
Everything interesting happens upstream in scoring; this layer only turns an
approved ticket into an API call.

Two rules it enforces by construction:

* **Credentials come from the environment, never from a file in this repo and
  never from an argument.** Nothing here reads, writes or logs a secret.
* **Paper is the default.** Reaching live money takes an explicit flag plus a
  typed confirmation, and is refused in unattended mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = ["OrderRequest", "OrderResult", "Broker", "BrokerError"]


class BrokerError(RuntimeError):
    """A broker rejected a request or could not be reached."""


@dataclass
class OrderRequest:
    """One order, normalised across brokers."""

    symbol: str                  #: equity ticker, or OCC-style option symbol
    side: str                    #: "buy" | "sell"
    quantity: float
    order_type: str = "limit"    #: "limit" | "market"
    limit_price: float | None = None
    time_in_force: str = "day"
    asset_class: str = "equity"  #: "equity" | "option"
    client_order_id: str | None = None

    def validate(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("a limit order needs a limit_price")
        if self.order_type not in ("limit", "market"):
            raise ValueError(f"unsupported order type {self.order_type!r}")


@dataclass
class OrderResult:
    """What came back. ``placed`` is False for anything simulated."""

    placed: bool
    status: str
    symbol: str
    message: str = ""
    broker_order_id: str | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "placed": self.placed,
            "status": self.status,
            "symbol": self.symbol,
            "message": self.message,
            "broker_order_id": self.broker_order_id,
        }


@runtime_checkable
class Broker(Protocol):
    """The whole surface the execution script depends on."""

    name: str
    is_paper: bool

    def account(self) -> dict:
        """Account summary, used to show you what you are about to trade against."""
        ...

    def submit(self, request: OrderRequest) -> OrderResult:
        """Place one order."""
        ...
