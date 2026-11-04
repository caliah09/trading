"""Order tickets — a reviewable specification of a trade.

The screener ranks and explains; this turns one ranked candidate into a
concrete, checkable order: instrument, limit price, invalidation level,
expected hold, and the cost of carrying it.

**What this deliberately does not do is size the position.** It reports max
loss and the invalidation level so you can size it yourself. Sizing needs a
measured win rate, and until the decision journal has one, any number here
would be fabricated precision.

A ticket is a *proposal*. Nothing places it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "OrderTicket", "LeveragedPair", "LEVERAGED_PAIRS",
    "build_ticket", "leveraged_drag",
]


@dataclass(frozen=True)
class LeveragedPair:
    """The leveraged expressions available for one underlying."""

    bull: str
    bear: str
    leverage: float
    expense_ratio: float          #: annual, as a decimal


#: Leveraged ETFs exist for indices and sectors, not single names — which is
#: why most of a single-stock watchlist has no entry here. Expense ratios are
#: approximate and should be checked against the current prospectus.
LEVERAGED_PAIRS: dict[str, LeveragedPair] = {
    "QQQ":  LeveragedPair("TQQQ", "SQQQ", 3.0, 0.0095),
    "SPY":  LeveragedPair("UPRO", "SPXU", 3.0, 0.0091),
    "IWM":  LeveragedPair("TNA",  "TZA",  3.0, 0.0108),
    "SOXX": LeveragedPair("SOXL", "SOXS", 3.0, 0.0097),
    "XLF":  LeveragedPair("FAS",  "FAZ",  3.0, 0.0096),
    "XLE":  LeveragedPair("ERX",  "ERY",  2.0, 0.0100),
    "DIA":  LeveragedPair("UDOW", "SDOW", 3.0, 0.0095),
}

#: Spread over the risk-free rate a leveraged fund pays to finance exposure.
FINANCING_SPREAD = 0.005

#: How far above the mid a limit may sit, as a percentage, by default.
DEFAULT_PRICE_CAP_PCT = 2.0


@dataclass
class OrderTicket:
    """A complete, reviewable trade proposal."""

    underlying: str
    direction: str                 #: "call" | "put" | "neutral"
    instrument: str                #: "option" | "equity" | "leveraged_etf"
    tradable_symbol: str
    side: str                      #: "buy" | "sell"
    order_type: str = "limit"
    limit_price: float | None = None
    price_basis: str = ""
    contract: dict | None = None
    invalidation: float | None = None
    invalidation_reason: str = ""
    target: float | None = None
    expected_hold_days: int | None = None
    max_loss: str = ""
    sizing_note: str = ""
    cost_notes: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    composite: float | None = None
    generated_for_run: int | None = None

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "direction": self.direction,
            "instrument": self.instrument,
            "tradable_symbol": self.tradable_symbol,
            "side": self.side,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "price_basis": self.price_basis,
            "contract": self.contract,
            "invalidation": self.invalidation,
            "invalidation_reason": self.invalidation_reason,
            "target": self.target,
            "expected_hold_days": self.expected_hold_days,
            "max_loss": self.max_loss,
            "sizing_note": self.sizing_note,
            "cost_notes": self.cost_notes,
            "reasoning": self.reasoning,
            "warnings": self.warnings,
            "composite": self.composite,
            "generated_for_run": self.generated_for_run,
        }

    def to_text(self) -> str:
        """Human-readable ticket, for the terminal or to copy into a broker."""
        lines = [
            f"{self.tradable_symbol}  —  {self.side.upper()} {self.instrument.replace('_', ' ')}",
            "=" * 60,
        ]
        if self.contract:
            lines.append(
                f"  contract       {self.contract.get('strike')} {self.contract.get('kind')} "
                f"exp {self.contract.get('expiry')}  ({self.contract.get('dte')}d)"
            )
            if self.contract.get("delta") is not None:
                lines.append(f"  delta          {float(self.contract['delta']):+.3f}")
        if self.limit_price is not None:
            lines.append(f"  limit          {self.limit_price:,.2f}   ({self.price_basis})")
        if self.invalidation is not None:
            lines.append(f"  invalidation   {self.invalidation:,.2f}   {self.invalidation_reason}")
        if self.target is not None:
            lines.append(f"  target         {self.target:,.2f}")
        if self.expected_hold_days:
            lines.append(f"  expected hold  ~{self.expected_hold_days} sessions")
        if self.max_loss:
            lines.append(f"  max loss       {self.max_loss}")
        if self.composite is not None:
            lines.append(f"  score          {self.composite:.1f}/100")

        if self.cost_notes:
            lines.append("")
            lines.append("  COSTS")
            lines.extend(f"    - {note}" for note in self.cost_notes)
        if self.reasoning:
            lines.append("")
            lines.append("  WHY")
            lines.extend(f"    - {reason}" for reason in self.reasoning)
        if self.warnings:
            lines.append("")
            lines.append("  WARNINGS")
            lines.extend(f"    ! {warning}" for warning in self.warnings)

        lines.append("")
        lines.append(f"  {self.sizing_note}")
        lines.append("  This is a proposal, not advice. Nothing has been placed.")
        return "\n".join(lines)


def leveraged_drag(realised_vol_pct: float, leverage: float, hold_days: int,
                   expense_ratio: float, risk_free_rate: float = 0.043) -> dict:
    """Expected cost of holding a leveraged ETF for ``hold_days``.

    Two components, and the research says the second is the larger:

    * **Rebalancing drag** — ``((L² − L) / 2) × σ²`` per year, from resetting
      exposure daily.
    * **Carry** — the expense ratio plus the financing cost of the borrowed
      exposure, ``(L − 1) × (risk-free + spread)``.

    Bessembinder decomposes levered-ETF underperformance as roughly 0.42%/month
    rebalancing against 1.04%/month frictions, so a volatility-only model
    understates the true cost. See ``docs/research/evidence-review.md`` §4.

    **Known tension, stated rather than hidden.** The rebalancing term is a
    *theoretical upper bound*: it assumes i.i.d. returns and continuous
    rebalancing. Real markets trend, and a trending market rebalances in your
    favour, so realised drag comes in below this. At 25% volatility the formula
    gives ~18.8%/year, well above Bessembinder's empirical ~5%/year — consistent
    with his attribution of the damage to *return reversals* rather than to
    volatility as such. Read the output as a conservative estimate for a choppy
    tape, not a point forecast. A model keyed to realised reversals rather than
    realised volatility would be better, and is not built.
    """
    sigma = max(0.0, realised_vol_pct) / 100.0
    rebalancing_annual = ((leverage ** 2 - leverage) / 2.0) * sigma ** 2
    carry_annual = expense_ratio + (leverage - 1.0) * (risk_free_rate + FINANCING_SPREAD)
    total_annual = rebalancing_annual + carry_annual
    fraction_of_year = max(0, hold_days) / 252.0

    return {
        "rebalancing_annual_pct": round(rebalancing_annual * 100, 2),
        "carry_annual_pct": round(carry_annual * 100, 2),
        "total_annual_pct": round(total_annual * 100, 2),
        "expected_cost_pct": round(total_annual * fraction_of_year * 100, 2),
        "hold_days": hold_days,
        "leverage": leverage,
        "realised_vol_pct": round(realised_vol_pct, 2),
    }


def build_ticket(
    entry: dict,
    *,
    instrument: str = "auto",
    price_cap_pct: float = DEFAULT_PRICE_CAP_PCT,
    risk_free_rate: float = 0.043,
    run_id: int | None = None,
    today: date | None = None,
) -> OrderTicket:
    """Turn one ranking entry into an order ticket.

    Args:
        entry: a row from ``Database.get_ranking``.
        instrument: "auto" picks an option when the chain produced one,
            otherwise equity. "leveraged_etf" forces the leveraged expression
            where one exists.
    """
    symbol = entry["symbol"]
    direction = entry.get("direction", "neutral")
    contract = entry.get("contract")
    spot = entry.get("spot")

    chosen = _choose_instrument(instrument, symbol, contract)

    ticket = OrderTicket(
        underlying=symbol,
        direction=direction,
        instrument=chosen,
        tradable_symbol=symbol,
        side="buy",
        composite=entry.get("composite"),
        generated_for_run=run_id,
        sizing_note=(
            "Size this yourself. The screener does not size positions — that needs a "
            "measured win rate, which the decision journal builds over time."
        ),
    )

    if direction == "neutral":
        ticket.warnings.append(
            "The stock layer found no directional edge here, so this ticket has no "
            "thesis behind it. Shown for completeness only."
        )

    _apply_pattern_context(ticket, entry)
    _apply_reasoning(ticket, entry)

    if chosen == "option":
        _fill_option(ticket, contract, price_cap_pct)
    elif chosen == "leveraged_etf":
        _fill_leveraged(ticket, entry, spot, risk_free_rate)
    else:
        _fill_equity(ticket, direction, spot)

    if entry.get("options_notes"):
        ticket.warnings.extend(entry["options_notes"])

    completeness = entry.get("completeness")
    if completeness:
        counted, considered = (int(x) for x in str(completeness).split("/"))
        if counted < considered:
            ticket.warnings.append(
                f"Scored on {completeness} parameters — {considered - counted} could not be "
                "computed and their weight was redistributed. Open the ticker detail for which."
            )
    return ticket


# --------------------------------------------------------------------------


def _choose_instrument(requested: str, symbol: str, contract: dict | None) -> str:
    if requested == "leveraged_etf":
        # Returned even with no listed pair, so _fill_leveraged can downgrade it
        # *and say why*. Silently handing back an equity ticket would leave you
        # wondering whether the leverage was applied.
        return "leveraged_etf"
    if requested == "equity":
        return "equity"
    if requested == "option":
        return "option"
    return "option" if contract else "equity"


def _apply_pattern_context(ticket: OrderTicket, entry: dict) -> None:
    """Pull invalidation, target and hold horizon from the pattern, if present."""
    for param in entry.get("parameters", []):
        if param.get("param_key") != "head_shoulders" or param.get("status") != "ok":
            continue
        detail = param.get("detail") or {}
        if detail.get("neckline_now") is not None:
            ticket.invalidation = float(detail["neckline_now"])
            ticket.invalidation_reason = "(neckline — pattern fails beyond this)"
        if detail.get("target") is not None:
            ticket.target = float(detail["target"])
        if detail.get("expected_hold_days"):
            ticket.expected_hold_days = int(detail["expected_hold_days"])
        return


def _apply_reasoning(ticket: OrderTicket, entry: dict) -> None:
    """Carry across the largest-contributing parameters, with their explanations."""
    scored = [
        p for p in entry.get("parameters", [])
        if p.get("status") == "ok" and abs(float(p.get("contribution") or 0)) > 0.5
    ]
    scored.sort(key=lambda p: abs(float(p["contribution"])), reverse=True)
    for param in scored[:4]:
        ticket.reasoning.append(
            f"{param['label']} {float(param['contribution']):+.2f} — "
            f"{param.get('explanation', '')[:180]}"
        )


def _fill_option(ticket: OrderTicket, contract: dict | None, price_cap_pct: float) -> None:
    if not contract:
        ticket.instrument = "equity"
        ticket.warnings.append("No contract was selected, so this fell back to the underlying.")
        return

    ticket.tradable_symbol = contract.get("contract_symbol", ticket.underlying)
    ticket.contract = contract
    ticket.side = "buy"

    mid = contract.get("mid")
    if mid:
        ticket.limit_price = round(float(mid) * (1 + price_cap_pct / 100.0), 2)
        ticket.price_basis = f"mid {float(mid):.2f} + {price_cap_pct:.0f}% cap"
    ticket.max_loss = "premium paid (defined risk)"

    spread = contract.get("spread_pct")
    if spread is not None and float(spread) > 10:
        ticket.warnings.append(
            f"Bid/ask spread is {float(spread):.1f}% of mid. You give that up on entry and "
            "again on exit."
        )
    if contract.get("open_interest") is not None and float(contract["open_interest"]) < 100:
        ticket.warnings.append(
            f"Open interest is only {float(contract['open_interest']):.0f}. Getting out may "
            "be harder than getting in."
        )
    if ticket.expected_hold_days and contract.get("dte"):
        if int(contract["dte"]) < ticket.expected_hold_days:
            ticket.warnings.append(
                f"Expiry is {contract['dte']} days out but the pattern's edge is associated "
                f"with a ~{ticket.expected_hold_days}-session hold. This expires before the "
                "thesis is expected to resolve."
            )
    ticket.cost_notes.append(
        "Theta runs against a long option every day the thesis takes to play out."
    )


def _fill_equity(ticket: OrderTicket, direction: str, spot: float | None) -> None:
    ticket.tradable_symbol = ticket.underlying
    ticket.side = "sell" if direction == "put" else "buy"
    if spot:
        # A short needs the limit below the mark, a long above it.
        adjust = 1.002 if ticket.side == "buy" else 0.998
        ticket.limit_price = round(float(spot) * adjust, 2)
        ticket.price_basis = f"last {float(spot):.2f} ± 0.2%"
    ticket.max_loss = (
        "unbounded without a stop — the invalidation level is where the thesis fails"
        if ticket.side == "sell" else "position value if it goes to zero"
    )


def _fill_leveraged(ticket: OrderTicket, entry: dict, spot: float | None,
                    risk_free_rate: float) -> None:
    pair = LEVERAGED_PAIRS.get(ticket.underlying)
    if pair is None:
        ticket.instrument = "equity"
        _fill_equity(ticket, ticket.direction, spot)
        ticket.warnings.append(
            f"No leveraged ETF exists for {ticket.underlying} — these are listed for indices "
            "and sectors, not single names. Fell back to the underlying."
        )
        return

    ticket.tradable_symbol = pair.bear if ticket.direction == "put" else pair.bull
    ticket.side = "buy"           # always long the fund; direction picks which fund
    ticket.max_loss = "position value if it goes to zero"
    ticket.price_basis = "market on open, or a limit of your choosing"

    realised_vol = _realised_vol(entry)
    hold = ticket.expected_hold_days or 60
    if realised_vol is None:
        ticket.warnings.append(
            "Realised volatility was unavailable, so holding cost could not be estimated. "
            "That is the main risk in these products — treat the ticket as incomplete."
        )
        return

    drag = leveraged_drag(realised_vol, pair.leverage, hold, pair.expense_ratio, risk_free_rate)
    ticket.cost_notes.append(
        f"Expected decay ≈ {drag['expected_cost_pct']:.2f}% over a ~{hold}-session hold "
        f"({drag['rebalancing_annual_pct']:.1f}%/yr rebalancing + "
        f"{drag['carry_annual_pct']:.1f}%/yr carry, at {realised_vol:.0f}% realised vol)."
    )
    ticket.cost_notes.append(
        "That cost is paid whether or not the direction is right, and it compounds against "
        "you in a choppy tape — the damage comes from reversals, not movement."
    )

    if ticket.target is not None and spot:
        projected_move = abs(float(ticket.target) - float(spot)) / float(spot) * 100
        levered_move = projected_move * pair.leverage
        if levered_move < drag["expected_cost_pct"] * 2:
            ticket.warnings.append(
                f"The projected move is {projected_move:.1f}% on the underlying "
                f"({levered_move:.1f}% levered) against {drag['expected_cost_pct']:.2f}% expected "
                "decay. Decay eats most of the edge — this is a poor leveraged expression, "
                "and the option or the underlying is likely the better trade."
            )


def _realised_vol(entry: dict) -> float | None:
    """Realised volatility from whichever parameter already computed it."""
    for param in entry.get("parameters", []):
        detail = param.get("detail") or {}
        if param.get("param_key") == "vol_risk_premium" and detail.get("realised_vol"):
            return float(detail["realised_vol"])
    return None
