#!/usr/bin/env python3
"""test_validation.py — Tier 2 검증 formalism 폐형해 단위테스트 (무네트워크·seed).

PSR/DSR/expected_max_sharpe/MinTRL/PBO(CSCV)/purged-CV 의 수학을 닫힌해·구성으로 검증.
PBO 의 핵심 교정: 순수 노이즈는 ≈0.5, block-local 엣지라야 ≈1.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import validation as v


# ── PSR ───────────────────────────────────────────────────────────────
def test_psr_equals_half_when_sr_equals_star():
    assert v.probabilistic_sharpe_ratio(0.1, 0.1, 250, 0.0, 3.0) == pytest.approx(0.5)


def test_psr_monotonic_in_sr():
    a = v.probabilistic_sharpe_ratio(0.05, 0, 250, 0, 3)
    b = v.probabilistic_sharpe_ratio(0.10, 0, 250, 0, 3)
    c = v.probabilistic_sharpe_ratio(0.20, 0, 250, 0, 3)
    assert a < b < c


def test_psr_negative_skew_penalized():
    lo = v.probabilistic_sharpe_ratio(0.1, 0, 250, -1.0, 3)
    mid = v.probabilistic_sharpe_ratio(0.1, 0, 250, 0.0, 3)
    hi = v.probabilistic_sharpe_ratio(0.1, 0, 250, 1.0, 3)
    assert lo < mid < hi


def test_psr_fat_tails_penalized():
    assert (v.probabilistic_sharpe_ratio(0.1, 0, 250, 0, 6.0)
            < v.probabilistic_sharpe_ratio(0.1, 0, 250, 0, 3.0))


# ── expected_max_sharpe / DSR ─────────────────────────────────────────
def test_expected_max_sharpe_increases_with_trials():
    assert v.expected_max_sharpe(5, 0.25) < v.expected_max_sharpe(100, 0.25)


def test_expected_max_sharpe_edge_guards():
    assert v.expected_max_sharpe(1, 0.25) == 0.0       # N≤1 → 0 (Φ⁻¹(0)=−∞ 가드)
    assert v.expected_max_sharpe(10, 0.0) == 0.0       # V≤0 → 0


def test_dsr_below_psr_when_multiple_trials():
    rng = np.random.default_rng(11)
    r = rng.normal(0.0008, 0.01, 500)
    sr = v.sharpe_ratio(r)["pp"]
    sk, ku = v._skew_kurt(r)
    psr0 = v.probabilistic_sharpe_ratio(sr, 0.0, len(r), sk, ku)
    dsr = v.deflated_sharpe_ratio(r, n_trials=20, sr_variance=0.0004)
    assert dsr is not None and dsr < psr0


def test_dsr_none_without_variance():
    rng = np.random.default_rng(12)
    r = rng.normal(0.0008, 0.01, 300)
    assert v.deflated_sharpe_ratio(r, n_trials=10, sr_variance=None) is None


def test_kurtosis_convention_non_excess():
    rng = np.random.default_rng(5)
    r = rng.normal(0, 0.01, 5000)
    _, ku = v._skew_kurt(r)
    assert abs(ku - 3.0) < 0.5                          # 非초과(정규=3); excess였으면 ≈0


# ── MinTRL ────────────────────────────────────────────────────────────
def test_mintrl_recovers_confidence():
    sr, sk, ku, p = 0.12, -0.2, 3.5, 0.95
    T = v.min_track_record_length(sr, 0.0, sk, ku, p)
    assert v.probabilistic_sharpe_ratio(sr, 0.0, T, sk, ku) == pytest.approx(p, abs=1e-3)


def test_mintrl_inf_when_below_star():
    assert v.min_track_record_length(0.05, 0.10, 0, 3) == float("inf")


# ── PBO (CSCV) ────────────────────────────────────────────────────────
def test_pbo_none_under_two_configs():
    M = np.random.default_rng(0).normal(0, 0.01, (100, 1))
    assert v.pbo_cscv(M) is None


def test_pbo_overfit_block_local():
    rng = np.random.default_rng(20)
    T, N, S = 1200, 10, 10
    M = rng.normal(0, 0.005, (T, N))
    blocks = np.array_split(np.arange(T), S)
    for b in range(S):
        M[blocks[b], b % N] += 0.05                     # 각 config 엣지를 한 블록에만
    res = v.pbo_cscv(M, n_splits=S)
    assert res is not None and res["pbo"] >= 0.9


def test_pbo_robust_one_dominant():
    rng = np.random.default_rng(21)
    M = rng.normal(0, 0.01, (1000, 20))
    M[:, 0] += 0.0015                                   # 한 config 지속 엣지
    res = v.pbo_cscv(M, n_splits=10)
    assert res is not None and res["pbo"] <= 0.1


def test_pbo_pure_noise_near_half():
    rng = np.random.default_rng(22)
    M = rng.normal(0, 0.01, (1000, 10))
    res = v.pbo_cscv(M, n_splits=10)
    assert res is not None and 0.2 <= res["pbo"] <= 0.8   # 노이즈 ≈0.5 (1 아님)


# ── A-fix-learn: PBO 동률·중복·무정보 (A-07) ─────────────────────────────
def test_pbo_identical_configs_are_no_information_not_zero():
    col = np.random.default_rng(40).normal(0.001, 0.01, 40)
    M = np.column_stack([col] * 4)                        # 40×4 동일 열
    assert v.pbo_cscv(M, n_splits=10) is None             # 과적합 0% 로 보고하지 않음


def test_pbo_constant_configs_are_no_information():
    M = np.column_stack([np.full(40, 0.01), np.zeros(40), np.full(40, -0.01)])
    assert v.pbo_cscv(M, n_splits=10) is None


def test_pbo_duplicate_columns_do_not_change_result():
    rng = np.random.default_rng(41)
    M = rng.normal(0, 0.01, (400, 6))
    base = v.pbo_cscv(M, n_splits=10)
    padded = v.pbo_cscv(np.column_stack([M, M[:, 0], M[:, 0], M[:, 3]]), n_splits=10)
    assert padded["pbo"] == pytest.approx(base["pbo"])
    assert padded["n_configs"] == 6 and padded["n_input_configs"] == 9


def test_pbo_equal_sharpe_configs_use_average_rank():
    col = np.random.default_rng(42).normal(0.001, 0.01, 200)
    res = v.pbo_cscv(np.column_stack([col, 2.0 * col]), n_splits=10)   # 모든 구간 Sharpe 동률
    # 동률을 최상위로 세면 PBO=0(완벽 통과). 평균 순위면 ω=0.5 → 과적합 쪽(λ≤0)으로 집계.
    assert res["pbo"] == pytest.approx(1.0)


def test_pbo_invariant_to_column_order_with_ties():
    rng = np.random.default_rng(43)
    M = rng.normal(0, 0.01, (300, 5))
    M = np.column_stack([M, 4.0 * M[:, 2]])               # 2번 열과 Sharpe 정확히 동률인 열
    perm = [5, 3, 0, 2, 4, 1]
    a = v.pbo_cscv(M, n_splits=10)
    b = v.pbo_cscv(M[:, perm], n_splits=10)
    assert a["pbo"] == pytest.approx(b["pbo"])


def test_dsr_effective_n_cannot_exceed_observations():
    r = np.random.default_rng(44).normal(0.002, 0.01, 50)
    assert v.deflated_sharpe_ratio(r, n_trials=1, effective_n=51) is None
    full = v.deflated_sharpe_ratio(r, n_trials=1)
    fewer = v.deflated_sharpe_ratio(r, n_trials=1, effective_n=10)
    assert full > fewer > 0.5                             # 독립 표본이 적을수록 증거 약함


# ── Purged K-Fold ─────────────────────────────────────────────────────
def test_purged_kfold_no_label_overlap():
    splits = v.purged_kfold_indices(100, n_splits=5, label_horizon=5, embargo=3)
    tests = []
    for train, test in splits:
        # 라벨 호라이즌은 양쪽 모두 제거: 왼쪽 t0−H, 오른쪽 t1+H+embargo (De Prado purge)
        lo, hi = int(test[0]) - 5, int(test[-1]) + 5 + 3
        assert not any(lo <= int(i) <= hi for i in train)   # 라벨창 겹침 0(양쪽)
        tests.append(set(int(i) for i in test))
    assert set().union(*tests) == set(range(100))           # 전부 커버
    for a in range(len(tests)):
        for b in range(a + 1, len(tests)):
            assert tests[a].isdisjoint(tests[b])            # disjoint


def test_purged_kfold_purges_right_side_label_horizon():
    """test 폴드 직후 (t1, t1+H] 의 forward-라벨 표본이 train 에서 제거되는지 (감사 확정 회귀)."""
    splits = v.purged_kfold_indices(100, n_splits=5, label_horizon=5, embargo=0)
    for train, test in splits:
        t1 = int(test[-1])
        # embargo=0 이어도 오른쪽 라벨 호라이즌(t1+1 .. t1+5)은 train 에 없어야 함
        right_overlap = [i for i in train if t1 < int(i) <= t1 + 5]
        assert right_overlap == []


# ── validate_strategy ─────────────────────────────────────────────────
def test_validate_strategy_keys_and_dsr_none_when_single_trial():
    rng = np.random.default_rng(30)
    r = rng.normal(0.0006, 0.01, 400)
    out = v.validate_strategy(r, n_trials=1)
    assert {"sharpe", "sharpe_pp", "psr", "dsr", "n_trials", "n_obs"} <= set(out)
    assert out["dsr"] is None


def test_validate_strategy_beats_benchmark():
    rng = np.random.default_rng(31)
    b = rng.normal(0.0005, 0.01, 400)
    r = b + 0.0003                                       # 벤치마크 + 양의 초과
    out = v.validate_strategy(r, benchmark_returns=b, n_trials=3, sr_variance=0.0004)
    assert out["psr_excess"] > 0.5 and out["dsr"] is not None


def test_validate_strategy_underperforms_benchmark():
    rng = np.random.default_rng(32)
    b = rng.normal(0.0006, 0.01, 400)
    r = b - 0.0003                                       # 벤치마크 미달
    assert v.validate_strategy(r, benchmark_returns=b)["psr_excess"] < 0.5


def test_validate_strategy_short_returns_none():
    assert v.validate_strategy([0.01]) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
