"""tests/test_congress_trading.py — 미 하원의원 주식거래 공시(STOCK Act PTR) 조회.

House Clerk PDF 를 매일 파싱해 공개하는 GitHub 저장소(TattooedHead/house-stock-
watcher-data)에서 읽는다. 금액은 구간(bracket)으로만 공시(정확 액수 아님) — 무네트워크
순수 로직만 테스트(디스크 캐시 seam 을 직접 채워서 검증, 상원은 미포함 — 감사 후속).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import congress_trading as ct  # noqa: E402

_FIXTURE = [
    {"representative": "Nancy Pelosi", "transaction_date": "01/16/2026", "disclosure_date": "01/20/2026",
     "ticker": "GOOGL", "asset_description": "Alphabet Inc.", "asset_type": "Stock", "type": "Purchase",
     "amount": "$500,001 - $1,000,000", "amount_mid": 750000, "owner": "Spouse",
     "source_url": "https://disclosures-clerk.house.gov/x1.pdf"},
    {"representative": "Nancy Pelosi", "transaction_date": "12/30/2025", "disclosure_date": "01/05/2026",
     "ticker": "DIS", "asset_description": "Walt Disney Co", "asset_type": "Stock", "type": "Sale",
     "amount": "$1,000,001 - $5,000,000", "amount_mid": 3000000, "owner": "Spouse",
     "source_url": "https://disclosures-clerk.house.gov/x2.pdf"},
    {"representative": "Nancy Pelosi", "transaction_date": "06/20/2025", "disclosure_date": "06/25/2025",
     "ticker": "AVGO", "asset_description": "Broadcom Inc", "asset_type": "Stock", "type": "Purchase",
     "amount": "$1,000,001 - $5,000,000", "amount_mid": 3000000, "owner": "Spouse",
     "source_url": "https://disclosures-clerk.house.gov/x3.pdf"},
    {"representative": "Mike Kelly", "transaction_date": "07/17/2026", "disclosure_date": "08/12/2026",
     "ticker": "ABT", "asset_description": "Abbott Laboratories", "asset_type": "Stock", "type": "Sale",
     "amount": "$1,001 - $15,000", "amount_mid": 8000, "owner": "Spouse",
     "source_url": "https://disclosures-clerk.house.gov/x4.pdf"},
]


def test_member_transactions_filters_case_insensitive_partial_and_sorts_desc(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: _FIXTURE)

    out = ct.member_transactions("pelosi")

    assert [r["ticker"] for r in out] == ["GOOGL", "DIS", "AVGO"], "최신순 정렬 안 됨"
    assert out[0]["date"] == "01/16/2026"
    assert out[0]["amount"] == "$500,001 - $1,000,000"
    assert out[0]["source_url"] == "https://disclosures-clerk.house.gov/x1.pdf"


def test_member_transactions_respects_limit(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: _FIXTURE)

    out = ct.member_transactions("Pelosi", limit=2)

    assert len(out) == 2
    assert [r["ticker"] for r in out] == ["GOOGL", "DIS"]


def test_member_transactions_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: _FIXTURE)
    assert ct.member_transactions("Nonexistent Person") is None


def test_member_transactions_returns_none_for_blank_name(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: _FIXTURE)
    assert ct.member_transactions("") is None
    assert ct.member_transactions("   ") is None
    assert ct.member_transactions(None) is None


def test_member_transactions_graceful_when_source_unavailable(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: [])
    assert ct.member_transactions("Pelosi") is None


def test_list_members_dedupes_and_sorts(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: _FIXTURE)
    assert ct.list_members() == ["Mike Kelly", "Nancy Pelosi"]


def test_load_all_caches_to_disk_and_reuses_fresh_cache(monkeypatch, tmp_path):
    """캐시가 신선하면 재다운로드 없이 디스크에서 읽는다."""
    monkeypatch.setattr(ct, "_CACHE_DIR", tmp_path)
    (tmp_path / "house_all_transactions.json").write_text(json.dumps(_FIXTURE), encoding="utf-8")

    calls = []
    monkeypatch.setattr(ct, "_get", lambda url: (calls.append(url), b"[]")[1])
    monkeypatch.setattr(ct, "_CACHE_TTL_H", 999999)   # 방금 썼으니 무조건 신선

    out = ct._load_all()

    assert calls == []
    assert len(out) == len(_FIXTURE)


def test_top_traded_groups_by_ticker_and_ranks_by_member_count(monkeypatch):
    """감사 후속 — 유빈님 요청: 정치인들이 많이 매수·매도한 종목 리스트업."""
    from datetime import datetime, timedelta
    today = datetime.now()
    recent = (today - timedelta(days=10)).strftime("%m/%d/%Y")
    old = (today - timedelta(days=200)).strftime("%m/%d/%Y")

    rows = [
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "NVDA",
         "type": "Purchase", "amount_mid": 100000},
        {"representative": "Mike Kelly", "transaction_date": recent, "ticker": "NVDA",
         "type": "Purchase", "amount_mid": 20000},
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "GOOGL",
         "type": "Purchase", "amount_mid": 50000},
        {"representative": "Nancy Pelosi", "transaction_date": old, "ticker": "AMZN",
         "type": "Purchase", "amount_mid": 999999},   # 기간 밖 — 제외
        {"representative": "Mike Kelly", "transaction_date": recent, "ticker": "ABT",
         "type": "Sale", "amount_mid": 8000},
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "--",
         "type": "Purchase", "amount_mid": 1000},   # 티커 없음 — 제외
        {"representative": "Nancy Pelosi", "transaction_date": recent, "ticker": "VSNT",
         "type": "Exchange", "amount_mid": 15},   # 교환 — 제외
    ]
    monkeypatch.setattr(ct, "_load_all", lambda: rows)

    out = ct.top_traded(days=90)

    bought_tickers = [r["ticker"] for r in out["bought"]]
    assert bought_tickers[0] == "NVDA"
    nvda = out["bought"][0]
    assert nvda["member_count"] == 2
    assert set(nvda["members"]) == {"Nancy Pelosi", "Mike Kelly"}
    assert "AMZN" not in bought_tickers
    assert "--" not in bought_tickers

    sold_tickers = [r["ticker"] for r in out["sold"]]
    assert sold_tickers == ["ABT"]


def test_top_traded_respects_limit(monkeypatch):
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(days=5)).strftime("%m/%d/%Y")
    rows = [{"representative": f"Member {i}", "transaction_date": recent, "ticker": f"T{i}",
            "type": "Purchase", "amount_mid": 1000} for i in range(15)]
    monkeypatch.setattr(ct, "_load_all", lambda: rows)

    out = ct.top_traded(days=90, limit=3)

    assert len(out["bought"]) == 3


def test_top_traded_empty_when_source_unavailable(monkeypatch):
    monkeypatch.setattr(ct, "_load_all", lambda: [])
    out = ct.top_traded(days=90)
    assert out == {"bought": [], "sold": []}


def test_load_all_graceful_on_network_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_CACHE_DIR", tmp_path)

    def _boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(ct, "_get", _boom)

    assert ct._load_all() == []
