"""Named candidate universes.

WHY THESE LISTS ARE STATIC
--------------------------
A screener needs a candidate pool. The obvious instinct is "every listed
stock", and that instinct is wrong for this tool: roughly 500-600 US names
have options liquid enough to trade, and the rest fail the chain hygiene
filters by construction. Scanning 5,800 tickers to discard 5,200 of them
spends hours of rate-limited bandwidth to arrive at the same answer.

So the universes here are curated rather than discovered. They are plain
tuples compiled into the package: no network call, no third-party list
service, no parsing someone's HTML table at refresh time.

THE COST OF THAT CHOICE, STATED PLAINLY
---------------------------------------
Index membership drifts. The S&P 500 turns over roughly 20-25 names a year,
and tickers change on mergers and renames. This list is a snapshot, not a
live feed, so it will be a few names wrong and will get wronger over time.

That degrades gracefully by design. A symbol that no longer resolves returns
no price history, and the pipeline records it in ``RunSummary.failed`` with a
reason rather than aborting the run. A missing constituent means one candidate
you did not consider; it does not mean a broken refresh or a wrong score.

If you need a name that is not here, add it to ``extra_symbols`` in the
config — that is appended to whichever universe is selected.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Universe", "UNIVERSES", "UNIVERSE_NAMES", "resolve", "describe",
    "WATCHLIST_UNIVERSE", "UnknownUniverse",
]

#: The sentinel universe meaning "whatever is in config.watchlist".
WATCHLIST_UNIVERSE = "watchlist"


class UnknownUniverse(ValueError):
    """Raised when a config names a universe that does not exist."""


# --- sector and thematic ETFs ------------------------------------------------
# The SPDR sector set plus the handful of thematic funds with genuinely liquid
# option chains. These matter for rotation reads: a semiconductor signal means
# something different when SMH is leading than when it is lagging.
_SECTOR_ETFS: tuple[str, ...] = (
    "SPY", "QQQ", "DIA", "IWM", "MDY", "RSP",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "SMH", "SOXX", "XBI", "IBB", "XOP", "XME", "XRT", "XHB", "KRE", "ITB",
    "GLD", "SLV", "USO", "UNG", "TLT", "IEF", "HYG", "LQD", "EEM", "EFA", "FXI",
    "ARKK", "VXX", "UVXY",
)

# --- leveraged and inverse ETFs ----------------------------------------------
# Kept as a separate group because they are a different instrument class, not
# just more tickers. Daily-rebalanced leverage decays in choppy markets — see
# tickets.leveraged_drag — so the screener treats them as an expression choice
# rather than as ordinary equities.
_LEVERAGED_ETFS: tuple[str, ...] = (
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SPXS",
    "SOXL", "SOXS", "TNA", "TZA", "FAS", "FAZ",
    "LABU", "LABD", "ERX", "ERY", "YINN", "YANG",
    "TMF", "TMV", "NUGT", "DUST", "JNUG", "JDST",
    "UDOW", "SDOW", "QLD", "SSO", "SDS", "QID",
)

# --- Nasdaq-100 --------------------------------------------------------------
# Snapshot. Overlaps the S&P 500 heavily; de-duplication happens in resolve().
_NASDAQ100: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "AVGO", "TSLA",
    "COST", "NFLX", "AMD", "PEP", "ADBE", "LIN", "CSCO", "TMUS", "INTU", "QCOM",
    "TXN", "AMGN", "ISRG", "CMCSA", "AMAT", "HON", "BKNG", "VRTX", "ADP", "PANW",
    "SBUX", "GILD", "MU", "ADI", "LRCX", "MDLZ", "REGN", "KLAC", "SNPS", "CDNS",
    "PYPL", "MAR", "CSX", "ORLY", "ASML", "CRWD", "ABNB", "MELI", "FTNT", "CTAS",
    "NXPI", "MNST", "WDAY", "PCAR", "ROP", "ODFL", "CPRT", "DASH", "PAYX", "AEP",
    "FAST", "MRVL", "KDP", "ROST", "DDOG", "VRSK", "EA", "CHTR", "EXC", "CTSH",
    "KHC", "GEHC", "BKR", "LULU", "IDXX", "CCEP", "TTWO", "XEL", "ANSS", "ON",
    "DXCM", "CSGP", "ZS", "TEAM", "FANG", "WBD", "ILMN", "MDB", "WBA", "SIRI",
    "ALGN", "ENPH", "LCID", "RIVN", "ARM", "SMCI", "GFS", "CDW", "BIIB", "MRNA",
)

# --- S&P 500 -----------------------------------------------------------------
# Snapshot, grouped by sector so it can be eyeballed and corrected by hand.
_SP500: tuple[str, ...] = (
    # Information technology
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "ACN", "CSCO",
    "INTU", "TXN", "QCOM", "IBM", "NOW", "AMAT", "ADI", "MU", "LRCX", "KLAC",
    "SNPS", "CDNS", "PANW", "ANET", "APH", "MSI", "ROP", "FTNT", "NXPI", "MCHP",
    "TEL", "GLW", "HPQ", "HPE", "DELL", "STX", "WDC", "NTAP", "KEYS", "TDY",
    "ZBRA", "TER", "SWKS", "QRVO", "MPWR", "ON", "GDDY", "AKAM", "JNPR", "FFIV",
    "CDW", "IT", "EPAM", "CTSH", "PTC", "ANSS", "TYL", "VRSN", "SMCI", "ENPH",
    # Communication services
    "GOOGL", "GOOG", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR",
    "EA", "TTWO", "OMC", "IPG", "LYV", "WBD", "NWSA", "FOXA", "MTCH", "PARA",
    # Consumer discretionary
    "AMZN", "TSLA", "HD", "MCD", "BKNG", "LOW", "TJX", "SBUX", "NKE", "ORLY",
    "CMG", "MAR", "ABNB", "HLT", "GM", "F", "AZO", "ROST", "YUM", "DHI",
    "LEN", "NVR", "PHM", "LVS", "WYNN", "MGM", "RCL", "CCL", "NCLH", "EXPE",
    "DRI", "ULTA", "BBY", "TSCO", "GRMN", "POOL", "WSM", "DECK", "LULU", "KMX",
    # Consumer staples
    "WMT", "PG", "COST", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB",
    "GIS", "KHC", "STZ", "SYY", "KR", "ADM", "HSY", "MKC", "CHD", "CLX",
    "TGT", "DG", "DLTR", "TAP", "CAG", "CPB", "SJM", "HRL", "TSN", "MNST",
    # Health care
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "AMGN", "PFE",
    "ISRG", "BSX", "SYK", "VRTX", "GILD", "ELV", "CI", "MDT", "REGN", "CVS",
    "ZTS", "BDX", "HCA", "MCK", "COR", "EW", "A", "IQV", "IDXX", "DXCM",
    "RMD", "BIIB", "MRNA", "ZBH", "BAX", "HOLX", "STE", "WAT", "MTD", "PODD",
    "ALGN", "CAH", "CNC", "MOH", "UHS", "DVA", "VTRS", "CRL", "TECH", "INCY",
    # Financials
    "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "SPGI",
    "BLK", "C", "SCHW", "CB", "MMC", "PGR", "ICE", "CME", "AON", "USB",
    "PNC", "TFC", "COF", "BK", "AIG", "MET", "PRU", "TRV", "ALL", "AFL",
    "AMP", "DFS", "FITB", "HBAN", "RF", "CFG", "KEY", "MTB", "STT", "NTRS",
    "SYF", "WTW", "BRO", "CINF", "L", "GL", "RJF", "NDAQ", "MSCI", "MCO",
    "FI", "FIS", "GPN", "PYPL", "ERIE", "ACGL", "HIG", "PFG", "IVZ", "BEN",
    # Industrials
    "GE", "CAT", "RTX", "UNP", "HON", "BA", "LMT", "DE", "UPS", "ADP",
    "ETN", "ITW", "NOC", "GD", "CSX", "NSC", "EMR", "PH", "TT", "CMI",
    "FDX", "WM", "RSG", "CARR", "JCI", "PCAR", "GWW", "AME", "ROK", "OTIS",
    "FAST", "ODFL", "VRSK", "EFX", "URI", "PWR", "IR", "DOV", "XYL", "SWK",
    "LHX", "TDG", "HWM", "AXON", "BLDR", "MAS", "ALLE", "PNR", "SNA", "TXT",
    "J", "ACM", "CHRW", "EXPD", "JBHT", "LUV", "DAL", "UAL", "AAL", "CPRT",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "WMB", "OKE",
    "KMI", "HES", "OXY", "DVN", "FANG", "BKR", "HAL", "TRGP", "CTRA", "EQT",
    "APA", "MRO",
    # Utilities
    "NEE", "SO", "DUK", "SRE", "AEP", "D", "EXC", "XEL", "ED", "PEG",
    "WEC", "ES", "AWK", "DTE", "PPL", "AEE", "FE", "CMS", "CNP", "ATO",
    "EIX", "ETR", "NI", "LNT", "EVRG", "PNW", "NRG", "VST", "CEG", "AES",
    # Materials
    "LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DOW", "DD", "PPG", "NUE",
    "VMC", "MLM", "IFF", "LYB", "STLD", "PKG", "IP", "AMCR", "ALB", "CE",
    "EMN", "MOS", "CF", "FMC", "BALL", "AVY", "SW",
    # Real estate
    "PLD", "AMT", "EQIX", "WELL", "SPG", "PSA", "O", "CCI", "DLR", "CBRE",
    "EXR", "AVB", "VTR", "EQR", "IRM", "SBAC", "INVH", "ARE", "MAA", "ESS",
    "UDR", "HST", "KIM", "REG", "FRT", "BXP", "CPT", "DOC",
)


@dataclass(frozen=True)
class Universe:
    """A named candidate pool."""

    key: str
    label: str
    symbols: tuple[str, ...]
    note: str

    def __len__(self) -> int:
        return len(self.symbols)


UNIVERSES: dict[str, Universe] = {
    WATCHLIST_UNIVERSE: Universe(
        key=WATCHLIST_UNIVERSE,
        label="My watchlist",
        symbols=(),  # filled from config at resolve time
        note="Only the tickers you listed. Fastest, and the default.",
    ),
    "sectors": Universe(
        key="sectors",
        label="Sector & index ETFs",
        symbols=_SECTOR_ETFS,
        note="Broad-market, sector and thematic funds. Good for reading rotation.",
    ),
    "leveraged": Universe(
        key="leveraged",
        label="Leveraged & inverse ETFs",
        symbols=_LEVERAGED_ETFS,
        note="Daily-rebalanced leverage. Decays in chop — treat as an expression, not a hold.",
    ),
    "nasdaq100": Universe(
        key="nasdaq100",
        label="Nasdaq-100",
        symbols=_NASDAQ100,
        note="Large-cap growth concentration. Heavy overlap with the S&P 500.",
    ),
    "sp500": Universe(
        key="sp500",
        label="S&P 500 (large-cap core)",
        symbols=_SP500,
        note=(
            "A partial snapshot of the index — the large- and mid-cap core, not "
            "all 500. Essentially all of it has tradeable options. Add anything "
            "missing via extra symbols."
        ),
    ),
    "broad": Universe(
        key="broad",
        label="S&P 500 + Nasdaq-100 + ETFs",
        symbols=tuple(_SP500) + tuple(_NASDAQ100) + tuple(_SECTOR_ETFS) + tuple(_LEVERAGED_ETFS),
        note="Everything with liquid options. The widest scan worth running.",
    ),
}

UNIVERSE_NAMES: tuple[str, ...] = tuple(UNIVERSES)


def resolve(name: str, watchlist: list[str] | None = None,
            extra: list[str] | None = None) -> list[str]:
    """Expand a universe name into a de-duplicated, upper-cased symbol list.

    ``watchlist`` supplies the symbols for the ``watchlist`` universe and is
    ignored otherwise. ``extra`` is appended to whichever universe is chosen,
    which is how a name missing from a static snapshot gets included without
    editing the package.

    Order is stable: universe members first in declaration order, then extras.
    De-duplication keeps the first occurrence, so ``broad`` does not score
    AAPL three times for appearing in three constituent lists.
    """
    if name not in UNIVERSES:
        raise UnknownUniverse(
            f"Unknown universe {name!r}. Available: {', '.join(UNIVERSE_NAMES)}"
        )

    base = list(watchlist or ()) if name == WATCHLIST_UNIVERSE else list(UNIVERSES[name].symbols)

    seen: set[str] = set()
    out: list[str] = []
    for raw in list(base) + list(extra or ()):
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def describe(name: str, watchlist: list[str] | None = None,
             extra: list[str] | None = None) -> dict[str, object]:
    """Metadata for the settings UI: label, note and resolved size."""
    universe = UNIVERSES[name]
    return {
        "key": universe.key,
        "label": universe.label,
        "note": universe.note,
        "size": len(resolve(name, watchlist, extra)),
    }
