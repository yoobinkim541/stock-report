"""test_evolution.py — 진화 텔레메트리 (순수·tmp JSONL·무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.adaptive import evolution


def _buys(n, ic_sign=1, cum=0.02):
    """policy_score↔fwd_excess 상관 + 평균 cum 인 성숙 매수 결정 생성."""
    rows = []
    for i in range(n):
        ps = i / n
        fe = (ps - 0.5) * 0.1 * ic_sign + cum
        rows.append({"side": "편입", "policy_score": ps, "fwd_excess": fe, "correct": fe > 0})
    return rows


def test_snapshot_ic_hit_cum():
    rows = _buys(20, ic_sign=1, cum=0.03)
    s = evolution.snapshot(rows)
    assert s["n"] == 20
    assert s["realized_ic"] is not None and s["realized_ic"] > 0.5   # 양상관
    assert s["cum_net_excess"] == round(sum(r["fwd_excess"] for r in rows) / 20, 4)
    assert s["buy_hit"] is not None


def test_snapshot_ignores_sells_and_immature():
    rows = [{"side": "퇴출", "policy_score": 0.9, "fwd_excess": 0.05},   # 매도 제외
            {"side": "편입", "policy_score": 0.5, "fwd_excess": None}]     # 미성숙 제외
    assert evolution.snapshot(rows)["n"] == 0


def test_verdict_cold_start():
    assert evolution.verdict({"n": 5, "realized_ic": None, "cum_net_excess": None})["code"] == "cold"


def test_verdict_edge():
    assert evolution.verdict({"n": 60, "realized_ic": 0.08, "cum_net_excess": 0.02})["code"] == "edge"


def test_verdict_no_edge():
    assert evolution.verdict({"n": 60, "realized_ic": 0.01, "cum_net_excess": -0.005})["code"] == "noedge"


def test_verdict_observe_mixed():
    # IC 높지만 누적 음수 → 엣지 단정 안 함(관찰)
    assert evolution.verdict({"n": 60, "realized_ic": 0.08, "cum_net_excess": -0.01})["code"] == "observe"


def test_record_read_roundtrip(tmp_path):
    evolution.record_learning("kr_mock", {"date": "2026-07-01", "adopted": True, "n": 10}, base_dir=str(tmp_path))
    evolution.record_learning("kr_mock", {"date": "2026-07-08", "adopted": False, "n": 12}, base_dir=str(tmp_path))
    h = evolution.read_learning("kr_mock", base_dir=str(tmp_path))
    assert len(h) == 2 and h[0]["date"] == "2026-07-01" and h[1]["adopted"] is False


def test_read_missing_empty(tmp_path):
    assert evolution.read_learning("us_mock", base_dir=str(tmp_path)) == []


def test_evolution_summary(tmp_path):
    evolution.record_learning("kr_mock", {"date": "2026-07-01", "adopted": True,
                                          "excess_challenger": 0.02, "realized_ic": 0.06}, base_dir=str(tmp_path))
    out = evolution.evolution_summary("kr_mock", _buys(60, cum=0.03), base_dir=str(tmp_path))
    assert out["surface"] == "kr_mock" and out["snapshot"]["n"] == 60
    assert out["verdict"]["code"] == "edge"
    assert out["n_runs"] == 1 and len(out["series"]) == 1 and len(out["adoptions"]) == 1


# ── 정체(stall) 감지 — "축적 중"과 "멈춤"을 구분 (감사 후속 2026-08-21) ──────────
# 국내 모의가 8/5 이후 16일간 새 결정 0건인데도 verdict 는 "콜드스타트(성숙 14/40)"만
# 표시해 "축적 중"으로 오인됐다. 실제로는 min_hold(60일)+현금0+3종목 조합으로 9/27 까지
# 구조적으로 새 표본이 나올 수 없는 상태 — 기다림이 답이 아닌데 기다리게 만들던 정직성 결함.

def test_stall_days_none_when_no_decisions():
    assert evolution.stall_days([], today="2026-08-21") is None


def test_stall_days_counts_since_last_decision():
    rows = [{"date": "2026-08-01"}, {"date": "2026-08-05"}]
    assert evolution.stall_days(rows, today="2026-08-21") == 16


def test_stall_days_ignores_unparsable_dates():
    rows = [{"date": "2026-08-05"}, {"date": "bad"}, {}]
    assert evolution.stall_days(rows, today="2026-08-21") == 16


def test_verdict_flags_stall_during_cold_start():
    """콜드스타트인데 장기간 새 결정이 없으면 '정체'로 승격 표기(대기 유도 방지)."""
    v = evolution.verdict({"n": 14, "realized_ic": -0.023, "cum_net_excess": 0.045},
                          stall_days=16)
    assert v["code"] == "stalled"
    assert "정체" in v["label"]
    assert "16" in v["note"]


def test_verdict_cold_start_when_recently_active():
    """최근 결정이 있으면 기존대로 콜드스타트(정상 축적)."""
    v = evolution.verdict({"n": 14, "realized_ic": None, "cum_net_excess": None}, stall_days=2)
    assert v["code"] == "cold"


def test_verdict_stall_ignored_when_sample_sufficient():
    """표본이 충분하면 정체 여부와 무관하게 실측 판정을 우선(엣지/무엣지)."""
    v = evolution.verdict({"n": 60, "realized_ic": 0.01, "cum_net_excess": -0.005}, stall_days=99)
    assert v["code"] == "noedge"


# ── 소표본 신뢰구간 — n=14 짜리 IC 를 과대해석하지 않도록 (D) ────────────────────

def test_ic_confidence_interval_wide_for_small_n():
    lo, hi = evolution.ic_ci(0.0, 14)
    assert lo < -0.4 and hi > 0.4          # n=14 면 IC 0 도 ±0.5 대 — 사실상 무정보
    assert lo < 0 < hi


def test_ic_confidence_interval_narrows_with_n():
    w_small = evolution.ic_ci(0.1, 20)
    w_large = evolution.ic_ci(0.1, 500)
    assert (w_small[1] - w_small[0]) > (w_large[1] - w_large[0]) * 3


def test_ic_confidence_interval_none_when_undefined():
    assert evolution.ic_ci(None, 50) is None
    assert evolution.ic_ci(0.1, 3) is None     # n<4 면 산출 불가


def test_snapshot_includes_ic_ci():
    s = evolution.snapshot(_buys(30, ic_sign=1, cum=0.03))
    assert s.get("ic_ci") is not None and len(s["ic_ci"]) == 2
    assert s["ic_ci"][0] <= s["realized_ic"] <= s["ic_ci"][1]


def test_verdict_notes_ci_when_ic_not_significant():
    """IC 신뢰구간이 0 을 포함하면 '유의하지 않음'을 명시(엣지 오주장 방지)."""
    v = evolution.verdict({"n": 60, "realized_ic": 0.06, "cum_net_excess": 0.01,
                           "ic_ci": [-0.19, 0.30]})
    assert "유의" in v["note"]


def test_evolution_summary_reports_stall_from_decisions(tmp_path):
    """summary 가 raw 결정(미성숙 포함)으로 정체를 판정 — training_set(성숙분)만으론
    '마지막 성숙일'이 찍혀 실제 정체를 과소평가한다."""
    evolution.record_learning("kr_mock", {"date": "2026-08-15", "adopted": False},
                              base_dir=str(tmp_path))
    decisions = [{"date": "2026-07-20"}, {"date": "2026-08-05"}]     # 최신 결정 8/05
    out = evolution.evolution_summary("kr_mock", _buys(14, cum=0.01),
                                      base_dir=str(tmp_path),
                                      decisions=decisions, today="2026-08-21")
    assert out["stall_days"] == 16
    assert out["verdict"]["code"] == "stalled"


def test_evolution_summary_without_decisions_keeps_cold(tmp_path):
    """결정 목록을 안 주면 정체 판정 불가 — 기존 동작(콜드스타트) 유지(하위호환)."""
    out = evolution.evolution_summary("kr_mock", _buys(14, cum=0.01), base_dir=str(tmp_path))
    assert out["stall_days"] is None
    assert out["verdict"]["code"] == "cold"


def test_snapshot_counts_shadow_observations():
    """랭킹 섀도(side='관측')도 IC 산출 대상 — 선택편향 없는 전 구간 측정용."""
    rows = [{"side": "관측", "policy_score": i / 10, "fwd_excess": (i - 5) * 0.01, "correct": i > 5}
            for i in range(10)]
    s = evolution.snapshot(rows)
    assert s["n"] == 10 and s["realized_ic"] is not None and s["realized_ic"] > 0.9


# ── 축별 IC — 어느 피처가 실제로 편입/편출을 예측하나 (C) ──────────────────────
# 국내 가중치 0.65(ranker .30 + fund .15 + signal .15 + conf .05)는 25년 백테스트
# 검증 대상에 아예 없었다(가격축만 검증됨). 섀도 원장이 쌓이면 축별로 직접 측정한다.

def _axis_rows(n=60):
    """good 축은 결과와 상관 有, bad 축은 무관(노이즈)."""
    rows = []
    for i in range(n):
        v = i / n
        rows.append({"side": "관측", "policy_score": v, "fwd_excess": (v - 0.5) * 0.2,
                     "features": {"good": v, "bad": (i * 7919 % 97) / 97.0}})
    return rows


def test_axis_ic_ranks_predictive_axis_above_noise():
    out = evolution.axis_ic(_axis_rows())
    assert out["good"]["ic"] > 0.9
    assert abs(out["bad"]["ic"]) < 0.4
    assert out["good"]["n"] == 60


def test_axis_ic_includes_confidence_interval():
    out = evolution.axis_ic(_axis_rows())
    assert out["good"]["ci"] is not None and len(out["good"]["ci"]) == 2
    assert out["good"]["ci"][0] > 0            # 유의하게 양수


def test_axis_ic_skips_axes_below_min_pairs():
    rows = [{"side": "관측", "fwd_excess": 0.01, "features": {"rare": 0.5}} for _ in range(2)]
    assert evolution.axis_ic(rows, min_pairs=5) == {}


def test_axis_ic_ignores_non_buy_and_immature():
    rows = [{"side": "퇴출", "fwd_excess": 0.1, "features": {"x": 0.9}},
            {"side": "관측", "fwd_excess": None, "features": {"x": 0.5}}]
    assert evolution.axis_ic(rows) == {}


def test_axis_ic_handles_missing_features_gracefully():
    rows = [{"side": "관측", "fwd_excess": 0.01} for _ in range(10)]
    assert evolution.axis_ic(rows) == {}
