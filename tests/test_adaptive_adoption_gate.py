"""A-fix-learn: 정책 채택 통계 게이트 (refit_and_adopt → policy.save 전 필수, A-04)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.adaptive import evolution, learner, policy as pol  # noqa: E402

_DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-05", periods=100)]
_ROWS = [{"date": d} for d in _DATES]
_STRONG = [0.02, 0.03, 0.01, 0.025, 0.015] * 6          # Sharpe≈2.5/평가단위, 30 단위
_MODERATE = [0.027, -0.013] * 15                         # Sharpe≈0.34 — 단일 시도면 PSR≈0.97


def _policy(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    return pol.Policy("gate_test", defaults={"w": 0.5}, bounds={"w": (0, 1)})


def _eval(series, *, n_independent=None, excess=None):
    def ev(oos_rows, params):
        if params.get("w", 0.5) >= 0.85:
            out = {"excess": float(np.mean(series)) if excess is None else excess,
                   "mdd": 0.01, "n": 30 if series is None else len(series)}
            if series is not None:
                out["series"] = list(series)
                out["n_independent"] = len(series) if n_independent is None else n_independent
            return out
        return {"excess": 0.0, "mdd": 0.01, "n": 30, "series": [0.0] * 30, "n_independent": 30}
    return ev


def _run(p, ev, **kw):
    return learner.refit_and_adopt(_ROWS, p, lambda tr: {"w": 0.9}, ev,
                                   index_mdd=0.10, min_samples=40, embargo=2, **kw)


def test_significant_independent_improvement_is_saved(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    out = _run(p, _eval(_STRONG))
    assert out["adopted"] is True
    assert out["evidence"]["ok"] is True and out["evidence"]["psr"] >= learner.ADOPT_MIN_PSR
    assert p.path.exists()


def test_micro_improvement_is_not_saved(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    noise = [1e-12 + 0.01 * s for s in [1, -1] * 15]
    out = _run(p, _eval(noise, excess=1e-12))
    assert out["adopted"] is False and out["evidence"]["reason"] == "not_significant"
    assert "통계 게이트" in out["reason"]
    assert not p.path.exists()


@pytest.mark.parametrize("series,n_ind,reason", [
    (None, None, "evidence_missing"),
    ([0.02, float("nan")] * 15, None, "evidence_invalid"),
    (_STRONG, 31, "evidence_invalid"),                    # 독립 단위 > 관측 수
    (_STRONG, learner.ADOPT_MIN_INDEPENDENT - 1, "insufficient_independent"),
])
def test_missing_or_insufficient_evidence_is_not_saved(tmp_path, monkeypatch, series, n_ind, reason):
    p = _policy(tmp_path, monkeypatch)
    out = _run(p, _eval(series, n_independent=n_ind, excess=0.02))
    assert out["adopted"] is False and out["evidence"]["reason"] == reason
    assert not p.path.exists()


def test_statistic_failure_is_not_saved(tmp_path, monkeypatch):
    from ml import validation
    p = _policy(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise FloatingPointError("broken")
    monkeypatch.setattr(validation, "deflated_sharpe_ratio", boom)
    out = _run(p, _eval(_STRONG))
    assert out["adopted"] is False and out["evidence"]["reason"] == "stat_failed"
    assert not p.path.exists()


def test_repeated_noise_search_deflates_marginal_candidate(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    assert _run(p, _eval(_MODERATE))["adopted"] is True      # 첫 시도라면 통과하는 후보
    p.path.unlink()
    noise_sharpes = list(np.random.default_rng(7).normal(0.0, 0.18, 49))
    out = _run(p, _eval(_MODERATE), n_trials=50, trial_sharpes=noise_sharpes)
    assert out["adopted"] is False and out["evidence"]["reason"] == "not_significant"
    assert not p.path.exists()


def test_reused_oos_without_trial_variance_is_not_saved(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    out = _run(p, _eval(_STRONG), n_trials=12, trial_sharpes=[])
    assert out["adopted"] is False and out["evidence"]["reason"] == "trial_variance_missing"
    assert out["evidence"]["sharpe_pp"] is not None           # 다음 시도 분산 추정용으로 기록
    assert not p.path.exists()


def test_objective_failure_still_blocks_even_with_strong_statistics(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)

    def ev(oos_rows, params):
        if params.get("w", 0.5) >= 0.85:
            return {"excess": 0.02, "mdd": 0.30, "n": 30, "series": _STRONG, "n_independent": 30}
        return {"excess": 0.0, "mdd": 0.01, "n": 30}
    out = _run(p, ev)
    assert out["adopted"] is False and not p.path.exists()


def test_trial_history_counts_evaluated_attempts(tmp_path):
    base = str(tmp_path)
    evolution.record_learning("kr_mock", {"date": "2026-07-01", "adopted": False,
                                          "reason": "콜드스타트 (표본 미달)"}, base_dir=base)
    evolution.record_learning("kr_mock", {"date": "2026-07-08", "adopted": False,
                                          "excess_challenger": 0.01}, base_dir=base)
    evolution.record_learning("kr_mock", {"date": "2026-07-15", "adopted": False,
                                          "excess_challenger": 0.02, "sharpe_challenger": 0.3},
                              base_dir=base)
    assert learner.trial_history("kr_mock", base_dir=base) == (2, [0.3])
