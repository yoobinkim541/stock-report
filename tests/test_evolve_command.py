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
