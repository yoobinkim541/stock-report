"""p4 — Data source layer skeleton.

Provides deterministic interfaces for price history and macro data with:
  - Fallback order: Stooq → Yahoo Finance (both injectable for tests).
  - Source-cache path helpers (news JSONL from source_collector).
  - As-of date handling (all fetchers accept an `as_of` parameter).
  - Placeholder interfaces for FRED, CBOE Put-Call, Fear & Greed, MSCI ETF proxy,
    and news source-cache. Placeholders return empty DataFrames or raise
    NotImplementedError only when explicitly called — tests never need network.

Network calls are injected via `_fetch_stooq` / `_fetch_yahoo` module-level
overrides so unit tests can monkeypatch them without touching os/network.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Callable, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Configurable fetch backends (replace in tests via monkeypatch)
# ---------------------------------------------------------------------------

def _fetch_stooq(ticker: str, start: str, end: str) -> pd.DataFrame:  # pragma: no cover
    """Fetch OHLCV from Stooq (network). Returns empty DataFrame on failure."""
    try:
        url = (
            f"https://stooq.com/q/d/l/?s={ticker.lower()}.us"
            f"&d1={start.replace('-', '')}&d2={end.replace('-', '')}&i=d"
        )
        df = pd.read_csv(url, parse_dates=["Date"], index_col="Date")
        df.columns = [c.lower() for c in df.columns]
        return df.sort_index()
    except Exception:
        return pd.DataFrame()


def _fetch_yahoo(ticker: str, start: str, end: str) -> pd.DataFrame:  # pragma: no cover
    """Fetch OHLCV from Yahoo Finance via pandas_datareader or yfinance."""
    try:
        import yfinance as yf  # type: ignore
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        df.columns = [c.lower() for c in df.columns]
        return df.sort_index()
    except Exception:
        pass
    try:
        import pandas_datareader as pdr  # type: ignore
        df = pdr.get_data_yahoo(ticker, start=start, end=end)
        df.columns = [c.lower() for c in df.columns]
        return df.sort_index()
    except Exception:
        return pd.DataFrame()


# Allow tests to swap these out without patching builtins
_stooq_fetcher: Callable[[str, str, str], pd.DataFrame] = _fetch_stooq
_yahoo_fetcher: Callable[[str, str, str], pd.DataFrame] = _fetch_yahoo


# ---------------------------------------------------------------------------
# Source-cache path helpers
# ---------------------------------------------------------------------------

_PROJECT_DIR = Path(os.environ.get("STOCK_REPORT_PROJECT_DIR", Path(__file__).parent.parent))
_REPORTS_DIR = Path.home() / "reports"
_SOURCE_CACHE_DIR = _REPORTS_DIR / "source-cache"


def source_cache_dir() -> Path:
    """Return the source-cache directory (~/reports/source-cache/)."""
    return _SOURCE_CACHE_DIR


def source_cache_files(ticker: str | None = None, date: str | None = None) -> list[Path]:
    """Return JSONL cache files, optionally filtered by ticker and/or date prefix."""
    if not _SOURCE_CACHE_DIR.exists():
        return []
    pattern = "*.jsonl"
    if date:
        pattern = f"{date}*.jsonl"
    files = list(_SOURCE_CACHE_DIR.glob(pattern))
    if ticker:
        t = ticker.upper()
        files = [f for f in files if t in f.name.upper()]
    return sorted(files)


# ---------------------------------------------------------------------------
# Core price fetcher with Stooq → Yahoo fallback
# ---------------------------------------------------------------------------

def _default_end() -> str:
    return dt.date.today().isoformat()


def fetch_price_history(
    ticker: str,
    start: str = "2020-01-01",
    end: str | None = None,
    as_of: str | None = None,
) -> pd.DataFrame:
    """Return daily OHLCV DataFrame for *ticker* with Stooq → Yahoo fallback.

    Args:
        ticker: Uppercase ticker symbol.
        start: ISO date string for the beginning of the history window.
        end: ISO date string for the end (defaults to today).
        as_of: If provided, clips the returned data to this date (inclusive).
               Use this to enforce point-in-time correctness.

    Returns:
        DataFrame indexed by date with lowercase columns (open, high, low, close, volume).
        Empty DataFrame if both sources fail.
    """
    end_date = end or _default_end()
    df = _stooq_fetcher(ticker, start, end_date)
    if df.empty:
        df = _yahoo_fetcher(ticker, start, end_date)

    if df.empty:
        return df

    # Normalise index to date (not datetime)
    if hasattr(df.index, "normalize"):
        df.index = df.index.normalize()
    df.index = pd.to_datetime(df.index).normalize()

    if as_of:
        cutoff = pd.Timestamp(as_of)
        df = df[df.index <= cutoff]

    return df


def fetch_close(
    ticker: str,
    start: str = "2020-01-01",
    end: str | None = None,
    as_of: str | None = None,
) -> pd.Series:
    """Convenience wrapper — returns the 'close' Series only."""
    df = fetch_price_history(ticker, start=start, end=end, as_of=as_of)
    if df.empty or "close" not in df.columns:
        return pd.Series(dtype=float, name=ticker)
    return df["close"].rename(ticker)


# ---------------------------------------------------------------------------
# Macro / alternative data placeholders
# The functions below are declared so callers can import them and tests can
# stub them out; they raise NotImplementedError when actually called without
# a network/key, so tests must monkeypatch before invoking.
# ---------------------------------------------------------------------------

class _Placeholder:
    """Descriptor that raises NotImplementedError with a helpful message."""
    def __init__(self, name: str, note: str = ""):
        self._name = name
        self._note = note

    def __call__(self, *args, **kwargs) -> pd.DataFrame:  # noqa: ANN001
        raise NotImplementedError(
            f"{self._name} is not implemented yet. "
            f"{self._note} "
            "Monkeypatch this function in tests or implement in a later pass."
        )


def fetch_fred_series(series_id: str, start: str = "2020-01-01", as_of: str | None = None) -> pd.Series:
    """Fetch a FRED economic time series (e.g. 'DFF', 'T10Y2Y').

    Returns pd.Series indexed by date. Requires FRED API access.
    Placeholder — raises NotImplementedError if not monkeypatched.
    """
    raise NotImplementedError(
        "fetch_fred_series requires network + FRED API. "
        "Monkeypatch in tests or implement via pandas_datareader/fredapi in p7+."
    )


def fetch_cboe_putcall(start: str = "2020-01-01", as_of: str | None = None) -> pd.Series:
    """Fetch CBOE equity put/call ratio history.

    Returns pd.Series indexed by date. Placeholder — requires network.
    """
    raise NotImplementedError(
        "fetch_cboe_putcall requires network access to CBOE data. "
        "Implement via direct CSV download or a data vendor in p7+."
    )


def fetch_fear_greed(start: str = "2020-01-01", as_of: str | None = None) -> pd.Series:
    """Fetch CNN Fear & Greed index or a proxy (e.g. via alternative-data API).

    Returns pd.Series indexed by date. Placeholder — requires network.
    """
    raise NotImplementedError(
        "fetch_fear_greed requires a data provider (CNN API / alternative-data). "
        "Implement or use VIXY/VIX as proxy in p7+."
    )


def fetch_msci_proxy(region: str = "world", start: str = "2020-01-01", as_of: str | None = None) -> pd.DataFrame:
    """Return MSCI index proxy via ETF prices (e.g. ACWI for World, EEM for EM).

    Placeholder — delegates to fetch_price_history for the chosen proxy ETF.
    Raises NotImplementedError to signal this is not yet wired.
    """
    raise NotImplementedError(
        "fetch_msci_proxy is not yet wired. "
        "In p7+ use fetch_price_history('ACWI') for World, fetch_price_history('EEM') for EM."
    )


def fetch_news_features(
    ticker: str,
    start: str = "2020-01-01",
    as_of: str | None = None,
) -> pd.DataFrame:
    """Aggregate news features (count, sentiment, theme, event) from source-cache JSONL.

    Returns an empty DataFrame if no cache files are found (safe for tests).
    Full implementation reads ~/reports/source-cache/*.jsonl and aggregates by date.
    """
    files = source_cache_files(ticker=ticker)
    if not files:
        return pd.DataFrame(columns=["date", "ticker", "count", "sentiment", "theme", "event"])

    # p7+: parse JSONL files and aggregate by date
    raise NotImplementedError(
        "fetch_news_features: JSONL parsing not yet implemented. "
        "Implement aggregation in p7+ or monkeypatch in tests."
    )
