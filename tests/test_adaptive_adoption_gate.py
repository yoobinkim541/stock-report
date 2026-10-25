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
    (_STRONG, 31, "evidence_invalid"),                    # 독립 단위 ≠ 관측 수
    (_STRONG, 10, "evidence_invalid"),                    # 중첩열 모멘트 + 작은 T 혼용 금지(RA-02)
    (_STRONG[:learner.ADOPT_MIN_INDEPENDENT - 1], None, "insufficient_independent"),
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


def _keyed(sharpes, prefix="k"):
    return {f"{prefix}{i}": s for i, s in enumerate(sharpes)}


def test_repeated_noise_search_deflates_marginal_candidate(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    assert _run(p, _eval(_MODERATE))["adopted"] is True      # 첫 시도라면 통과하는 후보
    p.path.unlink()
    noise_sharpes = list(np.random.default_rng(7).normal(0.0, 0.18, 49))
    out = _run(p, _eval(_MODERATE), trial_log={"legacy": 0, "trials": _keyed(noise_sharpes)})
    assert out["evidence"]["n_trials"] == 50
    assert out["adopted"] is False and out["evidence"]["reason"] == "not_significant"
    assert not p.path.exists()


def test_reused_oos_without_trial_variance_is_not_saved(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)
    out = _run(p, _eval(_STRONG), trial_log={"legacy": 11, "trials": {}})
    assert out["adopted"] is False and out["evidence"]["reason"] == "trial_evidence_insufficient"
    assert out["evidence"]["n_trials"] == 12
    assert out["evidence"]["trial_sharpe"] is not None        # 다음 시도 분산 추정용으로 기록(미반올림)
    assert out["evidence"]["trial_key"]
    assert not p.path.exists()


def test_objective_failure_still_blocks_even_with_strong_statistics(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch)

    def ev(oos_rows, params):
        if params.get("w", 0.5) >= 0.85:
            return {"excess": 0.02, "mdd": 0.30, "n": 30, "series": _STRONG, "n_independent": 30}
        return {"excess": 0.0, "mdd": 0.01, "n": 30}
    out = _run(p, ev)
    assert out["adopted"] is False and not p.path.exists()


def test_missing_risk_curve_blocks_even_with_strong_statistics(tmp_path, monkeypatch):
    """A-fix2 RA-01: eval 이 보유 경로 MDD/지수 MDD 를 못 내면(None) 위험 증거 없음 → 저장 0회."""
    p = _policy(tmp_path, monkeypatch)

    def ev(oos_rows, params):
        if params.get("w", 0.5) >= 0.85:
            return {"excess": 0.02, "mdd": None, "index_mdd": None, "mdd_basis": "unavailable",
                    "n": 30, "series": _STRONG, "n_independent": 30}
        return {"excess": 0.0, "mdd": None, "index_mdd": None, "n": 30}
    out = _run(p, ev)
    assert out["evidence"]["ok"] is True                      # 통계만으로는 통과하지만
    assert out["adopted"] is False and "위험 증거 없음" in out["reason"]
    assert not p.path.exists()


# ── RA-03: 시도 이력의 정보량·중복·누락 ───────────────────────────────────────────

_CHAL = {"series": _MODERATE, "n_independent": len(_MODERATE)}


def test_single_trial_moderate_candidate_passes_psr():
    ev = learner.adoption_evidence(_CHAL, trial_log=None, trial_key="cur")
    assert ev["ok"] is True and ev["n_trials"] == 1


def test_rounded_legacy_repeats_are_not_variance_evidence():
    """리뷰 재현: 같은 결과 9,999회(반올림 Sharpe만, 지문 없음) + 현재 → 예전 ok=True(psr .968)."""
    ev = learner.adoption_evidence(_CHAL, trial_log={"legacy": 9999, "trials": {}}, trial_key="cur")
    assert ev["n_trials"] == 10000 and ev["n_trial_sharpes"] == 1
    assert ev["ok"] is False and ev["reason"] == "trial_evidence_insufficient"


def test_mostly_missing_trial_sharpes_are_not_variance_evidence():
    """지문 있는 시도 20회 중 Sharpe 가 6개뿐(+현재 1) → 7/21 < 50% → 분산 추정 금지."""
    trials = {f"k{i}": (0.05 * (i - 3) if i < 6 else None) for i in range(20)}
    ev = learner.adoption_evidence(_CHAL, trial_log={"legacy": 0, "trials": trials}, trial_key="cur")
    assert ev["n_trials"] == 21 and ev["n_trial_sharpes"] == 7
    assert ev["ok"] is False and ev["reason"] == "trial_evidence_insufficient"


def test_distinct_trials_with_identical_sharpes_are_degenerate():
    """서로 다른 지문 10개가 모두 같은 Sharpe(=무정보 반복) → 분산 0 을 증거로 쓰지 않는다."""
    same = learner.adoption_evidence(_CHAL, trial_log=None, trial_key="x")["trial_sharpe"]
    ev = learner.adoption_evidence(_CHAL, trial_log={"legacy": 0, "trials": _keyed([same] * 10)},
                                   trial_key="cur")
    assert ev["n_trials"] == 11 and ev["ok"] is False and ev["reason"] == "trial_variance_degenerate"


def test_reevaluating_same_fingerprint_is_one_trial():
    """같은 (후보, OOS) 지문 재평가는 새 시도가 아니다 — 시도 수·분산 모두 한 번만."""
    s = learner.adoption_evidence(_CHAL, trial_log=None, trial_key="same")["trial_sharpe"]
    ev = learner.adoption_evidence(_CHAL, trial_log={"legacy": 0, "trials": {"same": s}},
                                   trial_key="same")
    assert ev["n_trials"] == 1 and ev["n_trial_sharpes"] == 1
    assert ev["ok"] is True                                   # 단일 시도 PSR 그대로(가짜 분산 없음)


def test_distinct_candidate_family_is_deflated_normally():
    """실제 서로 다른 후보군(지문 30개·Sharpe 분산 있음) → DSR 계산, 강한 후보는 통과."""
    sharpes = list(np.random.default_rng(3).normal(0.0, 0.05, 30))
    strong = {"series": _STRONG, "n_independent": len(_STRONG)}
    ev = learner.adoption_evidence(strong, trial_log={"legacy": 0, "trials": _keyed(sharpes)},
                                   trial_key="cur")
    assert ev["n_trials"] == 31 and ev["ok"] is True and ev["psr"] >= learner.ADOPT_MIN_PSR
    weak = learner.adoption_evidence(_CHAL, trial_log={"legacy": 0, "trials": _keyed(sharpes)},
                                     trial_key="cur")
    assert weak["ok"] is False and weak["reason"] == "not_significant"


def test_trial_history_dedupes_fingerprints_and_separates_legacy(tmp_path):
    base = str(tmp_path)
    rec = lambda **kw: evolution.record_learning("kr_mock", {"date": "2026-07-01", **kw}, base_dir=base)
    rec(adopted=False, reason="콜드스타트 (표본 미달)")                  # 평가 전 — 시도 아님
    rec(excess_challenger=0.01)                                          # legacy(지문 없음)
    rec(excess_challenger=0.02, sharpe_challenger=0.3)                   # legacy — 반올림값은 버림
    rec(excess_challenger=0.02, trial_key="a", trial_sharpe=0.123456789)
    rec(excess_challenger=0.02, trial_key="a", trial_sharpe=0.123456789)  # 같은 지문 재평가
    rec(excess_challenger=0.03, trial_key="b", trial_sharpe=None)
    assert learner.trial_history("kr_mock", base_dir=base) == {
        "legacy": 2, "trials": {"a": 0.123456789, "b": None}}
