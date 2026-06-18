"""tests/test_data_pipeline_resilience.py — fetch_prices 견고성 무네트워크 검증

yf.download 를 가짜로 주입해 다음을 검증:
  1. 첫 N회 예외 → 이후 성공 시 재시도가 동작하고 결과를 반환하는가
  2. 첫 배치 전체 실패 → 배치 축소(20→10→5) 후 부분 회복되는가
  3. 모든 시도 실패 시 빈 dict 반환(크래시 없음) + 실패 집계 경고

네트워크·실제 yfinance·실제 캐시 디렉터리를 건드리지 않는다.
time.sleep 는 monkeypatch 로 무력화해 백오프 대기를 건너뛴다.
"""
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml import data_pipeline


def _fake_ohlcv(tickers, rows: int = 60) -> pd.DataFrame:
    """yf.download 멀티인덱스 응답 모사 (컬럼: (field, ticker))."""
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    fields = ["Open", "High", "Low", "Close", "Volume"]
    cols = pd.MultiIndex.from_product([fields, tickers])
    data = np.arange(rows * len(fields) * len(tickers), dtype=float).reshape(rows, -1) + 1.0
    return pd.DataFrame(data, index=idx, columns=cols)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """캐시를 tmp로 격리하고 백오프 sleep 을 무력화 (라이브 캐시·대기 방지)."""
    monkeypatch.setattr(data_pipeline, "CACHE_DIR", tmp_path / "ml-cache")
    sleeps: list[float] = []
    monkeypatch.setattr(data_pipeline.time, "sleep", lambda s: sleeps.append(s))
    # 다른 테스트가 참조할 수 있게 sleep 기록을 모듈에 잠시 부착
    data_pipeline._test_sleeps = sleeps  # type: ignore[attr-defined]
    yield
    if hasattr(data_pipeline, "_test_sleeps"):
        delattr(data_pipeline, "_test_sleeps")


def _install_fake_yf(monkeypatch, download_fn):
    """sys.modules 에 가짜 yfinance 를 주입 (fetch_prices 의 지역 import 가 집어감)."""
    fake = types.ModuleType("yfinance")
    fake.download = download_fn
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_retry_then_success(monkeypatch):
    """첫 2회 예외 → 3번째 성공. 재시도로 결과를 확보해야 한다."""
    calls = {"n": 0}

    def flaky_download(batch, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("일시적 yfinance 오류")
        return _fake_ohlcv(list(batch))

    _install_fake_yf(monkeypatch, flaky_download)

    out = data_pipeline.fetch_prices(["AAA", "BBB"], days=120, batch_size=20)

    assert calls["n"] == 3                      # 2회 실패 + 1회 성공
    assert set(out.keys()) == {"AAA", "BBB"}    # 결과 확보
    assert len(out["AAA"]) > 10
    # 백오프가 두 번 발생했는지 (마지막 성공 시도 전까지)
    assert len(data_pipeline._test_sleeps) == 2


def test_batch_shrink_recovers_partial(monkeypatch):
    """큰 배치는 항상 실패, 5종목 이하 서브배치만 성공 → 축소로 부분 회복."""
    def size_sensitive_download(batch, **kwargs):
        batch = list(batch)
        if len(batch) > 5:
            raise RuntimeError("배치가 너무 큼")
        return _fake_ohlcv(batch)

    _install_fake_yf(monkeypatch, size_sensitive_download)

    tickers = [f"T{i:02d}" for i in range(12)]
    out = data_pipeline.fetch_prices(tickers, days=120, batch_size=20)

    # 20→(실패)→10(실패)→5(성공) 단계로 전 종목 회복
    assert set(out.keys()) == set(tickers)


def test_all_fail_returns_empty(monkeypatch):
    """모든 시도 실패 → 크래시 없이 빈 dict 반환."""
    def always_fail(batch, **kwargs):
        raise RuntimeError("영구 장애")

    _install_fake_yf(monkeypatch, always_fail)

    out = data_pipeline.fetch_prices(["XXX"], days=120, batch_size=20)
    assert out == {}


def test_cache_first_skips_download(monkeypatch):
    """TTL 내 캐시가 있으면 yf.download 를 호출하지 않는다 (캐시 우선 보존)."""
    # 캐시에 미리 적재
    df = pd.DataFrame(
        {"Close": np.arange(60, dtype=float) + 1.0},
        index=pd.date_range("2024-01-01", periods=60, freq="B"),
    )
    data_pipeline._save_cache("price_CACHED_120d", df)

    called = {"n": 0}

    def should_not_run(batch, **kwargs):
        called["n"] += 1
        raise AssertionError("캐시 히트 시 다운로드가 호출되면 안 됨")

    _install_fake_yf(monkeypatch, should_not_run)

    out = data_pipeline.fetch_prices(["CACHED"], days=120, batch_size=20)
    assert called["n"] == 0
    assert "CACHED" in out
    assert len(out["CACHED"]) == 60


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
