#!/usr/bin/env python3
"""test_entry_feedback.py — entry signal snapshot/outcome ledger."""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from ml.adaptive import Ledger  # noqa: E402
from ml.entry_analyzer import EntryScore  # noqa: E402
from ml import entry_feedback as F  # noqa: E402


def _score(**overrides):
    base = dict(
        ticker="PLTR",
        category="stock",
        underlying="PLTR",
        current_drawdown=-0.39,
        current_rsi=48,
        current_vix=15.3,
        current_mom_20d=-0.029,
        current_mom_60d=-0.068,
        current_price=126.45,
        n_similar=24,
        win_prob_20d=0.64,
        win_prob_60d=0.84,
        expected_ret_20d=0.095,
        expected_ret_60d=0.278,
        downside_p25_20d=-0.037,
        upside_p75_20d=0.351,
        score=0.75,
        signal="enter",
        reasons=["승률 64% (보통)", "손익비 2.6× (양호)"],
        timestamp="2026-07-11 00:00 KST",
        technical_rating="🔴 매도",
        technical_score=-0.3,
        pivot_p=128.91,
        pivot_position="below_p",
    )
    base.update(overrides)
    return EntryScore(**base)


def test_record_entry_scores_is_daily_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_today_kst", lambda: "2026-07-11")
    monkeypatch.setattr(F, "_now_kst", lambda: "2026-07-11T09:00:00+09:00")
    led = Ledger(F.SURFACE, base_dir=tmp_path)

    assert F.record_entry_scores([_score()], source="auto_watch", universe="watch", ledger=led) == 1
    assert F.record_entry_scores([_score(score=0.8)], source="auto_watch", universe="watch", ledger=led) == 0

    rows = led.read_decisions()
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "PLTR"
    assert row["signal"] == "enter"
    assert row["features"]["technical_rating"] == "🔴 매도"
    assert row["target_price"] > row["current_price"] > row["stop_price"]


def test_backfill_outcomes_adds_diagnosis_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_today_kst", lambda: "2026-08-15")
    monkeypatch.setattr(F, "_now_kst", lambda: "2026-07-11T09:00:00+09:00")
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    F.record_entry_scores([_score()], source="auto_watch", universe="watch", ledger=led)

    def fake_price_result(decision, horizon):
        assert horizon == 20
        return {
            "entry_date": "2026-07-11",
            "exit_date": "2026-08-10",
            "entry_price_actual": 126.45,
            "exit_price": 120.0,
            "benchmark_ret": 0.03,
            "stock_ret": -0.051,
            "fwd_mdd": 0.08,
            "idx_fwd_mdd": 0.02,
            "path_result": "stop",
            "path_date": "2026-07-20",
            "path_price": 121.81,
        }

    assert F.backfill_outcomes(ledger=led, horizons=(20,), price_fn=fake_price_result) == 1
    assert F.backfill_outcomes(ledger=led, horizons=(20,), price_fn=fake_price_result) == 0

    out = led.read_outcomes()[0]
    assert out["decision_id"].endswith(":h20")
    assert out["success"] is False
    assert out["diagnosis"] == "무효화선 이탈"
    assert "technical_conflict" in out["factor_tags"]
    assert "pivot_not_recovered" in out["factor_tags"]

    rows = F.training_rows(ledger=led, horizon=20)
    assert len(rows) == 1 and rows[0]["ticker"] == "PLTR"
    summary = F.summarize_feedback(rows, horizon=20)
    assert summary["n"] == 1
    assert summary["success_rate"] == 0.0
    assert ("technical_conflict", 1) in summary["top_failure_factors"]
    assert "20일 표본 1건" in F.format_feedback_summary(summary)


def test_build_outcome_marks_target_success():
    decision = F.score_to_decision(_score(technical_rating="🟢 매수", pivot_position="above_p"),
                                   source="manual", universe="single", date="2026-07-11")
    result = {
        "entry_date": "2026-07-11",
        "exit_date": "2026-08-10",
        "entry_price_actual": 126.45,
        "exit_price": 155.0,
        "benchmark_ret": 0.02,
        "stock_ret": 0.226,
        "fwd_mdd": 0.01,
        "idx_fwd_mdd": 0.02,
        "path_result": "target",
        "path_date": "2026-07-25",
        "path_price": decision["target_price"],
    }
    out = F.build_outcome(decision, 20, result)
    assert out["success"] is True
    assert out["diagnosis"] == "목표 도달"
    assert "technical_confirmed" in out["factor_tags"]
    assert "pivot_confirmed" in out["factor_tags"]
