#!/usr/bin/env python3
"""test_entry_feedback.py — entry signal snapshot/outcome ledger."""
import json
import os
import sys

import pandas as pd

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


def test_short_decision_contains_versioned_metadata_and_is_session_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_today_kst", lambda: "2026-08-31")
    monkeypatch.setattr(F, "_now_kst", lambda: "2026-08-31T09:30:00+09:00")
    led = Ledger(F.SURFACE, base_dir=tmp_path)

    kwargs = dict(source="auto_watch", universe="watch", ledger=led,
                  evaluation_profile="short", session="regular",
                  model_version="entry-v3", parameter_version="params-7",
                  feature_version="features-2", freshness_seconds=12)
    assert F.record_entry_scores([_score()], **kwargs) == 1
    assert F.record_entry_scores([_score(score=0.91)], **kwargs) == 0

    row = led.read_decisions()[0]
    assert row["evaluation_profile"] == "short"
    assert row["session"] == "regular"
    assert row["model_version"] == "entry-v3"
    assert row["parameter_version"] == "params-7"
    assert row["feature_version"] == "features-2"
    assert row["freshness_seconds"] == 12
    assert row["event_type"] == "enter"
    assert ":regular:" in row["id"]


def test_build_short_outcome_has_direction_path_and_net_metrics():
    decision = F.score_to_decision(_score(), source="auto_watch", universe="watch",
                                   date="2026-08-29", evaluation_profile="short",
                                   session="regular")
    result = {
        "entry_date": "2026-08-29T09:30:00+09:00",
        "exit_date": "2026-08-29T10:30:00+09:00",
        "entry_price_actual": 126.45,
        "exit_price": 130.0,
        "benchmark_ret": 0.01,
        "stock_ret": 0.0281,
        "fwd_mdd": 0.01,
        "idx_fwd_mdd": 0.005,
        "path_result": "target",
        "path_date": "2026-08-29T10:00:00+09:00",
        "path_price": decision["target_price"],
        "mfe": 0.04,
        "mae": -0.012,
        "time_to_target_minutes": 30,
        "fee_rate": 0.001,
        "slippage_rate": 0.001,
    }
    out = F.build_outcome(decision, "1h", result)

    assert out["horizon"] == "1h"
    assert out["direction_hit"] is True
    assert out["stock_ret"] == out["fwd_ret"]
    assert out["excess_ret"] == out["fwd_excess"]
    assert out["target_first"] is True and out["stop_first"] is False
    assert out["mfe"] == 0.04 and out["mae"] == -0.012
    assert out["time_to_target_minutes"] == 30
    assert out["net_ret"] < out["gross_ret"]


def test_default_intraday_result_records_time_to_level(monkeypatch):
    index = pd.date_range("2026-08-31 09:30", periods=32, freq="min", tz="Asia/Seoul")
    target = 130.0
    stock = pd.DataFrame({
        "Open": [100.0] * 32,
        "High": [100.0, 101.0, 100.0, target] + [100.0] * 28,
        "Low": [100.0] * 32,
        "Close": [100.0] * 32,
        "Volume": [1000] * 32,
    }, index=index)
    benchmark = stock.copy()

    def fake_frame(symbol, date, *, market):
        return stock if symbol == "PLTR" else benchmark

    monkeypatch.setattr(F, "_intraday_frame", fake_frame)
    decision = {
        "ticker": "PLTR", "benchmark": "QQQ", "market": "US",
        "date": "2026-08-31", "snapshot_ts": "2026-08-31T09:30:00+09:00",
        "target_price": target, "stop_price": 95.0,
    }

    result = F._default_intraday_result(decision, "30m")

    assert result["path_result"] == "target"
    assert result["time_to_target_minutes"] == 3
    assert result["time_to_target"] == 3
    assert result["time_to_stop_minutes"] is None


def test_intraday_symbol_preserves_domestic_index_benchmark():
    assert F._intraday_symbol("^KS11", "KR") == "^KS11"
    assert F._intraday_symbol("005930", "KR") == "005930.KS"
    assert F._intraday_symbol("PLTR", "US") == "PLTR"


def test_default_price_result_records_time_to_level(monkeypatch):
    index = pd.date_range("2026-08-25", periods=21, freq="D")
    target = 130.0
    stock = pd.DataFrame({
        "Open": [100.0] * 21,
        "High": [100.0, 101.0, target] + [100.0] * 18,
        "Low": [100.0] * 21,
        "Close": [100.0] * 21,
        "Volume": [1000] * 21,
    }, index=index)
    benchmark = stock.copy()
    monkeypatch.setattr(
        "ml.data_pipeline.fetch_prices",
        lambda tickers, days: {"PLTR": stock, "QQQ": benchmark},
    )
    decision = {
        "ticker": "PLTR", "benchmark": "QQQ", "market": "US",
        "date": "2026-08-25", "target_price": target, "stop_price": 95.0,
    }

    result = F._default_price_result(decision, "20d")

    assert result["path_result"] == "target"
    assert result["time_to_target_minutes"] == 2 * 24 * 60
    assert result["time_to_target"] == 2 * 24 * 60


def test_backfill_skips_pending_result_and_accepts_string_horizon(tmp_path):
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    F.record_entry_scores([_score()], source="auto_watch", universe="watch", ledger=led)
    calls = []

    def pending_then_ready(decision, horizon):
        calls.append(horizon)
        if horizon == "30m":
            return {"status": "pending"}
        return {
            "entry_date": "2026-07-11", "exit_date": "2026-07-11",
            "entry_price_actual": 126.45, "exit_price": 127.0,
            "benchmark_ret": 0.0, "stock_ret": 0.004,
            "path_result": "none", "fwd_mdd": 0, "idx_fwd_mdd": 0,
        }

    assert F.backfill_outcomes(ledger=led, horizons=("30m",), price_fn=pending_then_ready) == 0
    assert led.read_outcomes() == []
    assert calls == ["30m"]


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


def _training_row(i: int, *, confirmed: bool) -> dict:
    return {
        "id": f"2026-01-{(i % 28) + 1:02d}:test:watch:{'GOOD' if confirmed else 'BAD'}{i}",
        "date": f"2026-01-{(i % 28) + 1:02d}",
        "ticker": f"{'GOOD' if confirmed else 'BAD'}{i}",
        "signal": "enter",
        "score": 0.70 if confirmed else 0.75,
        "success": confirmed,
        "r_multiple": 1.5 if confirmed else -1.0,
        "features": {
            "technical_rating": "🟢 매수" if confirmed else "🔴 매도",
            "pivot_position": "above_p" if confirmed else "below_p",
            "mom_20d": 0.04 if confirmed else -0.03,
            "mom_60d": 0.08 if confirmed else -0.06,
            "vix": 16.0,
            "n_similar": 32,
            "win_prob_20d": 0.64,
            "win_prob_60d": 0.70 if confirmed else 0.55,
            "drawdown": -0.18,
            "reward_risk": 1.8,
        },
    }


def test_learn_feedback_adjustments_adopts_validated_model(tmp_path):
    rows = []
    for i in range(20):
        rows.append(_training_row(i, confirmed=False))
        rows.append(_training_row(i, confirmed=True))

    model_path = tmp_path / "entry_feedback_adjustments.json"
    result = F.learn_feedback_adjustments(rows=rows, save=True, path=model_path)

    assert result["adopted"] is True
    assert result["adjustments"]["technical_conflict"] < 0
    assert result["adjustments"]["technical_confirmed"] > 0
    assert result["challenger"]["excess"] > result["champion"]["excess"]

    saved = json.loads(model_path.read_text())
    assert saved["adjustments"]["technical_conflict"] < 0
    assert saved["meta"]["oos_n"] == result["oos_n"]


def test_apply_score_adjustment_uses_saved_model(tmp_path):
    model_path = tmp_path / "entry_feedback_adjustments.json"
    model_path.write_text(json.dumps({
        "version": 1,
        "adjustments": {
            "technical_conflict": -0.03,
            "pivot_not_recovered": -0.02,
        },
        "meta": {},
    }))
    context = {
        "features": {
            "technical_rating": "🔴 매도",
            "pivot_position": "below_p",
            "mom_20d": 0.02,
            "mom_60d": 0.03,
            "vix": 15.0,
            "n_similar": 30,
            "win_prob_20d": 0.60,
            "win_prob_60d": 0.70,
            "drawdown": -0.12,
            "reward_risk": 1.5,
        }
    }

    adjusted, delta, factors = F.apply_score_adjustment(0.75, context, path=model_path)

    assert adjusted == 0.70
    assert delta == -0.05
    assert "technical_conflict" in factors
    assert "pivot_not_recovered" in factors


def test_apply_score_adjustment_can_be_disabled(tmp_path):
    model_path = tmp_path / "entry_feedback_adjustments.json"
    model_path.write_text(json.dumps({
        "adjustments": {"technical_conflict": -0.03},
        "meta": {},
    }))
    adjusted, delta, factors = F.apply_score_adjustment(
        0.75,
        {"features": {"technical_rating": "🔴 매도", "n_similar": 30}},
        path=model_path,
        enabled=False,
    )
    assert adjusted == 0.75
    assert delta == 0.0
    assert factors == ["technical_conflict"]


def test_eval_adjustments_uses_realized_excess_and_average_loss():
    rows = [
        {"score": 0.70, "fwd_excess": 0.04, "fwd_mdd": 0.03, "r_multiple": 2.0, "success": True},
        {"score": 0.70, "fwd_excess": -0.02, "fwd_mdd": 0.10, "r_multiple": -0.5, "success": False},
    ]

    result = F._eval_adjustments(rows, {}, threshold=0.62)

    assert result["excess"] == 0.01
    assert result["avg_loss"] == 0.02
    assert result["mdd"] == 0.10


def test_oos_constraints_reject_mdd_or_average_loss_regression():
    champion = {"excess": 0.01, "mdd": 0.05, "avg_loss": 0.02}
    assert F._oos_constraints_ok(
        {"excess": 0.03, "mdd": 0.06, "avg_loss": 0.02, "n": 10}, champion
    ) is False
    assert F._oos_constraints_ok(
        {"excess": 0.03, "mdd": 0.05, "avg_loss": 0.03, "n": 10}, champion
    ) is False
    assert F._oos_constraints_ok(
        {"excess": 0.03, "mdd": 0.05, "avg_loss": 0.02, "n": 10}, champion
    ) is True
