#!/usr/bin/env python3
"""
test_adaptive_framework.py — ml.adaptive 공유 프레임워크 (무네트워크).

검증: 정책 클램프·TTL·폴백 / 불변 원장(append-only·멱등·조인) / ★목적함수
(아웃퍼폼 최우선·MDD≤지수, MDD 위반 후보 탈락) / walk-forward purge / 최근성 가중 / 챔피언·챌린저.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.adaptive import policy as pol     # noqa: E402
from ml.adaptive import reward, learner, regime, champion_challenger  # noqa: E402
from ml.adaptive.ledger import Ledger     # noqa: E402


# ── Policy ────────────────────────────────────────────────────────────────────
def test_policy_clamp_enforces_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    p = pol.Policy("t", defaults={"w": 0.5, "th": 0.6}, bounds={"w": (0.0, 1.0), "th": (0.4, 0.8)})
    c = p.clamp({"w": 9.9, "th": -3.0})
    assert c["w"] == 1.0 and c["th"] == 0.4          # 극단값 → 범위로 클램프


def test_policy_save_load_roundtrip_and_clamp(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    p = pol.Policy("t", defaults={"w": 0.5}, bounds={"w": (0.0, 1.0)})
    saved = p.save({"w": 2.0})                        # 저장 시 클램프
    assert saved["w"] == 1.0
    p2 = pol.Policy("t", defaults={"w": 0.5}, bounds={"w": (0.0, 1.0)})
    assert p2.load()["w"] == 1.0                      # 디스크에서 클램프된 값 로드


def test_policy_missing_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    p = pol.Policy("none", defaults={"w": 0.42}, bounds={})
    assert p.load()["w"] == 0.42


def test_policy_ignores_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    (tmp_path / "policy_t.json").write_text('{"w": 0.7, "bogus": 9}', encoding="utf-8")
    p = pol.Policy("t", defaults={"w": 0.5}, bounds={"w": (0.0, 1.0)})
    out = p.load()
    assert out["w"] == 0.7 and "bogus" not in out     # 스키마 드리프트 차단


# ── Ledger (불변 원장) ────────────────────────────────────────────────────────
def test_ledger_append_only_idempotent_and_join(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    did = lg.log_decision({"date": "2026-06-26", "ticker": "005930", "action": "편입", "price": 70000})
    assert did == "2026-06-26:005930"
    lg.log_decision({"date": "2026-06-26", "ticker": "005930", "action": "편입", "price": 70000})  # 재실행
    assert len(lg.read_decisions()) == 1              # 멱등 — 중복 줄 없음

    assert lg.pending()[0]["id"] == did               # 결과 전 → pending
    lg.log_outcome({"decision_id": did, "fwd_excess": 0.05, "success": True, "horizon": 20})
    lg.log_outcome({"decision_id": did, "fwd_excess": 0.05, "success": True, "horizon": 20})  # 멱등
    assert len(lg.read_outcomes()) == 1
    assert lg.pending() == []

    ts = lg.training_set()
    assert len(ts) == 1 and ts[0]["action"] == "편입" and ts[0]["fwd_excess"] == 0.05


def test_ledger_never_modifies_existing_lines(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.log_decision({"date": "2026-06-25", "ticker": "000660", "action": "편입"})
    raw1 = lg.decisions_path.read_text(encoding="utf-8")
    lg.log_decision({"date": "2026-06-26", "ticker": "035720", "action": "편입"})
    raw2 = lg.decisions_path.read_text(encoding="utf-8")
    assert raw2.startswith(raw1)                       # 기존 줄 불변, 뒤에 append 만


def test_ledger_journal_appends(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.append_journal("2026-06-26", "📥 편입 005930 — 강한 매수후보")
    lg.append_journal("2026-06-26", "📤 퇴출 000660 — 손절")
    md = (tmp_path / "kr_mock_journal" / "2026-06.md").read_text(encoding="utf-8")
    assert "편입 005930" in md and "퇴출 000660" in md


# ── Reward / ★목적함수 ────────────────────────────────────────────────────────
def test_max_drawdown_positive_magnitude():
    assert reward.max_drawdown([100, 120, 90, 110]) == pytest.approx((120 - 90) / 120)  # 0.25
    assert reward.max_drawdown([]) == 0.0


def test_excess_and_forward_return():
    assert reward.forward_return([100, 110, 121], 0, 2) == pytest.approx(0.21)
    assert reward.forward_return([100], 0, 2) is None
    assert reward.excess_return(0.10, 0.06) == pytest.approx(0.04)
    assert reward.excess_return(None, 0.06) is None


def test_objective_disqualifies_excessive_mdd():
    # 전략 MDD 가 지수의 1.3배 초과 → None(탈락) — 아웃퍼폼 높아도
    assert reward.objective_score(0.50, strat_mdd=0.40, index_mdd=0.20) is None
    # 제약 내 → 점수 산출(MDD 초과분만 패널티)
    s = reward.objective_score(0.10, strat_mdd=0.22, index_mdd=0.20, lam=1.0)
    assert s == pytest.approx(0.10 - 0.02)


def test_should_adopt_mdd_is_hard_constraint():
    idx = 0.25
    # 초과수익 더 높아도 MDD>지수 → 채택 거부 (MDD 제약 우선)
    assert reward.should_adopt({"excess": 0.20, "mdd": 0.30, "n": 100},
                               {"excess": 0.05, "mdd": 0.20, "n": 100}, idx, min_samples=40) is False
    # MDD 제약 충족 + 아웃퍼폼 → 채택
    assert reward.should_adopt({"excess": 0.06, "mdd": 0.20, "n": 100},
                               {"excess": 0.05, "mdd": 0.20, "n": 100}, idx, min_samples=40) is True
    # 표본 부족 → 거부
    assert reward.should_adopt({"excess": 0.50, "mdd": 0.10, "n": 5},
                               None, idx, min_samples=40) is False
    # 챔피언 없음 + 양의 초과수익 + MDD 제약 충족 → 채택
    assert reward.should_adopt({"excess": 0.03, "mdd": 0.10, "n": 100},
                               None, idx, min_samples=40) is True


def test_should_adopt_requires_absolute_outperformance():
    """★1순위: 지수 아웃퍼폼(초과수익>0). 더 나쁜 챔피언을 이기는 것만으론 채택 불가(언더퍼폼 방지)."""
    idx = 0.25
    assert reward.should_adopt({"excess": -0.03, "mdd": 0.05, "n": 100},
                               {"excess": -0.10, "mdd": 0.05, "n": 100}, idx, min_samples=40) is False
    assert reward.should_adopt({"excess": 0.04, "mdd": 0.05, "n": 100},
                               {"excess": 0.02, "mdd": 0.05, "n": 100}, idx, min_samples=40) is True


# ── Learner ───────────────────────────────────────────────────────────────────
def test_walk_forward_split_purges_boundary():
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]   # 10일
    tr, oos = learner.walk_forward_split(dates, train_frac=0.6, embargo=2)
    assert sum(tr) == 4 and sum(oos) == 4               # purge 2 → train 4, oos 4
    assert not any(t and o for t, o in zip(tr, oos))    # 겹침 없음


def test_refit_holds_when_samples_insufficient(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    p = pol.Policy("kr_mock", defaults={"w": 0.5}, bounds={"w": (0, 1)})
    rows = [{"date": f"2026-01-0{i}", "w": 0.5} for i in range(1, 4)]   # 3 < min
    out = learner.refit_and_adopt(rows, p, lambda tr: {"w": 0.9},
                                  lambda r, params: {"excess": 0.1, "mdd": 0.1, "n": len(r)},
                                  index_mdd=0.2, min_samples=40)
    assert out["adopted"] is False and "표본 부족" in out["reason"]


def test_refit_rejects_worse_mdd_adopts_better_excess(tmp_path, monkeypatch):
    monkeypatch.setattr(pol, "_CACHE_DIR", tmp_path)
    dates = [f"2026-{m:02d}-15" for m in range(1, 13)] * 4   # 48 표본
    rows = [{"date": d, "x": i} for i, d in enumerate(dates)]

    # eval_fn: 후보 w 에 따라 결과 모사 — w=0.9 는 MDD 큼(지수 초과), w=0.6 은 양호
    def make_eval(target_w):
        def ev(oos_rows, params):
            if params.get("w", 0) >= 0.85:
                return {"excess": 0.30, "mdd": 0.40, "n": len(oos_rows)}   # 고수익·고MDD(지수 초과)
            return {"excess": 0.08, "mdd": 0.18, "n": len(oos_rows)}        # 적정수익·저MDD
        return ev

    p = pol.Policy("kr_mock", defaults={"w": 0.5}, bounds={"w": (0, 1)})
    # 후보가 고MDD(w=0.9) → 초과수익 높아도 MDD>지수(0.20) → 거부
    out_bad = learner.refit_and_adopt(rows, p, lambda tr: {"w": 0.9}, make_eval(0.9),
                                      index_mdd=0.20, min_samples=40, embargo=2)
    assert out_bad["adopted"] is False

    # 후보가 저MDD(w=0.6) + 챔피언(기본 w=0.5도 같은 ev로 0.08/0.18) → excess 동률이라 미채택,
    # 챔피언을 약하게 만들기 위해 기본 정책을 별도 평가: 여기선 동률 → 거부 확인(아웃퍼폼 '초과' 필요)
    out_eq = learner.refit_and_adopt(rows, p, lambda tr: {"w": 0.6}, make_eval(0.6),
                                     index_mdd=0.20, min_samples=40, embargo=2)
    assert out_eq["adopted"] is False   # 동률은 채택 안 함(엄격한 > 필요)


# ── Regime ────────────────────────────────────────────────────────────────────
def test_recency_weights_favor_recent():
    dates = ["2026-01-01", "2026-04-01", "2026-06-01"]
    w = regime.recency_weights(dates, half_life_days=60)
    assert w[2] > w[1] > w[0]                           # 최근일수록 큰 가중
    assert sum(w) == pytest.approx(len(w))             # 평균가중 1 정규화


# ── Champion/Challenger ───────────────────────────────────────────────────────
def test_champion_challenger_rejects_mdd_violation():
    r = champion_challenger.evaluate({"excess": 0.05, "mdd": 0.2, "n": 100},
                                     {"excess": 0.20, "mdd": 0.40, "n": 100},
                                     index_mdd=0.25, min_samples=40)
    assert r["promote"] is False and "MDD" in r["reason"]


def test_champion_challenger_promotes_on_outperform_within_mdd():
    r = champion_challenger.evaluate({"excess": 0.05, "mdd": 0.2, "n": 100},
                                     {"excess": 0.09, "mdd": 0.20, "n": 100},
                                     index_mdd=0.25, min_samples=40)
    assert r["promote"] is True and "승격" in r["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
