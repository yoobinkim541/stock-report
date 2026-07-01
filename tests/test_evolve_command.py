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
