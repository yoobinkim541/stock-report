"""test_evolve_command.py — /evolve 렌더 (무네트워크·mock ledger)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot import evolve_command


def test_evolve_cold_start(monkeypatch, tmp_path):
    from ml.adaptive import Ledger, evolution
    monkeypatch.setattr(Ledger, "training_set", lambda self: [])
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))
    txt = evolve_command.build_evolve_report()
    assert "진화" in txt and "🇰🇷" in txt and "🇺🇸" in txt
    assert "콜드스타트" in txt and "실거래 미반영" in txt


def test_evolve_with_data_shows_ic(monkeypatch, tmp_path):
    from ml.adaptive import Ledger, evolution
    rows = [{"side": "편입", "policy_score": i / 60, "fwd_excess": (i / 60 - 0.5) * 0.1 + 0.03,
             "correct": True} for i in range(60)]
    monkeypatch.setattr(Ledger, "training_set", lambda self: rows)
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))
    txt = evolve_command.build_evolve_report()
    assert "성숙 60건" in txt and "순비용 IC" in txt


def test_evolve_shows_rank_shadow_ic(monkeypatch, tmp_path):
    """섀도(전 후보) IC 를 별도 줄로 노출 — 라이브 IC 는 구간제한으로 감쇠돼 있으므로."""
    from ml.adaptive import Ledger, evolution
    shadow = [{"side": "관측", "policy_score": i / 80, "fwd_excess": (i / 80 - 0.5) * 0.2,
               "correct": i > 40} for i in range(80)]

    def _ts(self):
        return shadow if self.surface.endswith("_shadow") else []
    monkeypatch.setattr(Ledger, "training_set", _ts)
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))

    txt = evolve_command.build_evolve_report()
    assert "🔬" in txt and "섀도(전 후보) 80건" in txt
    assert "95%CI" in txt


def test_evolve_shows_significant_axes(monkeypatch, tmp_path):
    """미검증 축(ranker 등)이 실제 예측력 있는지 축별 IC 로 노출."""
    from ml.adaptive import Ledger, evolution
    shadow = [{"side": "관측", "policy_score": i / 80, "fwd_excess": (i / 80 - 0.5) * 0.2,
               "correct": i > 40,
               "features": {"ranker": i / 80, "noise": (i * 7919 % 97) / 97.0}}
              for i in range(80)]

    def _ts(self):
        return shadow if self.surface.endswith("_shadow") else []
    monkeypatch.setattr(Ledger, "training_set", _ts)
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))

    txt = evolve_command.build_evolve_report()
    assert "📐 유의 축" in txt and "ranker" in txt


def test_evolve_shadow_pending_shows_wait_notice(monkeypatch, tmp_path):
    """섀도 적재는 됐지만 아직 미성숙이면 '성숙 대기'로 정직 표기."""
    from ml.adaptive import Ledger, evolution
    monkeypatch.setattr(Ledger, "training_set", lambda self: [])
    monkeypatch.setattr(Ledger, "read_decisions",
                        lambda self: ([{"date": "2026-08-21"}] * 20
                                      if self.surface.endswith("_shadow") else []))
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))

    txt = evolve_command.build_evolve_report()
    assert "섀도 적재 20건" in txt and "성숙 대기" in txt


def test_evolve_shows_offline_backtest_verdict(monkeypatch, tmp_path):
    """오프라인 25년 백테스트 판정을 라이브 옆에 병기 — 라이브 콜드스타트를 단독 해석하면
    이미 오프라인에서 약하다고 나온 신호를 '아직 모른다'로 오인한다."""
    import json
    from ml.adaptive import Ledger, evolution
    monkeypatch.setattr(Ledger, "training_set", lambda self: [])
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "kr_policy_backtest.json").write_text(json.dumps({
        "asof": "2026-08-15", "period": "2001~2026",
        "verdict": {"code": "OBSERVE", "net_excess_cagr": 0.0429, "dsr": 0.0802,
                    "pbo": 0.1865, "mdd_ok": True}}), encoding="utf-8")
    monkeypatch.setattr(evolve_command, "_BACKTEST_DIR", str(cache))

    txt = evolve_command.build_evolve_report()
    assert "OBSERVE" in txt and "DSR" in txt


def test_evolve_offline_verdict_absent_is_graceful(monkeypatch, tmp_path):
    from ml.adaptive import Ledger, evolution
    monkeypatch.setattr(Ledger, "training_set", lambda self: [])
    monkeypatch.setattr(evolution, "_DIR", str(tmp_path))
    monkeypatch.setattr(evolve_command, "_BACKTEST_DIR", str(tmp_path / "nope"))
    txt = evolve_command.build_evolve_report()
    assert "진화" in txt          # 예외 없이 렌더
