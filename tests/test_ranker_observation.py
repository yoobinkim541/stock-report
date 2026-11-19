"""랭킹 관측일·유효성 회귀 (무네트워크).

재현: 횡단면 최신일이 2026-11-17 인데 6일 전·수개월 전 종목, 단독 미래 스탬프,
비유한 점수, 유니버스 중복이 그대로 추천에 들어가던 경로.
세션을 공유하는 유효 종목의 점수 순서는 유지해야 한다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from ml import data_pipeline as dp  # noqa: E402
from ml import ranker  # noqa: E402


class _HintModel:
    def predict(self, frame):
        return frame["score_hint"].to_numpy(dtype=float)


def _result():
    return ranker.RankerResult(
        model=_HintModel(),
        feature_names=["score_hint"],
        train_end_date="2026-01-01",
        oos_ic=0.0,
        oos_icir=0.0,
        oos_top_decile_ret=0.0,
        oos_hit_rate=0.0,
        feature_importance=pd.Series(dtype=float),
    )


def _ohlc(end, periods, close):
    idx = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0},
        index=idx,
    )


def _feat(end, periods, score):
    idx = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame({"score_hint": float(score)}, index=idx)


def _install(monkeypatch, universe, prices, features):
    def _build(ticker, df, market_feat, qqq_close=None, sector_id=0):
        if ticker == "BOOM":
            raise RuntimeError("bad bar")
        return features[ticker]

    monkeypatch.setattr(ranker, "load_ranker", lambda path=ranker.MODEL_CACHE: _result())
    monkeypatch.setattr(ranker, "ranker_gate", lambda _: {"passed": True})
    monkeypatch.setattr(ranker, "_fundamental_scores", lambda *a, **k: {})
    monkeypatch.setattr(dp, "fetch_universe", lambda mode="nasdaq100": list(universe))
    monkeypatch.setattr(dp, "fetch_prices", lambda tickers, days=300, batch_size=20: prices)
    monkeypatch.setattr(dp, "build_stock_features", _build)
    monkeypatch.setattr(dp, "build_fear_greed_proxy", lambda days=300: pd.Series(50.0, name="fg_score"))
    import ml.technical_rating as technical_rating
    monkeypatch.setattr(technical_rating, "compute_technical_rating", lambda df: None)


def test_select_ranking_rows_drops_stale_future_duplicate_and_nonfinite():
    """단독 미래 스탬프가 세션을 밀지 않고, 중복은 더 최신 관측만 남긴다."""
    rows = [
        {"ticker": "AAA", "score": 0.9, "obs_date": "2026-11-13"},
        {"ticker": "AAA", "score": 0.1, "obs_date": "2026-11-17"},
        {"ticker": "BBB", "score": 0.2, "obs_date": "2026-11-17"},
        {"ticker": "OLD", "score": 9.0, "obs_date": "2026-08-01"},
        {"ticker": "FUTURE", "score": 8.0, "obs_date": "2099-01-01"},
        {"ticker": "INF", "score": float("inf"), "obs_date": "2026-11-17"},
        {"ticker": "NAN", "score": float("nan"), "obs_date": "2026-11-17"},
        {"ticker": None, "score": 0.3, "obs_date": "2026-11-17"},
    ]
    out = ranker._select_ranking_rows(rows)
    assert [r["ticker"] for r in out] == ["AAA", "BBB"]
    assert out[0]["score"] == 0.1
    assert out[0]["obs_date"] == "2026-11-17"


def test_rank_today_excludes_stale_duplicate_and_nonfinite(monkeypatch):
    """2026-11-17 세션에 6일 전·오래된 종목·비유한 점수가 1위가 되면 안 된다.

    LAG 는 피처가 2026-11-12 에서 끊기고 가격만 2026-11-17(종가 999)까지 있다.
    추천 가격은 피처 관측일의 100 이어야 한다.
    """
    session = "2026-11-17"
    lag_end = "2026-11-12"   # 세션 - 5일, 허용
    stale_end = "2026-11-11"  # 세션 - 6일, 제외
    prices = {
        "MSFT": _ohlc(session, 60, 100.0),
        "AAPL": _ohlc(session, 60, 100.0),
        "LAG": _ohlc(session, 80, 100.0),
        "STALE6": _ohlc(stale_end, 60, 50.0),
        "OLD": _ohlc("2026-08-03", 60, 10.0),
        "FUTURE": _ohlc("2026-12-01", 60, 10.0),
        "INF": _ohlc(session, 60, 100.0),
        "NAN": _ohlc(session, 60, 100.0),
        "BOOM": _ohlc(session, 60, 100.0),
    }
    lag_px = prices["LAG"]["Close"].copy()
    lag_px.loc[lag_px.index > pd.Timestamp(lag_end)] = 999.0
    prices["LAG"]["Close"] = lag_px
    features = {
        "MSFT": _feat(session, 60, 0.9),
        "AAPL": _feat(session, 60, 0.2),
        "LAG": _feat(lag_end, 60, 0.4),
        "STALE6": _feat(stale_end, 60, 0.7),
        "OLD": _feat("2026-08-03", 60, 9.0),
        "FUTURE": _feat("2026-12-01", 60, 8.0),
        "INF": _feat(session, 60, np.inf),
        "NAN": _feat(session, 60, np.nan),
    }
    universe = ["MSFT", "AAPL", "LAG", "STALE6", "OLD", "FUTURE", "INF", "NAN", "BOOM", "MSFT"]
    _install(monkeypatch, universe, prices, features)

    ranking = ranker.rank_today(mode="nasdaq100", top_n=10)
    assert list(ranking["ticker"]) == ["MSFT", "LAG", "AAPL"]
    assert list(ranking["score"]) == [0.9, 0.4, 0.2]
    assert set(ranking["obs_date"]) <= {session, lag_end}
    lag = ranking.loc[ranking["ticker"] == "LAG"].iloc[0]
    assert lag["obs_date"] == lag_end
    assert lag["price"] == 100.0

    scores = ranker.scores_by_ticker(["OLD", "MSFT", "AAPL", "INF", "FUTURE", "LAG", "STALE6"])
    assert scores == {"MSFT": 0.9, "LAG": 0.4, "AAPL": 0.2}
    for ticker, score in scores.items():
        got = float(ranking.loc[ranking["ticker"] == ticker, "score"].iloc[0])
        assert score == got


def test_rank_today_keeps_aligned_older_session(monkeypatch):
    """전 종목이 같은 과거 세션이면 벽시계와 비교해 비우지 않는다."""
    end = "2026-08-03"
    universe = ["AAA", "BBB"]
    prices = {t: _ohlc(end, 60, 100.0) for t in universe}
    features = {"AAA": _feat(end, 60, 0.2), "BBB": _feat(end, 60, 0.8)}
    _install(monkeypatch, universe, prices, features)

    ranking = ranker.rank_today(mode="nasdaq100", top_n=10)
    assert list(ranking["ticker"]) == ["BBB", "AAA"]
    assert set(ranking["obs_date"]) == {end}


def test_market_benchmark_blocks_whole_stale_cohort(monkeypatch):
    end = "2026-11-17"
    old = "2026-11-10"
    prices = {t: _ohlc(old, 60, 50.0) for t in ("A", "B")}
    prices["QQQ"] = _ohlc(end, 65, 50.0)
    prices["C"] = _ohlc(end, 65, 50.0)
    features = {t: _feat(old, 60, 1.0) for t in ("A", "B")}
    features["C"] = _feat(end, 65, 0.1)
    _install(monkeypatch, ["A", "B", "C"], prices, features)
    ranking = ranker.rank_today()
    assert list(ranking["ticker"]) == ["C"]
    assert "2026-11-17 종가" in ranker.format_ranking_report(ranking, _result())
