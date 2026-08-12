"""User configuration: watchlist, weights, thresholds, contract targeting.

Every ranking run stores a hash-identified snapshot of the exact config that
produced it (see ``screener.db``), so historical rankings remain interpretable
after you change your weights. ``ScreenerConfig.fingerprint`` is that hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from screener.param_specs import ALL_SPECS, SPECS_BY_KEY, spec

Posture = Literal["buy", "sell"]

DEFAULT_WATCHLIST: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "AMD", "SPY", "QQQ",
)


class ConfigError(ValueError):
    """Raised when a configuration is structurally invalid."""


@dataclass
class ParameterConfig:
    """User-controlled settings for a single scoring parameter."""

    weight: float
    enabled: bool = True
    thresholds: dict[str, float] = field(default_factory=dict)

    def threshold(self, key: str, param_key: str) -> float:
        """Return a threshold, falling back to the registered default.

        Falling back rather than raising means a config written by an older
        version of the app still loads after a new threshold is introduced.
        """
        if key in self.thresholds:
            return float(self.thresholds[key])
        for t in spec(param_key).thresholds:
            if t.key == key:
                return t.default
        raise KeyError(f"Parameter {param_key!r} has no threshold {key!r}")


@dataclass
class ContractTarget:
    """Target contract shape for one posture."""

    delta_low: float
    delta_high: float
    dte_low: int
    dte_high: int

    @property
    def delta_mid(self) -> float:
        return (self.delta_low + self.delta_high) / 2.0

    @property
    def dte_mid(self) -> float:
        return (self.dte_low + self.dte_high) / 2.0


# Defaults chosen in setup: "Standard" targeting profile.
DEFAULT_BUY_TARGET = ContractTarget(delta_low=0.45, delta_high=0.60, dte_low=45, dte_high=90)
DEFAULT_SELL_TARGET = ContractTarget(delta_low=0.16, delta_high=0.30, dte_low=30, dte_high=45)


@dataclass
class ScreenerConfig:
    """The complete set of knobs that determine a ranking."""

    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    posture: Posture = "buy"
    top_n: int = 5
    parameters: dict[str, ParameterConfig] = field(default_factory=dict)
    buy_target: ContractTarget = field(default_factory=lambda: replace(DEFAULT_BUY_TARGET))
    sell_target: ContractTarget = field(default_factory=lambda: replace(DEFAULT_SELL_TARGET))

    #: When True, options-layer contributions are scaled by directional conviction,
    #: so a perfect contract on a no-signal stock does not manufacture a high score.
    #: See README "Why the options layer is gated" for the tradeoff.
    conviction_gate: bool = True

    #: Black-Scholes inputs. yfinance publishes no greeks, so delta is computed
    #: locally and these are the assumptions that go into it.
    risk_free_rate: float = 0.043
    dividend_yield: float = 0.0

    #: Minimum bars required before an indicator is considered computable.
    min_bars_200ma: int = 200
    #: Chain hygiene — contracts failing these are ignored during selection.
    min_open_interest: int = 10
    max_spread_pct: float = 25.0

    def __post_init__(self) -> None:
        # Seed any parameter the user has not customised.
        for s in ALL_SPECS:
            if s.key not in self.parameters:
                self.parameters[s.key] = ParameterConfig(
                    weight=s.default_weight,
                    enabled=True,
                    thresholds=s.default_thresholds(),
                )
        self.watchlist = normalise_watchlist(self.watchlist)
        self.validate()

    # -- validation --------------------------------------------------------

    def validate(self) -> None:
        if self.posture not in ("buy", "sell"):
            raise ConfigError(f"posture must be 'buy' or 'sell', got {self.posture!r}")
        if self.top_n < 1:
            raise ConfigError("top_n must be at least 1")
        unknown = set(self.parameters) - set(SPECS_BY_KEY)
        if unknown:
            raise ConfigError(f"Unknown parameters in config: {sorted(unknown)}")
        for key, pc in self.parameters.items():
            if not 0.0 <= pc.weight <= 10.0:
                raise ConfigError(f"Weight for {key!r} must be within 0-10, got {pc.weight}")
        for name, target in (("buy_target", self.buy_target), ("sell_target", self.sell_target)):
            if not 0.0 < target.delta_low <= target.delta_high < 1.0:
                raise ConfigError(f"{name}: require 0 < delta_low <= delta_high < 1")
            if not 0 < target.dte_low <= target.dte_high:
                raise ConfigError(f"{name}: require 0 < dte_low <= dte_high")
        if self.active_weight_total() <= 0:
            raise ConfigError(
                "At least one parameter must be enabled with a weight above zero, "
                "otherwise no ranking can be produced."
            )

    # -- convenience -------------------------------------------------------

    def target(self) -> ContractTarget:
        """The contract target for the currently selected posture."""
        return self.buy_target if self.posture == "buy" else self.sell_target

    def active(self, key: str) -> bool:
        pc = self.parameters[key]
        return pc.enabled and pc.weight > 0

    def active_weight_total(self) -> float:
        return sum(pc.weight for k, pc in self.parameters.items() if self.active(k))

    def threshold(self, param_key: str, threshold_key: str) -> float:
        return self.parameters[param_key].threshold(threshold_key, param_key)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScreenerConfig":
        params = {
            k: ParameterConfig(
                weight=float(v["weight"]),
                enabled=bool(v.get("enabled", True)),
                thresholds={tk: float(tv) for tk, tv in (v.get("thresholds") or {}).items()},
            )
            for k, v in (data.get("parameters") or {}).items()
            if k in SPECS_BY_KEY  # silently drop parameters this version no longer has
        }

        def _target(raw: dict[str, Any] | None, fallback: ContractTarget) -> ContractTarget:
            if not raw:
                return replace(fallback)
            return ContractTarget(
                delta_low=float(raw["delta_low"]),
                delta_high=float(raw["delta_high"]),
                dte_low=int(raw["dte_low"]),
                dte_high=int(raw["dte_high"]),
            )

        known = {f for f in cls.__dataclass_fields__}
        scalars = {
            k: v for k, v in data.items()
            if k in known and k not in {"parameters", "buy_target", "sell_target", "watchlist"}
        }
        return cls(
            watchlist=list(data.get("watchlist") or DEFAULT_WATCHLIST),
            parameters=params,
            buy_target=_target(data.get("buy_target"), DEFAULT_BUY_TARGET),
            sell_target=_target(data.get("sell_target"), DEFAULT_SELL_TARGET),
            **scalars,
        )

    def canonical_json(self) -> str:
        """Stable JSON used for hashing. Key order is fixed so the hash is reproducible."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        """Short stable identifier for this exact configuration."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:12]

    # -- files -------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: Path) -> "ScreenerConfig":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


def normalise_watchlist(symbols: list[str]) -> list[str]:
    """Upper-case, strip, and de-duplicate while preserving order.

    Yahoo symbols are case-insensitive but the database keys on the string, so
    normalising here prevents 'aapl' and 'AAPL' becoming two different tickers.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols:
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out
