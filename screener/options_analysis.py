"""Option chain analysis: IV sampling, chain hygiene, and contract selection.

Three jobs, in order of importance:

1. **Sample at-the-money IV at a consistent tenor.** IV Rank compares today's
   implied volatility against its own history. If today's sample comes from a
   45-day expiry and last week's came from a 7-day expiry, the comparison is
   measuring the term structure, not a change in volatility. Every IV snapshot
   is therefore taken from the expiry nearest :data:`IV_REFERENCE_DTE`,
   regardless of which contract the screener ends up suggesting.

2. **Reject unusable quotes.** Yahoo publishes rows for strikes that have not
   traded in weeks. Scoring a delta derived from a stale IV, or suggesting a
   contract with a 40%-wide bid/ask, produces confident nonsense.

3. **Select a contract matching the target delta and expiry.**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from screener.blackscholes import delta as bs_delta
from screener.config import ScreenerConfig
from screener.providers.base import ChainSnapshot, MarketDataProvider
from screener.scoring.ramps import window_fit

log = logging.getLogger(__name__)

#: Tenor used for every stored IV observation. 30 days is the industry
#: convention (it is what VIX-style measures target) and is liquid on all
#: reasonably traded names.
IV_REFERENCE_DTE = 30

#: How many candidate expiries to try before giving up on contract selection.
#: The best-fitting expiry sometimes has a chain too thin to pass hygiene.
MAX_EXPIRY_ATTEMPTS = 3


@dataclass
class ContractCandidate:
    """A single option contract that passed hygiene checks."""

    contract_symbol: str
    kind: str            # "call" | "put"
    strike: float
    expiry: date
    dte: int
    bid: float
    ask: float
    mid: float
    spread_pct: float
    open_interest: float
    volume: float
    implied_volatility: float
    delta: float

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["expiry"] = self.expiry.isoformat()
        return data


@dataclass
class OptionsAssessment:
    """Everything the options-layer scorers need for one ticker."""

    symbol: str
    spot: float | None = None
    atm_iv: float | None = None                 # decimal, e.g. 0.284
    iv_reference_expiry: date | None = None
    iv_reference_dte: int | None = None
    selected: ContractCandidate | None = None
    best_expiry: date | None = None
    best_dte: int | None = None
    expiry_count: int = 0
    #: Human-readable notes explaining any absence — surfaced in the UI.
    notes: list[str] = field(default_factory=list)

    @property
    def has_chain(self) -> bool:
        return self.expiry_count > 0


def analyse_options(
    provider: MarketDataProvider,
    symbol: str,
    spot: float | None,
    config: ScreenerConfig,
    direction: str,
    today: date | None = None,
) -> OptionsAssessment:
    """Sample IV and select a contract for ``symbol``.

    Args:
        direction: "call" or "put" — the lean chosen by the stock layer. The
            contract is selected from that side of the chain.

    Returns an assessment whose fields are ``None`` where data was unavailable;
    the scoring engine converts those into "insufficient data" and redistributes
    the parameter's weight.
    """
    today = today or date.today()
    assessment = OptionsAssessment(symbol=symbol, spot=spot)

    if spot is None or spot <= 0:
        assessment.notes.append("No underlying price available, so no option analysis was possible.")
        return assessment

    expiries = [e for e in provider.option_expiries(symbol) if (e - today).days > 0]
    assessment.expiry_count = len(expiries)
    if not expiries:
        assessment.notes.append("No listed options chain found for this ticker.")
        return assessment

    # -- 1. IV sample at a fixed reference tenor ---------------------------
    iv_expiry = min(expiries, key=lambda e: abs((e - today).days - IV_REFERENCE_DTE))
    iv_chain = provider.option_chain(symbol, iv_expiry)
    assessment.iv_reference_expiry = iv_expiry
    assessment.iv_reference_dte = (iv_expiry - today).days
    assessment.atm_iv = atm_implied_volatility(iv_chain, spot)
    if assessment.atm_iv is None:
        assessment.notes.append(
            f"Chain for {iv_expiry:%Y-%m-%d} carried no usable implied volatility near the money."
        )

    # -- 2. Contract selection --------------------------------------------
    target = config.target()
    ranked_expiries = sorted(
        expiries,
        key=lambda e: window_fit(
            (e - today).days, target.dte_low, target.dte_high,
            config.threshold("dte_fit", "tolerance"),
        ),
        reverse=True,
    )
    assessment.best_expiry = ranked_expiries[0]
    assessment.best_dte = (ranked_expiries[0] - today).days

    for expiry in ranked_expiries[:MAX_EXPIRY_ATTEMPTS]:
        chain = iv_chain if expiry == iv_expiry else provider.option_chain(symbol, expiry)
        candidate = select_contract(chain, spot, direction, config, today)
        if candidate is not None:
            assessment.selected = candidate
            assessment.best_expiry = expiry
            assessment.best_dte = candidate.dte
            break
    else:
        assessment.notes.append(
            "No contract passed the liquidity filters "
            f"(open interest ≥ {config.min_open_interest}, "
            f"bid/ask spread ≤ {config.max_spread_pct:.0f}%) "
            f"in the {MAX_EXPIRY_ATTEMPTS} best-fitting expiries."
        )

    return assessment


def atm_implied_volatility(chain: ChainSnapshot, spot: float) -> float | None:
    """Average call/put implied volatility at the strike nearest ``spot``.

    Averaging both sides is more robust than taking either alone: put-call
    parity means they should agree, so a large divergence indicates one side is
    stale, and the mean limits the damage.
    """
    samples: list[float] = []
    for side in ("calls", "puts"):
        frame = getattr(chain, side)
        if frame.empty:
            continue
        usable = frame.dropna(subset=["strike", "implied_volatility"])
        if usable.empty:
            continue
        nearest = usable.iloc[(usable["strike"] - spot).abs().argsort().iloc[0]]
        samples.append(float(nearest["implied_volatility"]))
    if not samples:
        return None
    return float(np.mean(samples))


def select_contract(
    chain: ChainSnapshot,
    spot: float,
    kind: str,
    config: ScreenerConfig,
    today: date,
) -> ContractCandidate | None:
    """Pick the contract whose delta sits closest to the configured target.

    Returns None when nothing on that side of the chain survives hygiene
    filtering — a thin chain should produce no suggestion rather than a bad one.
    """
    frame = chain.side(kind)
    if frame.empty:
        return None

    dte = (chain.expiry - today).days
    if dte <= 0:
        return None
    time_years = dte / 365.0

    usable = frame.dropna(subset=["strike", "implied_volatility"]).copy()
    if usable.empty:
        return None

    usable["mid"] = _mid_price(usable)
    usable = usable[usable["mid"] > 0]
    if usable.empty:
        return None

    usable["spread_pct"] = ((usable["ask"] - usable["bid"]) / usable["mid"]) * 100.0

    hygienic = usable[
        (usable["open_interest"] >= config.min_open_interest)
        & (usable["spread_pct"] <= config.max_spread_pct)
        & (usable["spread_pct"] >= 0)
    ]
    if hygienic.empty:
        return None

    deltas = [
        bs_delta(
            spot=spot,
            strike=float(row.strike),
            time_years=time_years,
            vol=float(row.implied_volatility),
            kind=kind,
            rate=config.risk_free_rate,
            dividend=config.dividend_yield,
        )
        for row in hygienic.itertuples()
    ]
    hygienic = hygienic.assign(delta=deltas).dropna(subset=["delta"])
    if hygienic.empty:
        return None

    target = config.target()
    hygienic = hygienic.assign(
        delta_gap=(hygienic["delta"].abs() - target.delta_mid).abs()
    )
    # Ties on delta (adjacent strikes can round alike) go to the more liquid contract.
    best = hygienic.sort_values(
        by=["delta_gap", "open_interest"], ascending=[True, False]
    ).iloc[0]

    return ContractCandidate(
        contract_symbol=str(best["contract_symbol"]),
        kind=kind,
        strike=float(best["strike"]),
        expiry=chain.expiry,
        dte=dte,
        bid=_safe_float(best["bid"]),
        ask=_safe_float(best["ask"]),
        mid=float(best["mid"]),
        spread_pct=float(best["spread_pct"]),
        open_interest=float(best["open_interest"]),
        volume=float(best["volume"]),
        implied_volatility=float(best["implied_volatility"]),
        delta=float(best["delta"]),
    )


def _mid_price(frame: pd.DataFrame) -> pd.Series:
    """Mid price, falling back to last traded price when a side is missing.

    Yahoo returns bid/ask of 0 outside regular hours. Using ``last_price`` then
    keeps the screener usable in the evening, at the cost of a staler mark.
    """
    bid = frame["bid"].fillna(0.0)
    ask = frame["ask"].fillna(0.0)
    mid = (bid + ask) / 2.0
    both_sides = (bid > 0) & (ask > 0)
    return mid.where(both_sides, frame["last_price"].fillna(0.0))


def _safe_float(value) -> float:
    return 0.0 if pd.isna(value) else float(value)
