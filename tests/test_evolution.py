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
