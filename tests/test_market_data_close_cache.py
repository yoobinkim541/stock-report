"""providers.market_data.load_ohlc_close_series — 낡은 디스크 캐시는 라이브로 보강한다."""
import pandas as pd

from providers import market_data as md


def _close(last: str, n: int = 10):
    idx = pd.date_range(end=last, periods=n, freq="B")
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": range(n), "Volume": 1.0}, index=idx)


def test_fresh_cache_is_served_without_network(monkeypatch):
    today = pd.Timestamp.now().normalize()
    monkeypatch.setattr(md, "load_cached_ohlc", lambda sym, period: _close(str(today.date())))
    monkeypatch.setattr(md, "yf", None)
    s = md.load_ohlc_close_series("AAPL")
    assert s is not None and len(s) == 10


def test_stale_cache_is_refreshed_and_merged(monkeypatch):
    stale_last = (pd.Timestamp.now().normalize() - pd.Timedelta(days=30))
    monkeypatch.setattr(md, "load_cached_ohlc", lambda sym, period: _close(str(stale_last.date())))
    calls = []

    class _T:
        def __init__(self, sym):
            calls.append(sym)

        def history(self, period):
            return _close(str(pd.Timestamp.now().normalize().date()), n=5)
    monkeypatch.setattr(md, "yf", type("yf", (), {"Ticker": _T}))
    s = md.load_ohlc_close_series("AAPL")
    assert calls == ["AAPL"]
    assert (pd.Timestamp.now().normalize() - s.index[-1].normalize()).days <= 3   # 영업일 끝(주말 여유)
    assert len(s) > 10                                   # 캐시 이력 + 라이브 병합


def test_stale_cache_kept_when_live_fails(monkeypatch):
    stale_last = (pd.Timestamp.now().normalize() - pd.Timedelta(days=30))
    monkeypatch.setattr(md, "load_cached_ohlc", lambda sym, period: _close(str(stale_last.date())))

    class _T:
        def __init__(self, sym):
            pass

        def history(self, period):
            raise RuntimeError("down")
    monkeypatch.setattr(md, "yf", type("yf", (), {"Ticker": _T}))
    s = md.load_ohlc_close_series("AAPL")
    assert s is not None and len(s) == 10


def test_max_stale_none_disables_check(monkeypatch):
    stale_last = (pd.Timestamp.now().normalize() - pd.Timedelta(days=30))
    monkeypatch.setattr(md, "load_cached_ohlc", lambda sym, period: _close(str(stale_last.date())))
    monkeypatch.setattr(md, "yf", None)
    assert len(md.load_ohlc_close_series("AAPL", max_stale_days=None)) == 10
