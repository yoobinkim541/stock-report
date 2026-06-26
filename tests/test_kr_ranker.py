#!/usr/bin/env python3
"""
test_kr_ranker.py — KR 전용 ranker/데이터 파이프라인 (무네트워크, 구조·wrapper 검증).

실제 학습은 네트워크 필요 → 여기선 유니버스·벤치마크·시그니처·wrapper 로직만 검증.
(학습 자체는 weekly_kr_ranker_retrain 라이브에서 검증.)
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import data_pipeline as dp          # noqa: E402
from ml import ranker as rk                 # noqa: E402
from ml import kr_ranker                    # noqa: E402


def test_kr30_universe():
    u = dp.fetch_universe("kr30")
    assert len(u) == 30 and len(set(u)) == 30          # 중복 없는 30종목
    assert all(t.endswith(".KS") for t in u)           # 전부 KOSPI
    assert "005930.KS" in u                             # 삼성전자 포함


def test_kr_benchmark_constant():
    assert dp.KR_BENCHMARK == "^KS11"
    assert kr_ranker.KR_MODEL_CACHE.name == "kr_ranker_model.pkl"
    assert kr_ranker.KR_MODE == "kr30"


def test_build_ml_dataset_has_benchmark_param():
    sig = inspect.signature(dp.build_ml_dataset)
    assert "benchmark_ticker" in sig.parameters
    assert sig.parameters["benchmark_ticker"].default == "QQQ"   # 미국 기본 유지(하위호환)


def test_rank_today_accepts_benchmark_and_cache():
    sig = inspect.signature(rk.rank_today)
    assert "benchmark_ticker" in sig.parameters
    assert "cache_path" in sig.parameters


def test_kr_scores_by_ticker_maps_and_handles_failure(monkeypatch):
    import pandas as pd
    df = pd.DataFrame([{"ticker": "005930.KS", "score": 0.8},
                       {"ticker": "000660.KS", "score": 0.6}])
    monkeypatch.setattr(kr_ranker, "rank_kr_today", lambda top_n=30: df)
    out = kr_ranker.kr_scores_by_ticker()
    assert out == {"005930.KS": 0.8, "000660.KS": 0.6}

    # 실패 시 빈 dict (정책이 graceful 폴백하도록)
    def boom(top_n=30):
        raise RuntimeError("no network")
    monkeypatch.setattr(kr_ranker, "rank_kr_today", boom)
    assert kr_ranker.kr_scores_by_ticker() == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
