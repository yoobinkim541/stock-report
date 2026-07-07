#!/usr/bin/env python3
"""test_mock_llm_execution.py — 주문 LLM shadow/guard 계측."""
from types import SimpleNamespace

import pytest

from lib import mock_llm_execution as M
from ml.adaptive.ledger import Ledger


def test_run_order_review_disabled(monkeypatch):
    monkeypatch.setenv("MOCK_ORDER_LLM_ENABLED", "0")
    payload = M.build_order_review_payload(
        market="US", orders=[{"symbol": "MSFT", "side": "buy", "qty": 1}], signals=[])
    reviews, status = M.run_order_review(payload)
    assert reviews == {}
    assert status == "disabled"


def test_validate_reviews_rejects_unknown_symbol():
    with pytest.raises(ValueError, match="unknown symbol"):
        M.validate_reviews({
            "reviews": [{"symbol": "AAPL", "order_side": "buy", "order_action": "block"}]
        }, {"MSFT"})


def test_run_order_review_accepts_schema_json(monkeypatch):
    monkeypatch.setenv("MOCK_ORDER_LLM_ENABLED", "1")
    payload = M.build_order_review_payload(
        market="US",
        orders=[{"symbol": "MSFT", "side": "buy", "qty": 2}],
        signals=[{"ticker": "MSFT", "price": 420, "policy_score": 0.8}],
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=(
            '{"reviews":[{"symbol":"MSFT","order_side":"buy","order_action":"block",'
            '"reason":"급등 후 추가매수 보류","confidence":74}]}'
        ), stderr="")

    reviews, status = M.run_order_review(payload, runner=fake_runner)
    assert status == "ok"
    assert reviews["MSFT"]["order_action"] == "block"
    assert reviews["MSFT"]["confidence"] == 74
    assert "MSFT" in calls[0][0][3]


def test_apply_reviews_shadow_and_guarded_apply():
    plan = [
        {"symbol": "MSFT", "side": "buy", "qty": 5, "reason": "신규/추가"},
        {"symbol": "AAPL", "side": "sell", "qty": 4, "reason": "타깃이탈"},
    ]
    reviews = {
        "MSFT": {"order_action": "reduce_half", "reason": "추격매수 방지"},
        "AAPL": {"order_action": "block", "reason": "매도 보류"},
    }

    shadow, applied = M.apply_reviews(plan, reviews, apply_mode="shadow")
    assert shadow == plan
    assert applied == []

    guarded, applied = M.apply_reviews(plan, reviews, apply_mode="guarded_apply")
    assert guarded[0]["symbol"] == "MSFT" and guarded[0]["qty"] == 2
    assert guarded[1]["symbol"] == "AAPL" and guarded[1]["side"] == "sell"
    assert [a["llm_applied"] for a in applied] == ["reduce_half"]


def test_shadow_log_backfill_and_summary(tmp_path):
    ledger = Ledger("us_mock_llm_shadow", base_dir=tmp_path)
    plan = [{"symbol": "MSFT", "side": "buy", "qty": 3, "reason": "신규/추가"}]
    reviews = {"MSFT": {"order_action": "block", "reason": "추가매수 보류", "confidence": 80}}
    signals = {"MSFT": {"ticker": "MSFT", "policy_score": 0.8, "features": {"ranker": 0.9}}}

    added = M.log_shadow_reviews(
        ledger, market="US", date="2026-05-01", plan=plan, reviews=reviews,
        signals_by=signals, applied_mode="shadow")
    assert added == 1
    assert ledger.read_decisions()[0]["llm_action"] == "block"

    def price_fn(ticker, date, horizon):
        assert ticker == "MSFT"
        return (0.01, 0.05, 0.04, 0.03)  # underperformed QQQ by -4%p

    matured = M.backfill_shadow_outcomes(ledger, market="US", horizons_=[5, 20], price_fn=price_fn)
    assert matured == 2
    rows = M.shadow_training_set(ledger)
    assert {r["horizon"] for r in rows} == {5, 20}
    row = [r for r in rows if r["horizon"] == 20][0]
    assert row["would_help"] is True
    assert row["llm_delta_excess"] > 0
    assert M.pending_shadow_count(ledger, horizons_=[5, 20]) == 0

    summary = M.summarize_shadow(rows, horizon=20)
    assert summary["n"] == 1
    assert summary["horizon"] == 20
    assert summary["hit_rate"] == 100.0
    assert summary["avg_delta"] > 0
    assert "20D 성숙" in M.summary_line(summary)


def test_shadow_single_horizon_backfill_keeps_legacy_call_shape(tmp_path):
    ledger = Ledger("kr_mock_llm_shadow", base_dir=tmp_path)
    plan = [{"code": "005930", "side": "buy", "qty": 3, "reason": "신규/추가"}]
    reviews = {"005930": {"order_action": "block", "reason": "추가매수 보류", "confidence": 80}}
    signals = {"005930": {"ticker": "005930.KS", "code": "005930", "policy_score": 0.8}}
    M.log_shadow_reviews(
        ledger, market="KR", date="2026-05-01", plan=plan, reviews=reviews,
        signals_by=signals, applied_mode="shadow")

    matured = M.backfill_shadow_outcomes(
        ledger, market="KR", horizon=20, price_fn=lambda t, d, h: (0.01, 0.05, 0.04, 0.03))
    assert matured == 1
    row = M.shadow_training_set(ledger)[0]
    assert row["would_help"] is True
    assert row["llm_delta_excess"] > 0
    assert row["decision_id"].endswith(":h20")
