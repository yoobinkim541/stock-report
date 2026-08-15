#!/usr/bin/env python3
"""test_kis_fundamentals.py — KIS 실계좌 종목정보/순위분석 어댑터 (무네트워크·폐형해).

읽기전용 불변 강제 + 라이브 스모크로 확인된 실제 필드명 기반 파서 테스트.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.kis_fundamentals as kf
import providers.kis_quote as kq


# ── 순수 파서 (라이브 확인 필드명 — 005930 실응답 기반) ─────────────────────────

def test_parse_broker_opinions_handles_mixed_english_korean_labels():
    """KIS 는 증권사마다 BUY/매수 등 표기가 섞여 나온다 — 원문 그대로 보존."""
    rows = [
        {"stck_bsop_date": "20260810", "mbcr_name": "키움", "invt_opnn": "BUY",
         "rgbf_invt_opnn": "BUY", "hts_goal_prc": "350000", "stck_prdy_clpr": "231000",
         "dprt": "-21.57"},
        {"stck_bsop_date": "20260731", "mbcr_name": "한국투자", "invt_opnn": "매수",
         "rgbf_invt_opnn": "매수", "hts_goal_prc": "650000", "stck_prdy_clpr": "207000",
         "dprt": "-57.77"},
    ]
    got = kf.parse_broker_opinions(rows)
    assert got[0] == {"date": "20260810", "broker": "키움", "opinion": "BUY",
                      "prev_opinion": "BUY", "target_price": 350000.0,
                      "price_at_opinion": 231000.0, "deviation_pct": -21.57}
    assert got[1]["broker"] == "한국투자" and got[1]["opinion"] == "매수"


def test_parse_broker_opinions_skips_rows_without_broker_or_date():
    assert kf.parse_broker_opinions([{"invt_opnn": "BUY"}]) == []
    assert kf.parse_broker_opinions([]) == []


def test_parse_credit_balance():
    rows = [{"deal_date": "20260812", "stck_prpr": "255500",
             "whol_loan_rmnd_stcn": "23373316", "whol_loan_rmnd_amt": "488211270",
             "whol_loan_rmnd_rate": "0.39"}]
    got = kf.parse_credit_balance(rows)
    assert got == [{"date": "20260812", "price": 255500.0,
                    "loan_balance_shares": 23373316.0,
                    "loan_balance_amount": 488211270.0,
                    "loan_balance_rate_pct": 0.39}]
    assert kf.parse_credit_balance([{"stck_prpr": "1"}]) == []   # 날짜 없으면 스킵


def test_parse_short_sale():
    rows = [{"stck_bsop_date": "20260814", "stck_clpr": "274500",
             "ssts_cntg_qty": "1274200", "ssts_vol_rlim": "5.88",
             "acml_ssts_cntg_qty_rlim": "8.51"}]
    got = kf.parse_short_sale(rows)
    assert got == [{"date": "20260814", "price": 274500.0, "short_qty": 1274200.0,
                    "short_ratio_pct": 5.88, "cumulative_short_ratio_pct": 8.51}]
    assert kf.parse_short_sale([{}]) == []


def test_parse_financial_ratios():
    rows = [{"stac_yymm": "202603", "grs": "69.1600", "bsop_prfi_inrt": "756.1000",
             "ntin_inrt": "474.3200", "roe_val": "19.16", "eps": "6993.00",
             "bps": "71907.00", "rsrv_rate": "50140.0200", "lblt_rate": "30.1500"}]
    got = kf.parse_financial_ratios(rows)
    assert got == [{"period": "202603", "revenue_growth_pct": 69.16,
                    "op_income_growth_pct": 756.1, "net_income_growth_pct": 474.32,
                    "roe_pct": 19.16, "eps": 6993.0, "bps": 71907.0,
                    "reserve_ratio_pct": 50140.02, "debt_ratio_pct": 30.15}]
    assert kf.parse_financial_ratios([{}]) == []


# ── 게이트 / fail-closed ──────────────────────────────────────────────────────

def test_all_functions_disabled_return_none(monkeypatch):
    monkeypatch.delenv("REALTIME_ENABLED", raising=False)
    assert kf.broker_opinions("005930", date_from="20260601", date_to="20260815") is None
    assert kf.credit_balance_trend("005930", date="20260815") is None
    assert kf.short_sale_trend("005930", date_from="20260801", date_to="20260815") is None
    assert kf.financial_ratios("005930") is None


def test_all_functions_fail_closed_no_key_makes_no_http(monkeypatch, tmp_path):
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.delenv("KOREA_API_KEY", raising=False)
    monkeypatch.delenv("KOREA_API_SECRET", raising=False)
    monkeypatch.setattr(kq, "_TOKEN_FILE", str(tmp_path / "none.json"))
    monkeypatch.setattr(kq, "_token_cache", {"token": None, "exp": 0.0})

    def _boom(*a, **k):
        raise AssertionError("네트워크 호출 발생 — fail-closed 위반")
    monkeypatch.setattr(kq.requests, "get", _boom)
    monkeypatch.setattr(kq.requests, "post", _boom)

    assert kf.broker_opinions("005930", date_from="20260601", date_to="20260815") is None
    assert kf.credit_balance_trend("005930", date="20260815") is None
    assert kf.short_sale_trend("005930", date_from="20260801", date_to="20260815") is None
    assert kf.financial_ratios("005930") is None


# ── 읽기전용 구조 불변 (grep) ─────────────────────────────────────────────────

def test_module_has_no_order_path():
    """주문 경로가 소스에 존재하지 않음을 강제 — read-only 보장 (kis_quote 와 동일 관례)."""
    src = open(kf.__file__, encoding="utf-8").read()
    for forbidden in ("place_order", "/trading/order", "ORD_QTY", "OVRS_ORD_UNPR", "hashkey",
                      "VTTT", "TTTC", "kt10000", "kt10001", "requests.post"):
        assert forbidden not in src, f"읽기전용 위반: '{forbidden}' 발견"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
