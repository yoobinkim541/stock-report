#!/usr/bin/env python3
"""test_entry_adaptive.py — 해외 단기진입 적응 학습 (무네트워크, shadow·★목적함수)."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import entry_adaptive_learn as E       # noqa: E402
from ml import entry_analyzer as EA    # noqa: E402


def test_eval_threshold_entered_subset():
    s = [(0.8, 1.0), (0.7, -0.5), (0.5, 2.0)]    # 0.5 짜리는 thr 0.65 에서 제외
    ev = E._eval_threshold(s, 0.65)
    assert ev["n"] == 2                            # 0.8, 0.7 진입
    assert ev["excess"] == pytest.approx(0.25)     # (1.0 + -0.5)/2
    assert ev["mdd"] == pytest.approx(0.5)         # 음수 평균 |−0.5|


def test_best_threshold_maximizes_mean_r():
    # 0.70 이상만 양(+1), 0.65 는 음(−1) → 최적 임계값 0.70 부근
    s = [(0.75, 1.0) if i % 2 == 0 else (0.65, -1.0) for i in range(20)]
    assert E._best_threshold(s) >= 0.70


def test_learn_holds_on_insufficient_samples():
    out = E.learn([(0.7, 1.0)] * 5)
    assert out["adopted"] is False and "미달" in out["reason"]


def test_learn_adopts_improving_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "SHADOW_PATH", tmp_path / "entry_score_params_adaptive.json")
    monkeypatch.delenv("ADAPTIVE_ENTRY_ENABLED", raising=False)
    monkeypatch.setattr(EA, "SCORE_PARAMS_PATH", tmp_path / "none.json")   # 라이브 파일 격리 → cur 0.62
    monkeypatch.setattr(EA, "_score_params_cache", None)   # 캐시 초기화(후 복원)
    # 0.65(현행 0.62 진입군) 는 손실, 0.75 만 이익 → 임계값 상향이 OOS 개선
    samples = [((0.75, 1.0) if i % 2 == 0 else (0.65, -1.0)) for i in range(40)]
    out = E.learn(samples)
    assert out["adopted"] is True
    assert out["cand_thr"] >= 0.70
    assert (tmp_path / "entry_score_params_adaptive.json").exists()   # shadow 기록


def test_learn_holds_when_no_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "SHADOW_PATH", tmp_path / "s.json")
    monkeypatch.delenv("ADAPTIVE_ENTRY_ENABLED", raising=False)
    monkeypatch.setattr(EA, "_score_params_cache", None)
    samples = [(0.8, -1.0) for _ in range(40)]    # 전부 손실 → 어떤 임계값도 아웃퍼폼 X
    out = E.learn(samples)
    assert out["adopted"] is False                 # 절대 아웃퍼폼(>0) 미충족 → 보류


def test_score_params_clamped(monkeypatch):
    # 손상된 극단 enter_threshold → 안전범위로 클램프
    assert EA._clamp_score_params({"enter_threshold": 9.9})["enter_threshold"] == 0.85
    assert EA._clamp_score_params({"w_rsi": -1.0})["w_rsi"] == 0.0


def test_adaptive_shadow_off_by_default(tmp_path, monkeypatch):
    # 기본 off → shadow 파일 있어도 라이브 미반영 (라이브 calibration 파일 격리)
    monkeypatch.delenv("ADAPTIVE_ENTRY_ENABLED", raising=False)
    monkeypatch.setattr(EA, "SCORE_PARAMS_PATH", tmp_path / "none.json")
    monkeypatch.setattr(EA, "_score_params_cache", None)
    p = EA.get_score_params()
    assert p["enter_threshold"] == EA.DEFAULT_SCORE_PARAMS["enter_threshold"]   # 0.62 유지


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
