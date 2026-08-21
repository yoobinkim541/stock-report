"""tests/test_mock_maturation_tz.py — 보상 백필 tz 정합 (무네트워크·순수).

감사 후속(2026-08-21): 모의 결정이 20거래일(≈28일)을 훨씬 넘겨도 성숙하지 않는
사례가 양 시장에서 다수 발견됐다(US INTC 52일·PEP 52일·ADBE 44일, KR 028260 45일).

근본 원인: `_default_price_fn` 이 종목·벤치마크 시리즈를 `index.intersection()` 으로
공통 거래일 정렬하는데, 캐시 시점에 따라 한쪽은 tz-naive·다른 쪽은 tz-aware 로
저장돼 있어 **교집합이 0** 이 된다(실측: INTC tz=None vs QQQ tz=America/New_York
→ 교집합 0). 그러면 `len(common) <= horizon` 이 True → None 반환 → 영원히 미성숙.
비결정적(캐시 재작성 타이밍 의존)이라 일부만 성숙해 더 알아채기 어려웠다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pd = pytest.importorskip("pandas")

from ohlc_utils import align_common_index  # noqa: E402


def _s(start, n, tz=None, step=1.0):
    idx = pd.date_range(start, periods=n, freq="B", tz=tz)
    return pd.Series([100.0 + i * step for i in range(n)], index=idx)


def test_aligns_naive_and_aware_indexes():
    """핵심 회귀 — 한쪽만 tz 가 있어도 공통 거래일이 살아있어야 한다."""
    a = _s("2026-06-01", 40, tz=None)
    b = _s("2026-06-01", 40, tz="America/New_York")
    x, y = align_common_index(a, b)
    assert len(x) == 40 and len(y) == 40
    assert len(x) == len(y)


def test_both_naive_unchanged():
    a, b = _s("2026-06-01", 30), _s("2026-06-01", 30)
    x, y = align_common_index(a, b)
    assert len(x) == 30 and len(y) == 30


def test_both_aware_same_tz():
    a = _s("2026-06-01", 30, tz="Asia/Seoul")
    b = _s("2026-06-01", 30, tz="Asia/Seoul")
    x, y = align_common_index(a, b)
    assert len(x) == 30


def test_different_timezones_align_by_calendar_day():
    """서로 다른 tz(서울 vs 뉴욕)라도 같은 거래일로 맞춰져야 한다."""
    a = _s("2026-06-01", 20, tz="Asia/Seoul")
    b = _s("2026-06-01", 20, tz="America/New_York")
    x, y = align_common_index(a, b)
    assert len(x) == 20 and len(y) == 20


def test_partial_overlap_keeps_intersection_only():
    a = _s("2026-06-01", 30, tz=None)
    b = _s("2026-06-15", 30, tz="America/New_York")
    x, y = align_common_index(a, b)
    assert 0 < len(x) < 30 and len(x) == len(y)


def test_start_filter_applies():
    a = _s("2026-06-01", 40, tz=None)
    b = _s("2026-06-01", 40, tz="America/New_York")
    x, y = align_common_index(a, b, start="2026-07-01")
    assert len(x) == len(y) and len(x) < 40
    assert x.index[0] >= pd.Timestamp("2026-07-01")


def test_none_inputs_graceful():
    assert align_common_index(None, _s("2026-06-01", 5)) == (None, None)
    assert align_common_index(_s("2026-06-01", 5), None) == (None, None)


def test_empty_inputs_graceful():
    x, y = align_common_index(pd.Series(dtype=float), _s("2026-06-01", 5))
    assert x is None and y is None


# ── 실제 백필 경로 회귀 (양 시장) ─────────────────────────────────────────────

def _patch_series(monkeypatch, module, stock, bench):
    """무네트워크 고정 — 백필이 쓰는 두 진입점을 모두 스텁(신선도 재조회 경로 포함)."""
    from providers import market_data
    mapping = {}
    mapping.update(stock)
    mapping.update(bench)

    monkeypatch.setattr(market_data, "load_ohlc_close_series", lambda t, **k: mapping.get(t))
    monkeypatch.setattr(market_data, "load_close_series_upto",
                        lambda t, need_last=None, fetch_fn=None: mapping.get(t))
    # 실수로 라이브 조회가 새면 테스트가 즉시 실패하도록
    monkeypatch.setattr(market_data, "_fetch_close_series_live",
                        lambda *a, **k: pytest.fail("테스트에서 라이브 시세 조회 발생"))


def test_us_price_fn_matures_across_tz_mismatch(monkeypatch):
    from crons import us_mock_learn as UL
    _patch_series(monkeypatch, UL,
                  {"INTC": _s("2026-06-01", 60, tz=None, step=1.0)},
                  {"QQQ": _s("2026-06-01", 60, tz="America/New_York", step=0.5)})
    res = UL._default_price_fn("INTC", "2026-06-01", 20)
    assert res is not None, "tz 불일치로 성숙 실패 — 회귀"
    stock_ret, idx_ret, _, _ = res
    assert stock_ret > 0 and idx_ret > 0
    assert stock_ret > idx_ret          # 종목이 더 가파름(step 1.0 vs 0.5)


def test_kr_price_fn_matures_across_tz_mismatch(monkeypatch):
    from crons import kr_mock_learn as KL
    from ml.data_pipeline import KR_BENCHMARK
    _patch_series(monkeypatch, KL,
                  {"005930.KS": _s("2026-06-01", 60, tz=None, step=1.0)},
                  {KR_BENCHMARK: _s("2026-06-01", 60, tz="Asia/Seoul", step=0.5)})
    res = KL._default_price_fn("005930.KS", "2026-06-01", 20)
    assert res is not None, "tz 불일치로 성숙 실패 — 회귀"
    stock_ret, idx_ret, _, _ = res
    assert stock_ret > idx_ret


def test_price_fn_still_returns_none_when_genuinely_immature(monkeypatch):
    """정상적으로 기간 미달이면 여전히 None (성숙 오판 방지)."""
    from crons import us_mock_learn as UL
    _patch_series(monkeypatch, UL,
                  {"INTC": _s("2026-06-01", 10, tz=None)},
                  {"QQQ": _s("2026-06-01", 10, tz="America/New_York")})
    assert UL._default_price_fn("INTC", "2026-06-01", 20) is None


# ── 캐시 staleness — 두 번째 근본 원인 (감사 2026-08-21) ──────────────────────
# load_cached_ohlc 는 나이 검사가 전혀 없어 7/31 에 쓰인 parquet 를 무기한 서빙한다.
# 그 결과 US 종목 시리즈가 7/31 에서 멈춰(벤치 QQQ 는 8/14 까지) 7/8 이후 결정이
# 공통 거래일 18개(<20)로 영원히 미성숙 상태에 갇혔다. tz 수정만으로는 안 풀린다.

def test_close_series_refetches_when_cache_too_stale(monkeypatch):
    from providers import market_data

    stale = _s("2026-06-01", 40, tz=None)          # ~7/24 에서 끝남
    fresh = _s("2026-06-01", 60, tz=None)          # 더 최신까지
    calls = []

    monkeypatch.setattr(market_data, "load_cached_ohlc", lambda sym, period="1y": None)
    monkeypatch.setattr(market_data, "load_cached_price_ohlc", lambda sym: None)
    monkeypatch.setattr(market_data, "load_ohlc_close_series", lambda sym, **k: stale)

    def _fetch(sym):
        calls.append(sym)
        return fresh
    monkeypatch.setattr(market_data, "_fetch_close_series_live", _fetch, raising=False)

    out = market_data.load_close_series_upto(
        "ADBE", need_last=fresh.index[-1], fetch_fn=_fetch)
    assert calls == ["ADBE"], "캐시가 목표일에 못 미치면 재조회해야 함"
    assert out is not None and out.index[-1] == fresh.index[-1]


def test_close_series_uses_cache_when_fresh_enough(monkeypatch):
    from providers import market_data

    cached = _s("2026-06-01", 60, tz=None)
    calls = []

    monkeypatch.setattr(market_data, "load_ohlc_close_series", lambda sym, **k: cached)

    def _fetch(sym):
        calls.append(sym)
        return cached

    out = market_data.load_close_series_upto(
        "ADBE", need_last=cached.index[30], fetch_fn=_fetch)
    assert calls == [], "캐시가 충분하면 재조회 금지(불필요한 네트워크)"
    assert len(out) == 60


def test_close_series_upto_graceful_when_fetch_fails(monkeypatch):
    from providers import market_data
    cached = _s("2026-06-01", 10, tz=None)
    monkeypatch.setattr(market_data, "load_ohlc_close_series", lambda sym, **k: cached)

    def _boom(sym):
        raise RuntimeError("network")

    out = market_data.load_close_series_upto(
        "ADBE", need_last=_s("2026-06-01", 60).index[-1], fetch_fn=_boom)
    assert out is not None and len(out) == 10      # 캐시라도 반환(예외 전파 금지)
