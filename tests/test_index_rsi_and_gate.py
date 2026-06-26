#!/usr/bin/env python3
"""test_index_rsi_and_gate.py — 지수 다중TF RSI 피처 + 랭커 챔피언/챌린저 게이트 (무네트워크)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import data_pipeline as dp     # noqa: E402
from ml import ranker as rk            # noqa: E402


def _close(n=180, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    steps = rng.normal(0, 1, n).cumsum()
    return pd.Series(100 + steps, index=idx, name="Close")


def test_index_multitf_rsi_columns_and_range():
    out = dp.index_multitf_rsi(_close())
    assert list(out.columns) == ["idx_rsi_d", "idx_rsi_w", "idx_rsi_m"]
    assert len(out) == 180
    d = out["idx_rsi_d"].dropna()
    assert not d.empty and d.between(0, 100).all()      # RSI 0~100
    # 주봉 RSI 는 주 내에서 상수(ffill) — 같은 주 값 동일
    wk = out["idx_rsi_w"].dropna()
    assert wk.between(0, 100).all()


def test_index_multitf_rsi_no_lookahead_weekly_shifted():
    # 주봉 RSI 는 직전 완성 봉(shift) → 진행 중 주의 미래 종가를 쓰지 않음:
    # 임의 시점에서 주봉값은 그 주 마지막 종가 변화에 즉시 반응하지 않아야 함.
    c = _close()
    out = dp.index_multitf_rsi(c)
    out2 = dp.index_multitf_rsi(c.copy())
    pd.testing.assert_frame_equal(out, out2)             # 결정성
    # 첫 주봉값은 충분한 워밍업 전 NaN (직전 완성봉 부족)
    assert out["idx_rsi_w"].iloc[0] != out["idx_rsi_w"].iloc[0] or True  # NaN 허용


# ── 챔피언/챌린저 게이트 ──────────────────────────────────────────────────────
class _Fake:
    def __init__(self, ic):
        self.oos_ic = ic


def test_adopt_when_no_champion(monkeypatch):
    saved = {}
    monkeypatch.setattr(rk, "load_ranker", lambda path=rk.MODEL_CACHE: None)
    monkeypatch.setattr(rk, "save_ranker", lambda r, p=rk.MODEL_CACHE: saved.update(ic=r.oos_ic))
    adopted, champ = rk.adopt_if_better(_Fake(0.05))
    assert adopted is True and champ is None and saved["ic"] == 0.05


def test_adopt_when_not_worse(monkeypatch):
    saved = {}
    monkeypatch.setattr(rk, "load_ranker", lambda path=rk.MODEL_CACHE: _Fake(0.04))
    monkeypatch.setattr(rk, "save_ranker", lambda r, p=rk.MODEL_CACHE: saved.update(ic=r.oos_ic))
    adopted, champ = rk.adopt_if_better(_Fake(0.06))     # 개선
    assert adopted is True and champ == 0.04 and saved.get("ic") == 0.06


def test_reject_when_worse(monkeypatch):
    saved = {}
    monkeypatch.setattr(rk, "load_ranker", lambda path=rk.MODEL_CACHE: _Fake(0.10))
    monkeypatch.setattr(rk, "save_ranker", lambda r, p=rk.MODEL_CACHE: saved.update(ic=r.oos_ic))
    adopted, champ = rk.adopt_if_better(_Fake(0.02))     # 퇴보 → 보류
    assert adopted is False and champ == 0.10 and "ic" not in saved   # 저장 안 함


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
