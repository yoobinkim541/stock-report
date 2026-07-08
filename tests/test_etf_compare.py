"""etf_meta·providers/etf_compare 순수부 단위 테스트 (무네트워크).

시드 무결성·TR/PR 추출·수익률 창·지표·백분위·점수(전략별/엣지케이스).
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import etf_meta  # noqa: E402
from providers import etf_compare as ec  # noqa: E402


# ── 시드 무결성 ────────────────────────────────────────────────────────
def test_groups_shape_no_dup():
    seen = set()
    for key, g in etf_meta.ETF_GROUPS.items():
        assert g["strategy"] in ("index", "covered_call", "dividend"), key
        if g["strategy"] == "index":
            assert g["bench"] in g["etfs"], f"{key}: index 그룹 bench 는 멤버여야"
        else:                                   # 인컴 전략 — 벤치=기초지수 프록시(타 그룹 소속)
            assert g["bench"] in etf_meta.TICKER_GROUP, f"{key}: bench 미등록"
        for t in g["etfs"]:
            assert t not in seen, f"{t} 그룹 중복"
            seen.add(t)


def test_group_of_and_peers():
    assert etf_meta.group_of("QQQM") == "nasdaq100"
    assert etf_meta.group_of("069500") == "kr_kospi200"          # KR 코드 정규화
    assert etf_meta.group_of("A069500") == "kr_kospi200"
    assert etf_meta.group_of("MSFT") is None
    assert etf_meta.peers_of("QQQ") == ["QQQM"]
    assert etf_meta.peers_of("MSFT") == []
    assert "QQQI" in etf_meta.ETF_GROUPS["ndx_covered_call"]["etfs"]   # 보유 커버드콜


def test_known_etfs_union():
    from providers.etf_data import is_etf
    assert is_etf("QQQM") and is_etf("SPLG") and is_etf("GPIQ")  # 시드 union 오프라인 감지


# ── TR/PR 추출 ─────────────────────────────────────────────────────────
def _flat_df(n=50, tr_mult=1.5):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = pd.Series([100.0 + i for i in range(n)], index=idx)
    return pd.DataFrame({"Close": close, "Adj Close": close * tr_mult,
                         "Volume": [1000.0] * n}, index=idx)


def test_extract_tr_pr_flat_and_multiindex():
    df = _flat_df()
    tr, pr = ec._extract_tr_pr(df, "QQQ")
    assert float(tr.iloc[0]) == 150.0 and float(pr.iloc[0]) == 100.0
    mi = pd.concat({"QQQ": df}, axis=1)                          # group_by="ticker" 형상
    tr2, pr2 = ec._extract_tr_pr(mi, "QQQ")
    assert float(tr2.iloc[-1]) == float(tr.iloc[-1])
    assert ec._extract_tr_pr(None, "X") is None
    assert ec._extract_tr_pr(df.iloc[:1], "X") is None           # 2봉 미만


# ── 수익률 창·연율화 ────────────────────────────────────────────────────
def test_window_return_anchor_and_coverage():
    idx = pd.date_range("2024-01-01", periods=730, freq="D")
    s = pd.Series(range(100, 830), index=idx, dtype=float)
    r = ec.window_return(s, 365)
    anchor_val = float(s[s.index >= s.index[-1] - pd.Timedelta(days=365)].iloc[0])
    assert r == pytest.approx((float(s.iloc[-1]) / anchor_val - 1) * 100)
    short = s.iloc[-100:]                                        # 100일 < 0.6×365
    assert ec.window_return(short, 365) is None
    assert ec.ann_return(21.0, 2.0) == pytest.approx(10.0, abs=0.1)   # (1.21)^0.5−1
    assert ec.ann_return(None, 3.0) is None


# ── 지표 조립 ───────────────────────────────────────────────────────────
def _synthetic_group():
    idx = pd.date_range("2022-01-01", periods=1100, freq="D")
    n = len(idx)

    def frame(daily, div_drag=0.0):
        pr = pd.Series([100.0 * (1 + daily) ** i for i in range(n)], index=idx)
        tr = pr * pd.Series([(1 + div_drag) ** i for i in range(n)], index=idx)
        return pd.DataFrame({"Close": pr, "Adj Close": tr, "Volume": [1e6] * n})

    group = {"name": "t", "strategy": "index", "bench": "AAA",
             "etfs": ["AAA", "BBB", "CCC"]}
    prices = {"AAA": frame(0.0004), "BBB": frame(0.00042), "CCC": frame(0.0003)}
    extras = {
        "AAA": {"expense_ratio": 0.0009, "total_assets": 5e11,
                "dividends": {"yield_pct": 0.6, "count_12m": 4}},
        "BBB": {"expense_ratio": 0.0003, "total_assets": 4e10,
                "dividends": {"yield_pct": 0.6, "count_12m": 4}},
        "CCC": {"expense_ratio": 0.002, "total_assets": 1e9,
                "dividends": {"yield_pct": 0.3, "count_12m": 1}},
    }
    return group, prices, extras


def test_compute_metrics_synthetic():
    group, prices, extras = _synthetic_group()
    rows = ec.compute_metrics(prices, group, extras)
    by = {r["ticker"]: r for r in rows}
    assert by["AAA"]["tracking_diff"] == pytest.approx(0.0, abs=1e-9)   # 벤치 자신 = 0
    assert by["BBB"]["tracking_diff"] > 0 > by["CCC"]["tracking_diff"]
    assert by["BBB"]["tr_1y"] > by["CCC"]["tr_1y"]
    assert by["AAA"]["history_years"] == pytest.approx(3.0, abs=0.1)
    assert by["AAA"]["mdd"] is not None and by["AAA"]["mdd"] >= 0
    assert by["AAA"]["avg_dollar_vol"] and by["AAA"]["avg_dollar_vol"] > 0
    assert by["AAA"]["expense_ratio"] == 0.0009                  # extras 병합


# ── 백분위·점수 ─────────────────────────────────────────────────────────
def test_percentile_rank():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert ec.percentile_rank(vals, 4.0) == pytest.approx((3 + 0.5) / 4)
    assert ec.percentile_rank(vals, 1.0, higher_better=False) == pytest.approx(3.5 / 4)
    assert ec.percentile_rank([5.0, 5.0], 5.0) == 0.5            # 전원 동률
    assert ec.percentile_rank([7.0], 7.0) == 0.5                 # n==1
    assert ec.percentile_rank(vals, None) is None


def test_etf_score_index_cheap_tracker_wins():
    group, prices, extras = _synthetic_group()
    rows = ec.compute_metrics(prices, group, extras)
    by = {r["ticker"]: r for r in rows}
    s_bbb = ec.etf_score(by["BBB"], rows, "index")
    s_ccc = ec.etf_score(by["CCC"], rows, "index")
    assert 1 <= s_ccc["score"] < s_bbb["score"] <= 100           # 저비용+초과추적 상위
    assert set(s_bbb["components"]) == {"비용", "성과", "추적", "리스크", "유동성"}
    assert s_bbb["basis"] == "3y" and s_bbb["n_peers"] == 3


def test_etf_score_covered_call_income_replaces_tracking():
    group, prices, extras = _synthetic_group()
    rows = ec.compute_metrics(prices, group, extras)
    s = ec.etf_score(rows[0], rows, "covered_call")
    assert "인컴" in s["components"] and "추적" not in s["components"]


def test_etf_score_missing_renormalize_and_insufficient():
    group, prices, extras = _synthetic_group()
    rows = ec.compute_metrics(prices, group, extras)
    r = dict(rows[1], expense_ratio=None)                        # 보수 결측 → 재정규화
    s = ec.etf_score(r, rows, "index")
    assert s["score"] is not None and s["components"]["비용"] is None
    bare = {"ticker": "X", "expense_ratio": 0.001, "history_years": 0.2}   # 성과/리스크 없음
    s2 = ec.etf_score(bare, rows, "index")
    assert s2["score"] is None                                   # 가용 가중치 <50 — 정직 생략


def test_etf_score_small_group_shrinks():
    group, prices, extras = _synthetic_group()
    rows = ec.compute_metrics(prices, group, extras)
    two = rows[:2]
    s = ec.etf_score(two[1], two, "index")
    assert s["low_confidence"] is True
    assert abs(s["score"] - 50) <= 20                            # shrink=1/3 → 50 근방


def test_covered_call_external_bench_gap():
    """벤치가 그룹 밖(기초지수 프록시)이어도 prices 주입으로 tracking_diff 계산."""
    group, prices, extras = _synthetic_group()
    cc = {"name": "cc", "strategy": "covered_call", "bench": "AAA",
          "etfs": ["BBB", "CCC"]}                          # AAA(벤치)는 멤버 아님
    rows = ec.compute_metrics(prices, cc, extras)          # prices 에 AAA 포함
    by = {r["ticker"]: r for r in rows}
    assert by["BBB"]["tracking_diff"] > 0 > by["CCC"]["tracking_diff"]
