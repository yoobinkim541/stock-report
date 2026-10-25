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
            "reviews": [{"symbol": "AAPL", "order_side": "buy", "order_action": "block",
                         "reason": "r", "confidence": 50}]
        }, {("MSFT", "buy")})


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
    assert reviews[("MSFT", "buy")]["order_action"] == "block"
    assert reviews[("MSFT", "buy")]["confidence"] == 74
    assert "MSFT" in calls[0][0][3]


def test_apply_reviews_shadow_and_guarded_apply():
    plan = [
        {"symbol": "MSFT", "side": "buy", "qty": 5, "reason": "신규/추가"},
        {"symbol": "AAPL", "side": "sell", "qty": 4, "reason": "타깃이탈"},
    ]
    reviews = {
        ("MSFT", "buy"): {"order_action": "reduce_half", "reason": "추격매수 방지"},
        ("AAPL", "sell"): {"order_action": "block", "reason": "매도 보류"},
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
    reviews = {("MSFT", "buy"): {"order_action": "block", "reason": "추가매수 보류", "confidence": 80}}
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
    reviews = {("005930", "buy"): {"order_action": "block", "reason": "추가매수 보류", "confidence": 80}}
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


# ── A-08 회귀: review 는 (symbol, side) 로만 주문에 연결 ─────────────────────────

def _review(symbol, side, action, reason="근거", confidence=60):
    return {"symbol": symbol, "order_side": side, "order_action": action,
            "reason": reason, "confidence": confidence}


def _runner_json(obj):
    import json

    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(obj, ensure_ascii=False), stderr="")
    return runner


def _review_and_apply(monkeypatch, plan, runner, apply_mode="guarded_apply"):
    monkeypatch.setenv("MOCK_ORDER_LLM_ENABLED", "1")
    payload = M.build_order_review_payload(market="US", orders=plan, signals=[])
    reviews, status = M.run_order_review(payload, runner=runner)
    new_plan, applied = M.apply_reviews(plan, reviews, apply_mode=apply_mode, review_status=status)
    return reviews, status, new_plan, applied


def test_a_fix_exec_sell_review_cannot_block_buy_order(monkeypatch):
    plan = [{"symbol": "AAPL", "side": "buy", "qty": 10, "price": 100}]
    reviews, status, new_plan, applied = _review_and_apply(
        monkeypatch, plan, _runner_json({"reviews": [_review("AAPL", "sell", "block")]}))

    assert reviews == {}
    assert status.startswith("guard rejected:") and "order_side mismatch" in status
    assert not any(a["llm_applied"] == "block" for a in applied)


def test_a_fix_exec_same_symbol_two_sides_do_not_overwrite(monkeypatch):
    plan = [
        {"symbol": "AAPL", "side": "sell", "qty": 4, "reason": "타깃이탈"},
        {"symbol": "AAPL", "side": "buy", "qty": 10, "reason": "재편입"},
    ]
    reviews, status, new_plan, applied = _review_and_apply(monkeypatch, plan, _runner_json({"reviews": [
        _review("AAPL", "buy", "allow"),
        _review("AAPL", "sell", "reduce_half"),
    ]}))

    assert status == "ok"
    assert reviews[("AAPL", "buy")]["order_action"] == "allow"
    assert reviews[("AAPL", "sell")]["order_action"] == "reduce_half"
    assert new_plan == plan          # 매수는 allow, 매도 review 는 매수에 적용되지 않음
    assert applied == []


@pytest.mark.parametrize("items, needle", [
    ([_review("AAPL", "buy", "block"), _review("AAPL", "buy", "allow")], "duplicate review"),
    ([{k: v for k, v in _review("AAPL", "buy", "block").items() if k != "reason"}], "missing reason"),
    ([{k: v for k, v in _review("AAPL", "buy", "block").items() if k != "confidence"}], "invalid confidence"),
    ([_review("AAPL", "buy", "block", confidence="70")], "invalid confidence"),
    ([_review("AAPL", "buy", "block", confidence=170)], "invalid confidence"),
    (["AAPL buy block"], "review item must be object"),
])
def test_a_fix_exec_invalid_reviews_rejected_before_apply(monkeypatch, items, needle):
    plan = [{"symbol": "AAPL", "side": "buy", "qty": 10}]
    reviews, status, new_plan, applied = _review_and_apply(monkeypatch, plan, _runner_json({"reviews": items}))

    assert reviews == {}
    assert status.startswith("guard rejected:") and needle in status
    assert not any(a["llm_applied"] in ("block", "reduce_half") for a in applied)


def test_a_fix_exec_reduce_half_never_exceeds_half(monkeypatch):
    plan = [{"symbol": "AAPL", "side": "buy", "qty": q} for q in (1, 7)]
    for order in plan:
        _, status, new_plan, applied = _review_and_apply(
            monkeypatch, [order], _runner_json({"reviews": [_review("AAPL", "buy", "reduce_half")]}))
        assert status == "ok"
        assert sum(o["qty"] for o in new_plan) <= order["qty"] // 2
        assert applied[0]["llm_applied"] == "reduce_half"


# ── A-11 회귀: guarded_apply 실패 → 미검증 매수 보류·매도 진행, shadow 는 원계획 ───

def _raise_timeout(cmd, **kwargs):
    import subprocess
    raise subprocess.TimeoutExpired(cmd, 75)


def _exit_1(cmd, **kwargs):
    return SimpleNamespace(returncode=1, stdout="", stderr="fake unavailable")


def _not_json(cmd, **kwargs):
    return SimpleNamespace(returncode=0, stdout="검토 결과 없음", stderr="")


_FAILURE_RUNNERS = {
    "timeout": (_raise_timeout, "call failed"),
    "nonzero_exit": (_exit_1, "call failed"),
    "json_error": (_not_json, "guard rejected"),
    "unknown_ticker": (_runner_json({"reviews": [_review("HALLUCINATED", "buy", "allow")]}), "guard rejected"),
    "empty_reviews": (_runner_json({"reviews": []}), "partial: 0/2"),
    "partial_reviews": (_runner_json({"reviews": [_review("TSLA", "sell", "allow")]}), "partial: 1/2"),
}
_PLAN = [
    {"symbol": "TSLA", "side": "sell", "qty": 4, "reason": "타깃이탈"},
    {"symbol": "AAPL", "side": "buy", "qty": 10, "price": 100, "reason": "신규"},
]


@pytest.mark.parametrize("case", sorted(_FAILURE_RUNNERS))
def test_a_fix_exec_guarded_apply_holds_unreviewed_buys_on_failure(monkeypatch, case):
    runner, status_prefix = _FAILURE_RUNNERS[case]
    _, status, new_plan, applied = _review_and_apply(monkeypatch, list(_PLAN), runner)

    assert status.startswith(status_prefix)
    assert [(o["symbol"], o["side"]) for o in new_plan] == [("TSLA", "sell")]  # 매도는 진행, 매수 보류
    held = [a for a in applied if a["llm_applied"] == "hold_unreviewed"]
    assert [(a["symbol"], a["qty"], a["llm_status"]) for a in held] == [("AAPL", 10, status)]

    record = M.review_execution_record(apply_mode="guarded_apply", review_status=status, applied=applied)
    assert record["kind"] == "llm_order_review" and record["status"] == status
    assert record["fallback"] is True
    assert record["held_buys"] == [{"symbol": "AAPL", "side": "buy", "qty": 10}]
    assert "side" not in record and "code" not in record and "ticker" not in record
    note = M.fallback_note(record)
    assert "미검증 매수 1건 보류" in note


@pytest.mark.parametrize("case", sorted(_FAILURE_RUNNERS))
def test_a_fix_exec_shadow_keeps_plan_and_records_failure(monkeypatch, case):
    runner, status_prefix = _FAILURE_RUNNERS[case]
    _, status, new_plan, applied = _review_and_apply(monkeypatch, list(_PLAN), runner, apply_mode="shadow")

    assert new_plan == _PLAN and applied == []
    record = M.review_execution_record(apply_mode="shadow", review_status=status, applied=applied)
    assert record["status"].startswith(status_prefix)
    assert record["fallback"] is False
    assert M.fallback_note(record) is None


def test_a_fix_exec_guarded_apply_full_review_has_no_fallback(monkeypatch):
    _, status, new_plan, applied = _review_and_apply(monkeypatch, list(_PLAN), _runner_json({"reviews": [
        _review("TSLA", "sell", "allow"), _review("AAPL", "buy", "allow")]}))

    assert status == "ok"
    assert new_plan == _PLAN and applied == []
    record = M.review_execution_record(apply_mode="guarded_apply", review_status=status, applied=applied)
    assert record["fallback"] is False


def test_a_fix_exec_disabled_review_keeps_plan_in_guarded_apply(monkeypatch):
    monkeypatch.setenv("MOCK_ORDER_LLM_ENABLED", "0")
    payload = M.build_order_review_payload(market="US", orders=_PLAN, signals=[])
    reviews, status = M.run_order_review(payload)
    new_plan, applied = M.apply_reviews(list(_PLAN), reviews, apply_mode="guarded_apply",
                                        review_status=status)

    assert status == "disabled"
    assert new_plan == _PLAN and applied == []
