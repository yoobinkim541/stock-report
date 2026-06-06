"""Tests for ml/universe.py (p3 — Universe builder)."""

import json
import sys
import os
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.universe import (
    build_universe,
    nasdaq100_seed,
    sp500_seed,
    macro_proxies,
    _tickers_from_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(holdings: list[dict], path: Path) -> None:
    """Write a minimal portfolio_snapshot.json for testing."""
    data = {
        "snapshot_date": "2026-06-06",
        "overseas_general": {
            "holdings_usd": holdings,
        },
        "overseas_fractional": {"holdings_usd": []},
        "domestic": {"holdings": []},
    }
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_seeds_are_nonempty():
    assert len(nasdaq100_seed()) > 50
    assert len(sp500_seed()) > 50
    assert len(macro_proxies()) >= 9


def test_seeds_are_uppercase():
    for t in nasdaq100_seed():
        assert t == t.upper(), f"Not uppercase: {t}"
    for t in sp500_seed():
        assert t == t.upper(), f"Not uppercase: {t}"
    for t in macro_proxies():
        assert t == t.upper(), f"Not uppercase: {t}"


def test_universe_sorted():
    u = build_universe()
    assert u == sorted(u), "Universe must be stably sorted"


def test_universe_deduplicated():
    u = build_universe()
    assert len(u) == len(set(u)), "Universe must have no duplicates"


def test_universe_includes_macro_proxies():
    u = build_universe()
    for ticker in ("QQQ", "SPY", "SGOV", "TLT", "GLD", "UUP", "VIXY"):
        assert ticker in u, f"Macro proxy {ticker!r} missing from universe"


def test_universe_includes_portfolio_holdings(tmp_path):
    snapshot = tmp_path / "portfolio_snapshot.json"
    _make_snapshot(
        [
            {"ticker": "SGOV", "name": "T-Bill ETF", "shares": 20},
            {"ticker": "QQQI", "name": "Nasdaq HY", "shares": 35},
            {"ticker": "NVDA", "name": "Nvidia", "shares": 2},
        ],
        snapshot,
    )
    u = build_universe(snapshot_path=snapshot)
    for t in ("SGOV", "QQQI", "NVDA"):
        assert t in u, f"{t} from snapshot missing in universe"


def test_universe_now_crm_absent_when_not_in_snapshot(tmp_path):
    """NOW and CRM must not appear if they're absent from the portfolio snapshot
    and we use seed-only=False (disable seeds so only snapshot+macro matters)."""
    snapshot = tmp_path / "portfolio_snapshot.json"
    _make_snapshot(
        [{"ticker": "SGOV", "name": "T-Bill ETF", "shares": 20}],
        snapshot,
    )
    u = build_universe(
        snapshot_path=snapshot,
        include_nasdaq100=False,
        include_sp500=False,
        include_macro=True,
    )
    # NOW and CRM are not in macro_proxies and not in this snapshot
    assert "NOW" not in u, "NOW should be absent when not in snapshot and seeds disabled"
    assert "CRM" not in u, "CRM should be absent when not in snapshot and seeds disabled"


def test_universe_sgov_present_even_without_seeds(tmp_path):
    """SGOV must appear via macro proxies even when seeds are disabled."""
    snapshot = tmp_path / "portfolio_snapshot.json"
    # Empty snapshot
    _make_snapshot([], snapshot)
    u = build_universe(
        snapshot_path=snapshot,
        include_nasdaq100=False,
        include_sp500=False,
        include_macro=True,
    )
    assert "SGOV" in u


def test_extra_tickers_included():
    u = build_universe(extra_tickers=["ZZZZ", "AAAA"])
    assert "ZZZZ" in u
    assert "AAAA" in u


def test_extra_tickers_lowercased_input():
    u = build_universe(extra_tickers=["qqq", "spy"])
    assert "QQQ" in u
    assert "SPY" in u


def test_tickers_from_missing_snapshot_returns_empty(tmp_path):
    result = _tickers_from_snapshot(tmp_path / "nonexistent.json")
    assert result == []


def test_tickers_from_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json {{{{")
    result = _tickers_from_snapshot(bad)
    assert result == []


def test_build_universe_no_seeds_no_macro():
    """With everything disabled, only extra_tickers appear."""
    u = build_universe(
        snapshot_path=Path("/tmp/nonexistent-stock-snapshot.json"),
        include_nasdaq100=False,
        include_sp500=False,
        include_macro=False,
        extra_tickers=["FAKE"],
    )
    assert u == ["FAKE"]
