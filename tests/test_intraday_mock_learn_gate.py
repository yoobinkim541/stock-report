"""A-fix-learn: 단기 모의 학습 게이트 — 무정보 PBO 는 GO 근거가 아니다 (A-07)."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from crons import intraday_mock_learn as learn  # noqa: E402
from ml import intraday_policy as ip            # noqa: E402

_ALL_ONE = {axis: 1.0 for axis in ip.AXES}


def _rows(n, features, net_r):
    return [{"id": f"t{i:03d}", "date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}",
             "features": dict(features), "fwd_excess": net_r(i)} for i in range(n)]


def test_identical_theta_variants_are_not_go(tmp_path, monkeypatch):
    """모든 θ 변형이 같은 트레이드를 고르면 PBO 는 정보 없음(None) → OBSERVE, GO 아님."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rows = _rows(100, _ALL_ONE, lambda i: 0.15 + (0.01 if i % 2 else -0.01))
    gate = learn.gate_eval(rows, "kr")
    assert gate["psr"] == pytest.approx(1.0)
    assert gate["pbo"] is None
    assert gate["verdict"] == "OBSERVE"


def test_eval_policy_reports_daily_series_for_statistical_gate():
    rows = [{"id": "a", "date": "2026-03-02", "features": _ALL_ONE, "fwd_excess": 1.0},
            {"id": "b", "date": "2026-03-02", "features": _ALL_ONE, "fwd_excess": -1.0},
            {"id": "c", "date": "2026-03-03", "features": _ALL_ONE, "fwd_excess": 2.0}]
    out = learn.eval_policy(rows, {}, "kr")
    f = learn.RISK_FRAC
    assert out["n"] == 3 and out["n_candidates"] == 3 and out["n_selected"] == 3
    assert out["n_independent"] == 2
    assert out["series"] == pytest.approx([(1 + f) * (1 - f) - 1, 2 * f])
