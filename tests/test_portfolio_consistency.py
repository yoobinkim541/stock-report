#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""포트폴리오 단일 소스·죽은 티커 감사 테스트 (portfolio_universe.py)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio_universe as pu


def test_no_dead_ticker_mentions():
    """체크아웃된 소스 트리에 은퇴 티커 언급이 없어야 한다."""
    mentions = pu.find_dead_ticker_mentions()
    assert mentions == [], "은퇴 티커 잔존 참조:\n" + "\n".join(mentions)


def test_load_portfolio_tickers_from_snapshot(tmp_path):
    snap = {
        "overseas_general": {"holdings_usd": [
            {"ticker": "ORCL", "shares": 2, "value_usd": 500.0},
            {"ticker": "ZERO", "shares": 0, "value_usd": 0},
        ]},
        "overseas_fractional": {"holdings": [
            {"ticker": "NVDA", "shares": 0.5, "value_usd": 90.0},
        ]},
    }
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap), encoding="utf-8")
    assert pu.load_portfolio_tickers(str(p)) == ["ORCL", "NVDA"]


def test_load_portfolio_tickers_fallback(tmp_path):
    missing = str(tmp_path / "none.json")
    assert pu.load_portfolio_tickers(missing) == pu.DEFAULT_PORTFOLIO_TICKERS


def test_record_and_load_retired(tmp_path):
    retired_file = str(tmp_path / "retired.json")
    snap_missing = str(tmp_path / "none.json")  # 보유 = DEFAULT 폴백

    pu.record_retired_ticker("cpng", path=retired_file)
    pu.record_retired_ticker("XYZ", path=retired_file)
    data = json.load(open(retired_file, encoding="utf-8"))
    assert set(data) == {"CPNG", "XYZ"}

    retired = pu.load_retired_tickers(snapshot_path=snap_missing,
                                      retired_path=retired_file)
    assert {"CPNG", "XYZ"} <= retired
    # 현재 보유 종목은 기록돼 있어도 은퇴로 치지 않는다 (재매수 복귀)
    pu.record_retired_ticker("MSFT", path=retired_file)
    retired = pu.load_retired_tickers(snapshot_path=snap_missing,
                                      retired_path=retired_file)
    assert "MSFT" not in retired


def test_audit_detects_planted_mention(tmp_path):
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text(
        'TICKERS = ["CPNG", "MSFT"]\n'
        'ALSO = "CPNG"  # ticker-ok 의도적 언급\n', encoding="utf-8")
    mentions = pu.find_dead_ticker_mentions(str(src_dir), retired={"CPNG"})
    assert len(mentions) == 1 and "foo.py:1" in mentions[0]
