"""p3 — Universe builder.

Returns a deduplicated, uppercase, stable-sorted list of tickers from:
  1. Static NASDAQ-100 / S&P-500 seed lists (compact but representative).
  2. Current portfolio holdings read from portfolio_snapshot.json.
  3. ETF / macro proxy tickers.

Extension points (p7+):
  - Replace or augment _NASDAQ100_SEED / _SP500_SEED with live wiki/CBOE sources.
  - Pass a custom snapshot_path for back-testing with historical snapshots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Static seed lists (representative subsets; extend or replace for production)
# ---------------------------------------------------------------------------

_NASDAQ100_SEED: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AVGO", "COST", "NFLX", "AMD", "ADBE", "QCOM", "TXN", "AMAT",
    "INTC", "MU", "LRCX", "SNPS", "CDNS", "ADI", "MRVL", "KLAC",
    "PANW", "CRWD", "FTNT", "ANSS", "TTWO", "EA", "ATVI", "ASML",
    "ADP", "PAYX", "FAST", "ODFL", "CTAS", "MNST", "CPRT", "ORLY",
    "ROST", "DLTR", "KDP", "MDLZ", "FISV", "PYPL", "INTU", "ISRG",
    "IDXX", "DXCM", "BIIB", "AMGN", "GILD", "VRTX", "REGN", "MRNA",
    "CSX", "PCAR", "ZM", "DOCU", "TEAM", "OKTA", "DDOG", "MDB",
    "ZS", "NET", "SNOW", "PLTR", "RBLX", "COIN", "SMCI", "MELI",
    "BKNG", "EXPE", "TRIP", "LYFT", "UBER", "ABNB", "DASH", "WDAY",
    "NOW", "CRM", "ORCL", "SAP", "ADSK", "VRSK", "CSGP", "GEHC",
    "SBUX", "CMCSA", "CHTR", "TMUS", "WBA", "DLNR", "FANG", "CEG",
    "EXC", "XEL", "NXPI", "MCHP",
)

_SP500_SEED: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK.B",
    "LLY", "JPM", "V", "MA", "UNH", "XOM", "TSLA", "PG", "JNJ",
    "HD", "ABBV", "CVX", "MRK", "PEP", "COST", "KO", "BAC", "TMO",
    "WMT", "CRM", "NFLX", "ACN", "ABT", "DHR", "LIN", "MCD", "PM",
    "CSCO", "GE", "IBM", "UPS", "CAT", "BA", "GS", "MS", "RTX",
    "SPGI", "INTU", "SYK", "BLK", "VRTX", "ADI", "ISRG", "AXP",
    "AMGN", "REGN", "GILD", "BSX", "MDT", "MDLZ", "PLD", "AMT",
    "CCI", "EQIX", "WM", "ECL", "ZTS", "IDXX", "ALGN", "RMD",
    "ELV", "CI", "HUM", "MCK", "AIG", "PRU", "AFL", "CB", "AON",
    "MMC", "TRV", "PGR", "ALL", "SHW", "APD", "EMR", "ETN", "HON",
    "ITW", "PH", "ROK", "FTV", "CARR", "OTIS", "LHX", "NOC", "GD",
    "LMT", "DE", "CMI", "PCAR", "FDX", "NSC", "CSX", "UNP", "DAL",
    "UAL", "LUV", "AAL", "EXPD", "NEM", "FCX", "CLF", "STLD",
    "NUE", "CMC", "CF", "MOS", "FMC", "DVN", "MRO", "APA", "HAL",
    "SLB", "BKR", "OXY", "COP", "EOG", "PSX", "VLO", "MPC",
    "DIS", "WBD", "PARA", "FOXA", "FOX", "NWS", "NWSA", "OMC",
    "IPG", "PDD", "BABA", "NIO", "XPEV", "LI", "RIVN",
)

# ETF / macro proxy tickers always included
_MACRO_PROXIES: tuple[str, ...] = (
    "QQQ", "SPY", "IWM", "DIA",        # broad equity
    "SGOV", "SHY", "IEF", "TLT",       # rates / duration
    "GLD", "SLV", "PDBC",              # commodities
    "UUP",                              # USD
    "VIXY", "VXX",                     # volatility
    "HYG", "LQD",                      # credit
    "EEM", "VWO",                      # emerging markets
    "XLK", "XLF", "XLE", "XLV",       # sectors
    "ARKK",                             # thematic
)

# ---------------------------------------------------------------------------
# Portfolio snapshot helpers
# ---------------------------------------------------------------------------

_DEFAULT_SNAPSHOT_PATH = Path(__file__).parent.parent / "portfolio_snapshot.json"


def _tickers_from_snapshot(snapshot_path: str | Path | None = None) -> list[str]:
    """Return all ticker strings found in a portfolio_snapshot.json file."""
    path = Path(snapshot_path) if snapshot_path else _DEFAULT_SNAPSHOT_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    tickers: list[str] = []
    for section_key in ("overseas_general", "overseas_fractional"):
        section = data.get(section_key, {})
        for holding in section.get("holdings_usd", []):
            t = holding.get("ticker", "").strip().upper()
            if t:
                tickers.append(t)
    for holding in data.get("domestic", {}).get("holdings", []):
        t = holding.get("ticker", "").strip().upper()
        if t:
            tickers.append(t)
    return tickers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_universe(
    *,
    snapshot_path: str | Path | None = None,
    extra_tickers: Iterable[str] = (),
    include_nasdaq100: bool = True,
    include_sp500: bool = True,
    include_macro: bool = True,
) -> list[str]:
    """Return a deduplicated, uppercase, sorted universe of tickers.

    Args:
        snapshot_path: Path to portfolio_snapshot.json. Uses project root by default.
        extra_tickers: Additional tickers to include (e.g. from a live index source).
        include_nasdaq100: Whether to include _NASDAQ100_SEED.
        include_sp500: Whether to include _SP500_SEED.
        include_macro: Whether to include _MACRO_PROXIES.

    Extension point: replace or augment _NASDAQ100_SEED / _SP500_SEED by passing
    extra_tickers from a live source (CBOE, Wikipedia, etc.) in p7+.
    """
    pool: set[str] = set()

    if include_nasdaq100:
        pool.update(_NASDAQ100_SEED)
    if include_sp500:
        pool.update(_SP500_SEED)
    if include_macro:
        pool.update(_MACRO_PROXIES)

    pool.update(t.upper() for t in extra_tickers if t.strip())
    pool.update(_tickers_from_snapshot(snapshot_path))

    return sorted(pool)


def nasdaq100_seed() -> tuple[str, ...]:
    """Return the static NASDAQ-100 seed list (read-only)."""
    return _NASDAQ100_SEED


def sp500_seed() -> tuple[str, ...]:
    """Return the static S&P-500 seed list (read-only)."""
    return _SP500_SEED


def macro_proxies() -> tuple[str, ...]:
    """Return the fixed ETF / macro proxy list (read-only)."""
    return _MACRO_PROXIES
