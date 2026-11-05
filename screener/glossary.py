"""Plain-language explanations of everything this app puts on screen.

WHY THIS EXISTS
---------------
The dashboard shows a ticket reading ``AMD270115P00430000 — BUY option``,
``delta -0.526``, ``theta runs against a long option``. Every one of those is
precise and every one of them is opaque if nobody has told you what it means.
A tool that explains its own reasoning in jargon has not explained anything.

So the glossary ships with the app rather than living in a chat log or a
bookmarked article. Where the term appears on screen, the explanation is one
tab away.

THE WRITING RULES, because they are what make this useful
---------------------------------------------------------
1. ``plain`` must make sense to someone who has never traded. No term may be
   defined using another term from this glossary unless that term is also
   linked. If the plain text needs jargon, the definition has failed.

2. ``detail`` adds the precision that ``plain`` deliberately dropped. It is
   allowed to be technical. It is not allowed to contradict ``plain``.

3. ``seen_in`` names where in this app the term appears. An explanation you
   cannot connect to something on your screen is trivia.

4. No definition oversells. Where something is a convention rather than a
   finding, or carries a risk the name hides, the text says so.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GlossaryEntry", "CATEGORIES", "GLOSSARY", "by_category", "entry"]


@dataclass(frozen=True)
class GlossaryEntry:
    """One explained term."""

    term: str
    category: str
    plain: str
    detail: str = ""
    aka: tuple[str, ...] = ()
    seen_in: str = ""
    caution: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "term": self.term,
            "category": self.category,
            "plain": self.plain,
            "detail": self.detail,
            "aka": list(self.aka),
            "seen_in": self.seen_in,
            "caution": self.caution,
        }


#: Ordered so the page reads as a course rather than an alphabetical dump.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("start", "Start here",
     "The four ideas that make everything else make sense."),
    ("contract", "Reading a contract",
     "Decoding the symbols and numbers on a suggested contract."),
    ("greeks", "How an option's price moves",
     "Why the price changes when the stock does — and when it doesn't."),
    ("orders", "Placing and closing a trade",
     "The buttons, and what each one actually does."),
    ("score", "How this app scores things",
     "What the composite means, and what it deliberately does not mean."),
    ("risk", "Risk and sizing",
     "The parts that decide whether you survive being wrong."),
)


GLOSSARY: tuple[GlossaryEntry, ...] = (
    # ---------------------------------------------------------------- start
    GlossaryEntry(
        term="Option",
        category="start",
        plain=(
            "A thing you buy and own, like a share — except it is a deal about a "
            "share rather than the share itself. Its price goes up and down all "
            "day, and you can sell it back to the market whenever the market is "
            "open."
        ),
        detail=(
            "Formally, a contract giving you the right (not the obligation) to buy "
            "or sell 100 shares at a fixed price before a fixed date. In practice "
            "most traders never exercise that right — they buy the contract, its "
            "price moves, and they sell it."
        ),
        aka=("contract",),
        seen_in="Suggested contract, on any ticker's detail view.",
    ),
    GlossaryEntry(
        term="Expiration is a deadline, not a payday",
        category="start",
        plain=(
            "You do NOT wait until the expiry date to get your money. You sell "
            "whenever you like, the same way you would sell a share. The expiry "
            "date is the day the option stops existing — after it, the option is "
            "gone. Think of a concert ticket: you can resell it any day before the "
            "show, and the date on it is when it becomes worthless, not when you "
            "get paid."
        ),
        detail=(
            "This is the single most expensive misunderstanding in options. Most "
            "positions are closed days or weeks after they are opened, long before "
            "expiry. The date still matters, because the time value in the price "
            "shrinks as it approaches — but it is a countdown, not a settlement."
        ),
        aka=("expiry", "expiration", "DTE", "days to expiration"),
        seen_in="'exp 2027-01-15 (71d)' on a suggested contract.",
    ),
    GlossaryEntry(
        term="Contract = 100 shares",
        category="start",
        plain=(
            "Option prices are quoted per share, but one contract always covers "
            "100 shares. So a price of $59.80 means you actually pay $5,980."
        ),
        detail=(
            "Multiply every quoted option price by 100 to get real money. This "
            "catches nearly everyone once: a contract that 'only costs $2' is $200, "
            "and a $60 contract is a $6,000 position."
        ),
        seen_in="Every price on a suggested contract and order ticket.",
    ),
    GlossaryEntry(
        term="Call and Put",
        category="start",
        plain=(
            "A CALL profits when the stock goes UP. A PUT profits when the stock "
            "goes DOWN. That is the whole distinction."
        ),
        detail=(
            "A call is the right to buy at the strike; a put is the right to sell "
            "at it. Buying a put is how you bet against a stock without shorting "
            "it — and unlike shorting, your loss is capped at what you paid."
        ),
        seen_in="'430.0 put' on a contract; 'Call lean' / 'Put lean' on every ranking row.",
        caution=(
            "Picking the call when you meant the put is the most common beginner "
            "error, and it is the exact opposite trade. Check before you submit."
        ),
    ),

    # ------------------------------------------------------------- contract
    GlossaryEntry(
        term="Option symbol",
        category="contract",
        plain=(
            "The long code like AMD270115P00430000 is just four facts stuck "
            "together: the stock (AMD), the expiry date (27-01-15), whether it is a "
            "put or call (P), and the strike price (00430000 = $430)."
        ),
        detail=(
            "This is the OCC standard format. Strike is written as the price times "
            "1000, padded to eight digits, which is why $430 appears as 00430000."
        ),
        aka=("OCC symbol",),
        seen_in="The first line of any order ticket.",
    ),
    GlossaryEntry(
        term="Strike",
        category="contract",
        plain=(
            "The price written into the contract. For a put, you make money as the "
            "stock falls below it."
        ),
        detail=(
            "The strike is fixed for the life of the contract. The stock price "
            "moves; the strike does not. The gap between them is what drives most "
            "of the option's value."
        ),
        aka=("strike price",),
        seen_in="'430.0 put' — 430 is the strike.",
    ),
    GlossaryEntry(
        term="Premium",
        category="contract",
        plain=(
            "What the option costs. Quoted per share, so multiply by 100 for what "
            "leaves your account."
        ),
        detail=(
            "When you buy an option the premium is both your cost and your maximum "
            "loss. When you sell one, it is what you receive — and the risk is no "
            "longer capped."
        ),
        seen_in="'limit 59.80' on a ticket — $5,980 for one contract.",
    ),
    GlossaryEntry(
        term="Bid, Ask and Spread",
        category="contract",
        plain=(
            "The BID is what buyers are offering. The ASK is what sellers want. "
            "The gap between them is the SPREAD, and crossing it costs you real "
            "money the instant you trade."
        ),
        detail=(
            "The MID is halfway between, and is the fairest estimate of what the "
            "option is actually worth. Buy at the ask and you start down by half "
            "the spread; a 4% spread on a $6,000 position is roughly $240 gone "
            "before the stock has moved."
        ),
        aka=("mid", "bid/ask"),
        seen_in="'Bid / Ask $56.85 / $59.25 · Spread 4.1%' on a suggested contract.",
        caution=(
            "This is why every ticket here uses a limit price near the mid rather "
            "than a market order."
        ),
    ),
    GlossaryEntry(
        term="Open interest",
        category="contract",
        plain=(
            "How many of this exact contract are currently held by other people. "
            "More is better — it means you can get in and out easily."
        ),
        detail=(
            "Thin open interest usually comes with a wide spread, and a wide spread "
            "eats the edge before the trade starts. This app filters out contracts "
            "below your minimum rather than suggesting something you cannot trade."
        ),
        aka=("OI",),
        seen_in="'Open interest 4,096' on a suggested contract.",
    ),
    GlossaryEntry(
        term="In the money / Out of the money",
        category="contract",
        plain=(
            "IN THE MONEY means the contract already has real value — for a put, "
            "the stock is below the strike. OUT OF THE MONEY means it does not yet, "
            "and is pure hope."
        ),
        detail=(
            "An out-of-the-money option is cheaper and can gain far more in "
            "percentage terms, but it expires worthless unless the stock actually "
            "gets past the strike. In-the-money costs more and behaves more like "
            "the stock itself."
        ),
        aka=("ITM", "OTM"),
        seen_in="Implied by the gap between the strike and the current price.",
    ),

    # --------------------------------------------------------------- greeks
    GlossaryEntry(
        term="Intrinsic value and time value",
        category="greeks",
        plain=(
            "The price of an option is two things glued together. INTRINSIC VALUE "
            "is the part that is already real. TIME VALUE is the extra you pay for "
            "the chance it gets better — and that part shrinks to exactly zero by "
            "expiry."
        ),
        detail=(
            "If the stock is $404 and your put's strike is $430, $25.60 of the "
            "price is intrinsic — real today. Everything above that is time value. "
            "On a 71-day at-the-money option, time value is often more than half "
            "the price, which means more than half of what you paid is guaranteed "
            "to disappear if the stock does not move."
        ),
        aka=("extrinsic value",),
        seen_in="Not shown separately — it is the gap between the premium and the "
                "strike-to-price distance.",
    ),
    GlossaryEntry(
        term="Delta",
        category="greeks",
        plain=(
            "How much the option's price moves when the stock moves $1. A delta of "
            "-0.53 on a put means: stock falls $1, option gains about $0.53 — which "
            "is $53 per contract."
        ),
        detail=(
            "Puts have negative delta because they move opposite the stock. Delta "
            "is not fixed: it grows as the option goes further in the money. This "
            "app computes delta itself with Black-Scholes, because the data source "
            "publishes no greeks, so it will differ slightly from your broker's."
        ),
        seen_in="'delta -0.526' on a suggested contract, and the Delta-fit parameter.",
    ),
    GlossaryEntry(
        term="Theta",
        category="greeks",
        plain=(
            "The daily cost of waiting. Every day that passes, an option you own "
            "loses a little value even if the stock does nothing at all."
        ),
        detail=(
            "Theta is time value draining away, and it accelerates as expiry "
            "approaches — the last month decays fastest. This is why a correct "
            "prediction that takes too long still loses money, and why the holding "
            "period matters as much as the direction."
        ),
        seen_in="'Theta runs against a long option every day the thesis takes to "
                "play out' in the COSTS section of a ticket.",
    ),
    GlossaryEntry(
        term="Implied volatility",
        category="greeks",
        plain=(
            "How much movement the market is currently pricing into the option. "
            "High implied volatility means options are expensive; low means cheap."
        ),
        detail=(
            "It is not a forecast of direction, only of size of movement. Buying "
            "when implied volatility is high means paying up — and if it falls back "
            "afterwards, your option can lose value even when the stock moves your "
            "way. That is the trap this app's IV Rank parameter is watching for."
        ),
        aka=("IV",),
        seen_in="'Implied vol 59.8%' on a contract; the IV Rank parameter.",
    ),
    GlossaryEntry(
        term="Breakeven",
        category="greeks",
        plain=(
            "If you held all the way to expiry, this is the stock price where you "
            "would get exactly your money back. For a put: strike minus what you "
            "paid."
        ),
        detail=(
            "Breakeven only applies at expiry. Before then you can be profitable "
            "well short of it, because the option still carries time value. Treat "
            "breakeven as the worst case, not the plan."
        ),
        seen_in="Derived from the strike and the limit price on a ticket.",
    ),

    # --------------------------------------------------------------- orders
    GlossaryEntry(
        term="Buy to Open / Sell to Close",
        category="orders",
        plain=(
            "BUY TO OPEN starts a position. SELL TO CLOSE ends it and gives you the "
            "money. Those are the only two you need for the tickets this app "
            "produces."
        ),
        detail=(
            "The other two — Sell to Open and Buy to Close — are for selling "
            "options you do not own. That is a different trade with a very "
            "different risk profile, where losses are not capped."
        ),
        seen_in="'BUY option' on a ticket means Buy to Open.",
    ),
    GlossaryEntry(
        term="Limit order and market order",
        category="orders",
        plain=(
            "A LIMIT order says 'this price or better, otherwise don't fill'. A "
            "MARKET order says 'any price, right now'. Use limit orders."
        ),
        detail=(
            "On options, spreads are wide enough that market orders routinely cost "
            "several percent on entry. A limit at the mid may take a few minutes to "
            "fill, or may need nudging a cent at a time — that patience is most of "
            "your edge at this spread."
        ),
        seen_in="'limit 59.80 (mid 58.62 + 2% cap)' on a ticket.",
    ),
    GlossaryEntry(
        term="Paper trading",
        category="orders",
        plain=(
            "Practising with fake money on real prices. Orders behave like real "
            "ones; nothing is actually bought."
        ),
        detail=(
            "It teaches mechanics and timing honestly, but not everything. Paper "
            "fills are usually more generous than real ones, and it cannot "
            "reproduce how different a position feels when the money is yours."
        ),
        seen_in="Not part of this app — it is how you practise the tickets elsewhere.",
    ),

    # ---------------------------------------------------------------- score
    GlossaryEntry(
        term="Composite score",
        category="score",
        plain=(
            "A 0-100 number measuring how much the indicators AGREE with each "
            "other. It is not a prediction, and it is not a percentage chance of "
            "being right."
        ),
        detail=(
            "Each parameter produces a signal, that signal is multiplied by the "
            "weight you gave it, and the results are added up. A score near 50 "
            "means the evidence is coherent but unremarkable. A high score means "
            "the indicators are telling the same story — which can still be the "
            "wrong story."
        ),
        seen_in="The large number on every ranking row and detail view.",
    ),
    GlossaryEntry(
        term="Direction and conviction",
        category="score",
        plain=(
            "DIRECTION is which way the evidence points — call lean (up) or put "
            "lean (down). CONVICTION is how strongly it points that way. The score "
            "measures agreement; direction tells you what it agrees about."
        ),
        detail=(
            "This is why a ticker can rank first with a SELL ticket. Rank means "
            "'clearest story on your list', not 'best thing to buy'. Conviction "
            "also scales the options-layer contributions, so a good contract on a "
            "directionless stock cannot inflate a score."
        ),
        seen_in="The 'Put lean' / 'Call lean' pill, and the Conviction figure.",
    ),
    GlossaryEntry(
        term="Contribution",
        category="score",
        plain=(
            "How many points a single parameter added to the score. Positive means "
            "it AGREES with the direction; negative means it argues against it."
        ),
        detail=(
            "Contributions add up exactly to the composite — the detail view shows "
            "the arithmetic for each one so you can check it rather than trust it. "
            "A negative contribution is not a bug; it is a dissenting indicator, "
            "and it is usually the most interesting row on the page."
        ),
        seen_in="The right-hand column of the parameter breakdown.",
    ),
    GlossaryEntry(
        term="Weight redistribution",
        category="score",
        plain=(
            "When a parameter cannot be computed, its weight is shared out among "
            "the ones that could, instead of counting as a zero."
        ),
        detail=(
            "Scoring a missing value as zero would quietly punish the ticker for a "
            "data gap. ETFs have no earnings date, and IV Rank needs about 60 days "
            "of history before it means anything — neither is a flaw in the stock. "
            "The '10/11' badge tells you how many parameters actually counted."
        ),
        seen_in="The '10/11' badge on a ranking row; 'scale 100/57' in the arithmetic.",
    ),
    GlossaryEntry(
        term="Config fingerprint",
        category="score",
        plain=(
            "A short code identifying the exact settings that produced a ranking. "
            "Change a weight and the code changes."
        ),
        detail=(
            "Every stored run keeps the fingerprint that produced it, so an old "
            "ranking always shows what you actually saw that day. The practical "
            "consequence: runs under different fingerprints are not directly "
            "comparable, so settle your weights before you start logging decisions."
        ),
        seen_in="The 'CONFIG b709664d' chip above the rankings.",
    ),

    # ----------------------------------------------------------------- risk
    GlossaryEntry(
        term="Defined risk",
        category="risk",
        plain=(
            "When you BUY an option, the most you can lose is what you paid. That "
            "is the whole risk — no stop loss is needed to cap it."
        ),
        detail=(
            "This is the structural advantage of buying options over shorting "
            "stock. Shorting a share has no ceiling on the loss because the price "
            "has no ceiling. A long put has a floor built in."
        ),
        seen_in="'max loss: premium paid (defined risk)' on an options ticket.",
        caution=(
            "'Capped' is not 'small'. Losing 100% of the premium is entirely normal "
            "when an option expires out of the money."
        ),
    ),
    GlossaryEntry(
        term="Invalidation",
        category="risk",
        plain=(
            "The price at which the reason for the trade stops being true. Not "
            "where you panic — where you were simply wrong."
        ),
        detail=(
            "For a Head & Shoulders setup this is the neckline: above it, the "
            "pattern has failed and the evidence that justified the position no "
            "longer exists. Deciding it in advance is the point, because deciding "
            "it afterwards is when people move the line."
        ),
        seen_in="'invalidation 450.67 (neckline — pattern fails beyond this)'.",
    ),
    GlossaryEntry(
        term="Risk / reward and breakeven win rate",
        category="risk",
        plain=(
            "How much you stand to make compared with how much you stand to lose. "
            "A ratio of 1.19 means you win $1.19 for every $1 risked — which means "
            "you must be right more than 46% of the time just to break even."
        ),
        detail=(
            "Breakeven win rate is risk / (risk + reward). At 2:1 you need 33%; at "
            "3:1 you need 25%. This is why traders chase higher ratios — not greed, "
            "but room to be wrong more often."
        ),
        aka=("R/R",),
        seen_in="Computed from the limit, invalidation and target on a ticket.",
    ),
    GlossaryEntry(
        term="Position sizing",
        category="risk",
        plain=(
            "Deciding how much to buy. You do not pick a number of contracts — you "
            "pick what you are willing to lose, and that decides the number."
        ),
        detail=(
            "For a defined-risk option the premium is the risk, so one contract at "
            "$5,980 is a $5,980 bet. On a $100,000 account that is nearly 6% on a "
            "single position that can go to zero. A common starting rule is 1% of "
            "the account per trade."
        ),
        seen_in="'Size this yourself' — the app deliberately will not do this for you.",
        caution=(
            "The app refuses to size positions because correct sizing needs a "
            "measured win rate, and you do not have one until the journal has "
            "enough closed decisions."
        ),
    ),
    GlossaryEntry(
        term="Expected hold",
        category="risk",
        plain=(
            "Roughly how long the setup is expected to take to play out, in trading "
            "days. Weekends do not count, so 60 sessions is about 84 calendar days."
        ),
        detail=(
            "This matters more for options than for shares, because an option has "
            "an expiry. If the expected hold is longer than the contract's life, "
            "the position runs out of time before the thesis resolves — pick a "
            "later expiry instead."
        ),
        seen_in="'expected hold ~60 sessions' on a ticket.",
        caution=(
            "Compare it against the contract's DTE every time. 60 sessions is about "
            "84 calendar days; a 71-day contract does not cover it."
        ),
    ),
    GlossaryEntry(
        term="Decision journal",
        category="risk",
        plain=(
            "A record of what you took and what you skipped. The skips matter as "
            "much as the takes."
        ),
        detail=(
            "Without recorded skips there is no comparison group, and 'my picks "
            "worked' cannot be tested. The app will not suggest a single weight "
            "change until 15 decisions have closed, because a win rate from four "
            "trades is noise wearing a percentage sign."
        ),
        seen_in="The Journal tab, and the decision buttons on every detail view.",
    ),
)


def by_category() -> list[dict[str, object]]:
    """Entries grouped for rendering, in teaching order."""
    return [
        {
            "key": key,
            "label": label,
            "blurb": blurb,
            "entries": [e.to_dict() for e in GLOSSARY if e.category == key],
        }
        for key, label, blurb in CATEGORIES
    ]


def entry(term: str) -> GlossaryEntry | None:
    """Look up one entry by its exact term."""
    for item in GLOSSARY:
        if item.term == term:
            return item
    return None
