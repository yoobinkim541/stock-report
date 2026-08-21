"""tests/test_sweet_spot_benchmark_window.py — ML vs 벤치마크 구간 정합 (무네트워크·순수).

감사 후속(2026-08-21): 노션 "ML 성과" 표가 **서로 다른 기간의 수치를 나란히** 놓고 있었다.

    ["ML (nested OOS)", ml.cagr, ..., f"{ml.n_days}일"]   ← OOS 구간만 (126일)
    ["QQQ 보유",        qqq.cagr, ..., "벤치마크"]         ← 전체 구간 (756일)

ML 은 학습분(앞 2/3)을 제외한 OOS 구간에서만 평가되는데, 벤치마크는 전체 기간
CAGR 이라 비교 자체가 성립하지 않는다. 실측(2026-08-21):

    ML(OOS 126일)      CAGR  5.20%  Sharpe 0.13
    QQQ(같은 126일)    CAGR 42.86%  Sharpe 1.47   ← 올바른 비교 대상
    QQQ(전체 756일)    CAGR 26.46%  Sharpe 1.05   ← 표에 표시되던 값

표시값(26.46%)이 실제 동일구간(42.86%)보다 낮아 **ML 이 실제보다 좋아 보였다**.
OOS 창이 하락장에 걸리면 반대로 ML 이 우세한 것처럼 보일 수도 있다 — 어느 쪽이든
방향이 창에 따라 뒤집히는 무의미한 비교다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pd = pytest.importorskip("pandas")

from ml.sweet_spot import SweetSpotResult, oos_aligned_benchmarks  # noqa: E402


def _mk_equity():
    idx = pd.date_range("2026-01-01", periods=10, freq="B")
    return pd.DataFrame({
        "ML_model": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        "overlay":  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
        "threshold": [100] * 10,
        "SPY":      [100, 102, 104, 106, 108, 110, 112, 114, 116, 118],
        "QQQ":      [100, 103, 106, 109, 112, 115, 118, 121, 124, 127],
    }, index=idx)


def test_returns_benchmarks_over_ml_window():
    """벤치마크는 ML 이 실제 평가된 구간(equity 인덱스)으로 계산돼야 한다."""
    out = oos_aligned_benchmarks(_mk_equity())
    assert set(out) >= {"QQQ", "SPY"}
    # QQQ 100 → 127 (+27%), ML 100 → 109 (+9%) — 같은 10 영업일 기준
    assert out["QQQ"].cumulative_return == pytest.approx(0.27, abs=1e-6)
    assert out["SPY"].cumulative_return == pytest.approx(0.18, abs=1e-6)


def test_benchmark_window_matches_ml_length():
    eq = _mk_equity()
    out = oos_aligned_benchmarks(eq)
    assert out["QQQ"].n_days == len(eq)


def test_ignores_leading_nan_from_in_sample_period():
    """ML 인샘플 구간(NaN)은 제외하고 벤치마크를 맞춰야 한다."""
    eq = _mk_equity()
    eq.loc[eq.index[:4], "ML_model"] = float("nan")
    out = oos_aligned_benchmarks(eq)
    assert out["QQQ"].n_days == 6          # NaN 4개 제외


def test_graceful_on_empty_or_missing_columns():
    assert oos_aligned_benchmarks(pd.DataFrame()) == {}
    assert oos_aligned_benchmarks(None) == {}
    only_ml = pd.DataFrame({"ML_model": [100, 101]},
                           index=pd.date_range("2026-01-01", periods=2, freq="B"))
    assert oos_aligned_benchmarks(only_ml) == {}


def test_result_dataclass_exposes_aligned_field():
    """SweetSpotResult 가 정합 벤치마크를 실어 나를 수 있어야 한다(소비자 공통 사용)."""
    assert "qqq_oos_result" in SweetSpotResult.__dataclass_fields__
