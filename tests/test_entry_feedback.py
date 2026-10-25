#!/usr/bin/env python3
"""test_entry_feedback.py — entry signal snapshot/outcome ledger."""
import json
import os
import sys

import pandas as pd
import pytest

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


def test_invalid_benchmark_price_cannot_enter_learning_rows(tmp_path):
    decision = F.score_to_decision(_score(), source="test", universe="watch", date="2026-07-11")
    result = {"stock_ret": 0.1, "benchmark_ret": 0.0, "entry_price_actual": 100.0,
              "exit_price": 110.0, "benchmark_entry": 0.0, "benchmark_exit": 100.0,
              "path_result": "none"}
    outcome = F.build_outcome(decision, 20, result)
    assert outcome["status"] == "invalid"
    assert "benchmark_entry" in outcome["quality_reason"]
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    led.log_decision(decision)
    led.log_outcome(outcome)
    assert F.training_rows(ledger=led, horizon=20) == []


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
    assert out["decision_id"].endswith(":h20d")
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


def test_training_rows_dedupes_legacy_and_canonical_outcomes(tmp_path):
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    led.log_decision({"id": "d1", "date": "2026-07-11", "ticker": "PLTR", "signal": "enter"})
    valid = {"fwd_ret": .1, "fwd_excess": .05, "benchmark_ret": .05,
             "entry_price_actual": 100, "exit_price": 110, "path_result": "none"}
    led.log_outcome({**valid, "decision_id": "d1:h20", "horizon": 20, "status": "matured", "success": False})
    led.log_outcome({**valid, "decision_id": "d1:h20d", "horizon": "20d", "status": "matured", "matured_at": "2026-08-10", "success": True})

    rows = F.training_rows(ledger=led, horizon=20)

    assert len(rows) == 1
    assert rows[0]["decision_id"] == "d1:h20d"


@pytest.mark.parametrize("field,value", [("stock_ret", float("nan")),
    ("benchmark_ret", float("inf")), ("exit_price", float("-inf")),
    ("entry_price_actual", 0), ("path_result", None), ("stock_ret", None)])
def test_corrupt_evaluable_outcome_is_invalid_and_append_only(tmp_path, field, value):
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    decision = F.score_to_decision(_score(), source="test", universe="test")
    led.log_decision(decision)
    result = {"stock_ret": .1, "benchmark_ret": .02, "entry_price_actual": 100,
              "exit_price": 110, "path_result": "none", field: value}
    out = F.build_outcome(decision, 20, result)
    assert out["status"] == "invalid"
    assert out["success"] is None
    json.dumps(out, allow_nan=False)
    assert F.backfill_outcomes(ledger=led, horizons=(20, "20d"), price_fn=lambda *a: result) == 1
    before = list(tmp_path.glob("*outcomes.jsonl"))[0].read_bytes()
    assert F.training_rows(ledger=led) == []
    assert F.backfill_outcomes(ledger=led, horizons=(20,), price_fn=lambda *a: result) == 0
    assert list(tmp_path.glob("*outcomes.jsonl"))[0].read_bytes() == before


def test_training_excludes_incomplete_legacy_rows(tmp_path):
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    led.log_decision({"id": "d", "ticker": "A"})
    led.log_outcome({"decision_id": "d:h20", "horizon": 20, "status": "matured", "success": True})
    assert F.training_rows(ledger=led) == []


def test_entry_records_common_snapshots_for_wait_and_enter(tmp_path):
    led = Ledger(F.SURFACE, base_dir=tmp_path)
    F.record_entry_scores([_score(), _score(ticker="B", signal="wait")], ledger=led)
    rows = led.read_decisions()
    assert [r["selection_status"] for r in rows] == ["selected", "shadow"]
    for row in rows:
        assert row["recommendation_v2_shadow"]["horizon"] == "20d"
        assert row["recommendation_v2_shadow"]["expected_excess"] is None
        assert "source_coverage" in row and "data_quality" in row


def test_summary_excludes_non_finite_metrics_but_retains_rows():
    rows = [
        {"success": True, "fwd_excess": float("nan"), "r_multiple": float("inf"), "stock_ret": 0.1},
        {"success": False, "fwd_excess": 0.04, "r_multiple": 1.0, "stock_ret": 0.2},
    ]

    summary = F.summarize_feedback(rows, horizon=20)

    assert summary["n"] == 2
    assert summary["avg_excess"] == 0.04
    assert summary["avg_r"] == 1.0
    assert summary["avg_stock_ret"] == 0.15


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


def _training_row(i: int, *, confirmed: bool, bad_score: float = 0.75, label_days: int = 1) -> dict:
    """i일차(2026-01-01+i) 결정. 라벨은 label_days 뒤 exit·성숙(이용가능)."""
    day = pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)
    available = (day + pd.Timedelta(days=label_days)).strftime("%Y-%m-%d")
    name = f"{'GOOD' if confirmed else 'BAD'}{i}"
    return {
        "id": f"{day:%Y-%m-%d}:test:watch:{name}",
        "date": f"{day:%Y-%m-%d}",
        "ticker": name,
        "signal": "enter",
        "score": 0.70 if confirmed else bad_score,
        "success": confirmed,
        "r_multiple": 1.5 if confirmed else -1.0,
        "exit_date": available,
        "matured_at": available,
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


@pytest.fixture
def enter_threshold(monkeypatch):
    import ml.entry_analyzer as analyzer
    monkeypatch.setattr(analyzer, "get_score_params", lambda: {"enter_threshold": 0.62})
    return 0.62


def test_learn_feedback_adjustments_adopts_validated_model(tmp_path, enter_threshold):
    """실패 그룹 점수 .68 → 학습 보정(-0.08 cap)이 진입 문턱 .62 를 넘겨 실패 그룹을 걸러낸다."""
    rows = []
    for i in range(20):
        rows.append(_training_row(i, confirmed=False, bad_score=0.68))
        rows.append(_training_row(i, confirmed=True))

    model_path = tmp_path / "entry_feedback_adjustments.json"
    result = F.learn_feedback_adjustments(rows=rows, save=True, path=model_path)

    assert result["adopted"] is True
    assert result["adjustments"]["technical_conflict"] < 0
    assert result["adjustments"]["technical_confirmed"] > 0
    assert result["challenger"]["excess"] > result["champion"]["excess"]
    assert result["train_last_date"] < result["oos_first_date"]

    saved = json.loads(model_path.read_text())
    assert saved["adjustments"]["technical_conflict"] < 0
    assert saved["meta"]["oos_n"] == result["oos_n"]
    assert saved["meta"]["purged_n"] == result["purged_n"]


def test_learn_feedback_adjustments_holds_when_capped_adjustment_cannot_change_selection(
        tmp_path, enter_threshold):
    """실패 그룹 .75 − 총보정 cap .08 = .67 ≥ .62 → 선택 불변 → 동률은 채택 거부(생산 규칙 유지)."""
    rows = []
    for i in range(20):
        rows.append(_training_row(i, confirmed=False))
        rows.append(_training_row(i, confirmed=True))

    model_path = tmp_path / "entry_feedback_adjustments.json"
    result = F.learn_feedback_adjustments(rows=rows, save=True, path=model_path)

    assert result["adjustments"]["technical_conflict"] < 0
    assert result["challenger"] == result["champion"]
    assert result["adopted"] is False
    assert "개선 부족" in result["reason"]
    assert not model_path.exists()


def test_learn_feedback_split_uses_whole_days_and_purges_unavailable_labels(monkeypatch, enter_threshold):
    """A-fix-learn: 같은 날 다종목·20일 중첩 라벨·뒤늦게 성숙한 라벨·가용시각 없는 라벨."""
    rows = []
    for i in range(40):
        rows.append(_training_row(i, confirmed=True, label_days=3))
        rows.append(_training_row(i, confirmed=False, label_days=20))
    late = _training_row(0, confirmed=True, label_days=3)
    late.update(ticker="LATE", id="2026-01-01:test:watch:LATE", matured_at="2026-03-31")
    unknown = _training_row(1, confirmed=True)
    unknown.update(ticker="UNKNOWN", id="2026-01-02:test:watch:UNKNOWN")
    del unknown["exit_date"], unknown["matured_at"]
    rows += [late, unknown]
    seen = {}

    def capture(train):
        seen["train"] = list(train)
        return {}
    monkeypatch.setattr(F, "_fit_factor_adjustments", capture)

    result = F.learn_feedback_adjustments(rows=rows, save=False)

    train = seen["train"]
    # 경계는 결정일 단위: 학습 행의 날짜는 모두 첫 OOS 결정일 이전(같은 날이 양쪽에 걸치지 않음)
    assert result["train_last_date"] < result["oos_first_date"]
    assert all(r["date"] < result["oos_first_date"] for r in train)
    oos_start = pd.Timestamp(result["oos_start_at"])
    for row in train:
        available = pd.Timestamp(max(row["exit_date"], row["matured_at"])) + pd.Timedelta(days=1)
        assert available.tz_localize("UTC") <= oos_start          # 가용(그날 끝) ≤ 첫 OOS 결정
    tickers = {r["ticker"] for r in train}
    assert "LATE" not in tickers and "UNKNOWN" not in tickers
    assert result["purged_n"] >= 2
    assert result["train_n"] + result["oos_n"] + result["purged_n"] == len(rows)


def _minute_frame(start: str, periods: int, step_min: int, close_fn, tz="Asia/Seoul") -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq=f"{step_min}min", tz=tz)
    closes = [close_fn(i * step_min) for i in range(periods)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1000] * periods}, index=index)


_SHORT_DECISION = {"ticker": "PLTR", "benchmark": "QQQ", "market": "US", "date": "2026-08-31",
                   "snapshot_ts": "2026-08-31T09:30:00+09:00", "target_price": 1e9, "stop_price": 1.0}


def test_intraday_outcome_uses_same_exit_time_for_stock_and_benchmark(monkeypatch):
    """A-fix-learn: 벤치마크가 2분 간격이어도 30분 창은 양쪽 모두 09:30→10:00."""
    stock = _minute_frame("2026-08-31 09:30", 71, 1, lambda m: 100.0 + 0.01 * m)
    bench = _minute_frame("2026-08-31 09:30", 36, 2, lambda m: 100.0 + 0.02 * m)
    monkeypatch.setattr(F, "_intraday_frame",
                        lambda symbol, date, *, market: stock if symbol == "PLTR" else bench)

    result = F._default_intraday_result(dict(_SHORT_DECISION), "30m")

    assert result["exit_date"].startswith("2026-08-31T10:00")
    assert result["benchmark_exit_date"].startswith("2026-08-31T10:00")
    assert result["stock_ret"] == pytest.approx(0.3 / 100.0)
    assert result["benchmark_ret"] == pytest.approx(0.6 / 100.0)     # 60분 벤치(1.2%) 아님


def test_intraday_outcome_with_stock_gap_is_not_stretched_to_longer_horizon(monkeypatch):
    """종목 봉이 09:40 이후 10:30 까지 비면 30분 창을 봉 31개로 세어 60분+로 늘리지 않는다."""
    full = _minute_frame("2026-08-31 09:30", 91, 1, lambda m: 100.0 + 0.01 * m)
    gap = (full.index <= pd.Timestamp("2026-08-31 09:40+09:00")) | (
        full.index >= pd.Timestamp("2026-08-31 10:30+09:00"))
    stock = full[gap]
    monkeypatch.setattr(F, "_intraday_frame",
                        lambda symbol, date, *, market: stock if symbol == "PLTR" else full)

    result = F._default_intraday_result(dict(_SHORT_DECISION), "30m")

    assert result["status"] == "invalid" and result["quality_reason"] == "exit_bar_missing"
    decision = {**_SHORT_DECISION, "id": "d-gap"}
    outcome = F.build_outcome(decision, "30m", result)
    assert outcome["status"] == "invalid" and outcome["quality_reason"] == "exit_bar_missing"


def test_intraday_outcome_pending_until_window_covered_then_invalid(monkeypatch):
    stock = _minute_frame("2026-08-31 09:30", 21, 1, lambda m: 100.0)     # 09:50 까지만
    monkeypatch.setattr(F, "_intraday_frame", lambda symbol, date, *, market: stock)

    monkeypatch.setattr(F, "_now_utc", lambda: pd.Timestamp("2026-08-31T01:00:00Z"))
    assert F._default_intraday_result(dict(_SHORT_DECISION), "30m") is None

    monkeypatch.setattr(F, "_now_utc", lambda: pd.Timestamp("2026-09-02T01:00:00Z"))
    late = F._default_intraday_result(dict(_SHORT_DECISION), "30m")
    assert late["status"] == "invalid" and late["quality_reason"] == "window_not_covered"


def _daily_frame(dates, close_fn) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    closes = [close_fn(i) for i in range(len(index))]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1000] * len(index)}, index=index)


def _patch_daily(monkeypatch, stock, bench):
    monkeypatch.setattr("ml.data_pipeline.fetch_prices",
                        lambda tickers, days: {"PLTR": stock, "QQQ": bench})


_DAILY_DECISION = {"ticker": "PLTR", "benchmark": "QQQ", "market": "US", "date": "2026-08-03"}


def test_daily_outcome_compares_same_trading_days_when_stock_has_mid_window_gap(monkeypatch):
    days = pd.bdate_range("2026-08-03", periods=30)
    bench = _daily_frame(days, lambda i: 100.0 + i)
    stock_days = [d for k, d in enumerate(days) if k not in (3, 4)]      # 2일 거래정지(창 중간)
    stock = _daily_frame(stock_days, lambda i: 50.0 + i)
    _patch_daily(monkeypatch, stock, bench)

    result = F._default_price_result(dict(_DAILY_DECISION), "5d")

    assert result["entry_date"] == result["benchmark_entry_date"] == "2026-08-03"
    assert result["exit_date"] == result["benchmark_exit_date"] == days[5].strftime("%Y-%m-%d")
    assert result["benchmark_ret"] == pytest.approx(105.0 / 100.0 - 1.0)


def test_daily_outcome_halted_on_exit_day_is_invalid(monkeypatch):
    days = pd.bdate_range("2026-08-03", periods=30)
    bench = _daily_frame(days, lambda i: 100.0 + i)
    stock = _daily_frame([d for k, d in enumerate(days) if k != 5], lambda i: 50.0 + i)
    _patch_daily(monkeypatch, stock, bench)

    result = F._default_price_result(dict(_DAILY_DECISION), "5d")

    assert result["status"] == "invalid" and result["quality_reason"] == "end_bar_missing"


def test_daily_outcome_with_different_start_day_is_invalid(monkeypatch):
    days = pd.bdate_range("2026-08-03", periods=30)
    bench = _daily_frame(days, lambda i: 100.0 + i)
    stock = _daily_frame(days[2:], lambda i: 50.0 + i)                  # 결정일 이후 이틀 늦게 시작
    _patch_daily(monkeypatch, stock, bench)

    result = F._default_price_result(dict(_DAILY_DECISION), "5d")

    assert result["status"] == "invalid" and result["quality_reason"] == "start_bar_missing"


def test_daily_outcome_pending_while_stock_data_lags(monkeypatch):
    days = pd.bdate_range("2026-08-03", periods=30)
    bench = _daily_frame(days, lambda i: 100.0 + i)
    stock = _daily_frame(days[:4], lambda i: 50.0 + i)                  # 아직 종료일 데이터 없음
    _patch_daily(monkeypatch, stock, bench)

    assert F._default_price_result(dict(_DAILY_DECISION), "5d") is None


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
