from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_strategy_lab_renderer_surfaces_summary_preview_and_versions():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

pack = {{"strategy_studio": {{"ok": True, "spec_count": 1, "version_count": 2, "latest": {{"name": "EMA trend"}}}}}}
catalog = {{
    "ok": True,
    "count": 1,
    "latest": {{"id": "spec-1", "name": "EMA trend", "version": 2}},
    "specs": [{{"id": "spec-1", "name": "EMA trend", "version": 2, "spec": {{"name": "EMA trend"}}}}],
    "version_total": 2,
}}
selected_spec = {{
    "id": "spec-1",
    "name": "EMA trend",
    "version": 2,
    "spec": {{
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "indicators": [{{"kind": "ema", "period": 20}}],
    }},
}}
preview = {{
    "ok": True,
    "report": {{
        "summary": {{"name": "EMA trend", "trade_count": 4, "cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35}},
        "metrics": {{"cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35, "trade_count": 4}},
        "warnings": [],
        "trades": [{{"date": "2026-01-02", "action": "enter_long"}}],
        "equity": {{"columns": ["nav"], "index": ["2026-01-01"], "rows": [{{"nav": 100.0}}]}},
        "weights": {{"columns": ["QQQ"], "index": ["2026-01-01"], "rows": [{{"QQQ": 1.0}}]}},
    }},
    "metrics": {{"cagr": 0.12, "max_drawdown": -0.08, "sharpe": 1.35, "trade_count": 4}},
    "benchmark": {{"symbol": "QQQ", "available": True}},
    "warnings": ["stale quotes"],
    "errors": [],
    "trade_count": 4,
}}
versions = [
    {{"id": "spec-1", "version": 2, "name": "EMA trend", "source": "ui", "created_at": "2026-08-01T00:00:00+00:00"}},
    {{"id": "spec-1", "version": 1, "name": "EMA trend", "source": "create", "created_at": "2026-07-31T00:00:00+00:00"}},
]
patch_preview = {{
    "ok": True,
    "patch": {{"rules": {{"exit": [{{"field": "atr14", "op": ">", "value": 0.0}}]}}}},
    "diff": [{{"path": "rules.exit[0].field", "before": None, "after": "atr14"}}],
    "preview": preview,
}}
strategy_studio.render_strategy_lab("lab", pack, mode="research", catalog=catalog, selected_spec=selected_spec, preview=preview, versions=versions, patch_preview=patch_preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "전략 스튜디오" in body
    assert "EMA trend" in body
    assert "버전" in body
    assert "패치" in body
    assert "미리보기" in body
    assert any(button.label == "차트 리플레이로 보내기" for button in at.button)


def test_strategy_lab_period_options_are_valid_yfinance_periods():
    """기간 드롭다운 값이 yfinance Ticker.history(period=...) 가 받는 값이어야 함.

    yfinance 는 'Nd' 형태(임의 일수, 예: 60d/30d)는 허용하지만 'Nm'(1m/3m/6m) 은
    단위 오기재로 거부한다 ('1 month' 는 '1mo' 여야 함) — 조용히 빈 값을 반환해
    price panel is empty 로 이어진다.
    """
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

pack = {{"strategy_studio": {{"ok": True, "spec_count": 0, "version_count": 0, "latest": None}}}}
catalog = {{"ok": True, "count": 0, "latest": None, "specs": [], "version_total": 0}}
strategy_studio.render_strategy_lab("lab", pack, mode="research", catalog=catalog)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    period_select = at.selectbox(key="strategy_studio::research::lab::period")
    assert list(period_select.options) == ["1mo", "3mo", "6mo", "1y", "2y", "5y", "60d", "30d"]


def test_strategy_lab_preview_button_does_not_raise_session_state_error(monkeypatch):
    from dashboard import cached

    monkeypatch.setattr(
        cached,
        "strategy_studio_preview",
        lambda *a, **k: {
            "ok": True,
            "report": {"summary": {"name": "EMA trend", "trade_count": 1}},
            "metrics": {"trade_count": 1},
            "trade_count": 1,
        },
    )

    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

pack = {{"strategy_studio": {{"ok": True, "spec_count": 1, "version_count": 1, "latest": {{"name": "EMA trend"}}}}}}
catalog = {{
    "ok": True,
    "count": 1,
    "latest": {{"id": "spec-1", "name": "EMA trend", "version": 1}},
    "specs": [{{"id": "spec-1", "name": "EMA trend", "version": 1, "spec": {{"name": "EMA trend"}}}}],
    "version_total": 1,
}}
selected_spec = {{
    "id": "spec-1",
    "name": "EMA trend",
    "version": 1,
    "spec": {{
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "indicators": [{{"kind": "ema", "period": 20}}],
    }},
}}
strategy_studio.render_strategy_lab("ai_console", pack, mode="lab", catalog=catalog, selected_spec=selected_spec)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)

    preview_button = next(b for b in at.button if "run_preview" in b.key)
    preview_button.click().run()

    assert not at.exception, str(at.exception)


def test_patch_panel_shows_real_error_when_propose_patch_raises():
    """감사 #32 — propose_strategy_patch() 가 예외를 던지면 patch_result["preview"]
    는 빈 dict {} 로 만들어지고, 렌더러는 preview 안쪽만 보고 항상 일반 메시지
    "패치 미리보기 실패" 로 대체해 실제 원인(current_patch["error"])이 사라졌음."""
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

patch_preview = {{
    "ok": False,
    "error": "지표 파라미터 'period' 는 1 이상이어야 합니다",
    "patch": {{}},
    "diff": [],
    "preview": {{}},
}}
strategy_studio._render_patch_panel("lab", None, patch_preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "지표 파라미터 'period' 는 1 이상이어야 합니다" in warnings
    assert "패치 미리보기 실패" not in warnings


def test_strategy_studio_dashboard_wrappers_forward_data(monkeypatch):
    from dashboard import cached, views

    monkeypatch.setattr(views.strategy_studio, "strategy_lab_state", lambda: {"ok": True, "catalog": {"count": 1}, "presets": {"rsi_cash": {}}})
    monkeypatch.setattr(views.strategy_studio, "preview_strategy_spec", lambda *args, **kwargs: {"ok": True, "report": {"summary": {"name": "EMA trend"}}})
    monkeypatch.setattr(views.strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {"id": "spec-1", "name": "EMA trend"})
    monkeypatch.setattr(views.strategy_studio, "list_strategy_specs", lambda limit=50: [{"id": "spec-1", "name": "EMA trend"}])

    cached.strategy_studio_catalog.clear()
    cached.strategy_studio_preview.clear()

    catalog = views.strategy_studio_catalog()
    preview = views.strategy_studio_preview({"name": "EMA trend"})
    cached_catalog = cached.strategy_studio_catalog()
    cached_preview = cached.strategy_studio_preview({"name": "EMA trend"})

    assert catalog["catalog"]["count"] == 1
    assert preview["ok"] is True
    assert cached_catalog["catalog"]["count"] == 1
    assert cached_preview["ok"] is True


def test_strategy_lab_renders_profile_validation_and_gate_diagnostics():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

selected_spec = {{
    "id": "spec-modern",
    "name": "Momentum rank",
    "version": 1,
    "spec": {{
        "name": "Momentum rank",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "data_profile": "global_swing",
        "execution_profile": "global_swing",
        "signal": {{"type": "factor", "plugin": "momentum"}},
        "portfolio": {{"optimizer": "cost_aware_risk_budget", "max_turnover": 0.3}},
        "execution": {{"profile": "global_swing", "partial_fill": True}},
        "validation": {{"mode": "purged_walk_forward"}},
        "promotion": {{"environment": "sandbox"}},
    }},
}}
preview = {{
    "ok": False,
    "profile": "global_swing",
    "execution_profile": "global_swing",
    "validation_mode": "purged_walk_forward",
    "validation": {{"promotion_eligible": False, "aggregate": {{"fold_count": 2, "turnover": 0.42}}}},
    "promotion": {{"accepted": False, "activation_safe": False, "preview": False, "failed_checks": ["net_excess"]}},
    "data_quality": {{"status": "stale", "ok": False, "warnings": ["가격 스냅샷이 오래되었습니다"]}},
    "provenance": {{"ok": False, "data": {{"source": "yahoo", "version": "v1"}}}},
    "diagnostics": [{{"type": "turnover_limit", "message": "turnover limit exceeded"}}],
    "report": {{"summary": {{"name": "Momentum rank", "trade_count": 3, "cagr": 0.1, "max_drawdown": -0.08, "sharpe": 1.1, "turnover": 0.42}}}},
}}
strategy_studio.render_strategy_lab("diagnostics", {{"strategy_studio": {{"ok": True}}}}, mode="research", catalog={{"specs": []}}, selected_spec=selected_spec, preview=preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in list(at.markdown) + list(at.caption))
    assert "실행 프로필" in body
    assert "승격" in body
    assert "데이터 품질" in body
    assert "진단" in body
    assert any("net_excess" in str(frame.value) for frame in at.dataframe)
    assert any(select.key.endswith("data_profile") for select in at.selectbox)
    assert any(select.key.endswith("execution_profile") for select in at.selectbox)
    assert any(select.key.endswith("validation_mode") for select in at.selectbox)


def test_strategy_lab_disables_live_activation_without_strict_capability_gate():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

selected_spec = {{"id": "spec-blocked", "name": "RSI", "version": 1, "spec": {{
    "name": "RSI", "base_symbol": "QQQ", "validation": {{"mode": "single_pass"}},
}}}}
preview = {{
    "ok": True,
    "validation_mode": "single_pass",
    "validation": {{"promotion_eligible": False, "folds": []}},
    "promotion": {{"accepted": False, "activation_safe": False, "preview": True, "failed_checks": ["preview_only"]}},
    "data_quality": {{"status": "unknown", "ok": False}},
    "provenance": {{"ok": False}},
    "report": {{"summary": {{"name": "RSI", "trade_count": 1}}}},
}}
strategy_studio.render_strategy_lab("blocked", {{"strategy_studio": {{"ok": True}}}}, mode="research", catalog={{"specs": []}}, selected_spec=selected_spec, preview=preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    live_buttons = [button for button in at.button if button.key.endswith("activate_live")]
    assert live_buttons
    assert live_buttons[0].disabled is True
    body = " ".join(str(item.value) for item in list(at.markdown) + list(at.caption))
    assert "실거래 활성화 가능" not in body


def test_non_rsi_presets_include_profiles_costs_validation_and_universe_warning():
    from ml.strategy_studio.presets import builtin_strategy_presets

    presets = builtin_strategy_presets()
    expected = {
        "momentum_rank",
        "mean_reversion",
        "breakout_with_trailing_stop",
        "factor_ensemble",
        "kr_intraday_vwap",
    }
    assert expected.issubset(presets)
    for key in expected:
        preset = presets[key]
        assert preset["data_profile"]
        assert preset["execution_profile"]
        assert preset["validation"]["mode"]
        assert preset["validation"]["benchmarks"]
        assert preset["costs"]
        assert preset["metadata"]["universe_warning"]


def test_strategy_lab_renders_common_result_evidence_and_explicit_states():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

selected_spec = {{"id": "spec-evidence", "name": "Evidence strategy", "version": 2, "spec": {{
    "name": "Evidence strategy", "market": "us", "timeframe": "1d", "base_symbol": "QQQ",
    "data_profile": "global_swing", "execution_profile": "global_swing",
    "signal": {{"type": "factor", "plugin": "momentum"}},
    "portfolio": {{"optimizer": "cost_aware_risk_budget", "max_turnover": 0.3}},
    "execution": {{"profile": "global_swing"}},
    "validation": {{"mode": "purged_walk_forward"}},
    "promotion": {{"environment": "sandbox"}},
}}}}
preview = {{
    "ok": True, "profile": "global_swing", "execution_profile": "global_swing",
    "validation_mode": "purged_walk_forward",
    "report": {{
        "summary": {{"name": "Evidence strategy", "trade_count": 12, "cagr": 0.16,
                       "max_drawdown": -0.09, "sharpe": 1.4, "turnover": 0.22,
                       "cost_drag": 0.013}},
        "trades": [{{"date": "2026-01-02", "action": "enter_long", "status": "partial"}}],
        "equity": {{"columns": ["nav"], "index": ["2026-01-01"], "rows": [{{"nav": 100.0}}]}},
        "weights": {{"columns": ["QQQ"], "index": ["2026-01-01"], "rows": [{{"QQQ": 1.0}}]}},
    }},
    "validation": {{"promotion_eligible": False, "aggregate": {{"fold_count": 2, "turnover": 0.22}}}},
    "promotion": {{"accepted": False, "activation_safe": False, "preview": False, "failed_checks": ["net_excess"]}},
    "data_quality": {{"status": "stale", "ok": False, "warnings": ["가격 스냅샷이 오래되었습니다"]}},
    "provenance": {{"ok": False, "checks": [{{"fold": "fold-1", "ok": False}}]}},
    "folds": [{{"fold_id": "fold-1", "test_start": "2026-01-01", "test_end": "2026-02-01"}}],
    "diagnostics": [{{"type": "turnover_limit", "message": "turnover limit exceeded"}}],
}}
strategy_studio.render_strategy_lab("evidence", {{"strategy_studio": {{"ok": True}}}}, mode="research",
    catalog={{"specs": []}}, selected_spec=selected_spec, preview=preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in list(at.markdown) + list(at.caption) + list(at.info) + list(at.warning))
    for label in ("드래프트", "sandbox", "실거래", "검증 폴드", "데이터 품질", "원천", "비용", "회전율", "진단"):
        assert label in body


def test_strategy_lab_keeps_live_disabled_when_cpcv_chronology_proof_is_incomplete():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import strategy_studio

selected_spec = {{"id": "spec-cpcv", "name": "CPCV strategy", "version": 1, "spec": {{
    "name": "CPCV strategy", "market": "us", "base_symbol": "QQQ",
    "validation": {{"mode": "cpcv"}}, "promotion": {{"environment": "sandbox"}},
}}}}
preview = {{
    "ok": True, "validation_mode": "cpcv",
    "validation": {{"promotion_eligible": True, "aggregate": {{
        "fold_count": 1, "cpcv_chronology_ok": True,
        "cpcv_chronology_evidence": [{{"fold_id": "fold-1", "valid": True}}],
    }}, "folds": [{{"fold_id": "fold-1"}}]}},
    "promotion": {{"accepted": True, "activation_safe": True, "preview": False}},
    "data_quality": {{"status": "fresh", "ok": True}},
    "provenance": {{"ok": True}}, "report": {{"summary": {{"trade_count": 4}}}},
}}
strategy_studio.render_strategy_lab("cpcv", {{"strategy_studio": {{"ok": True}}}}, mode="research",
    catalog={{"specs": []}}, selected_spec=selected_spec, preview=preview)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    live_buttons = [button for button in at.button if button.key.endswith("activate_live")]
    assert live_buttons and live_buttons[0].disabled is True
    body = " ".join(str(item.value) for item in list(at.markdown) + list(at.caption) + list(at.warning))
    assert "chronology" in body or "시간 순서" in body
    assert "실거래 활성화 가능" not in body


def test_strategy_lab_run_button_uses_public_run_contract_without_network():
    from agent_console import strategy_studio as backend

    original_backend_run = backend.run_strategy_spec
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

original_run = strategy_studio.views.strategy_studio.run_strategy_spec

def fake_run(spec, *, period=None, validation_mode=None):
    st.session_state["test_run_mode"] = validation_mode
    strategy_studio.views.strategy_studio.run_strategy_spec = original_run
    return {{
        "ok": True,
        "run_id": "run-test",
        "validation_mode": validation_mode,
        "report": {{"summary": {{"name": "RSI", "trade_count": 7}}}},
        "metrics": {{"trade_count": 7}},
        "validation": {{}},
        "promotion": {{"accepted": False, "activation_safe": False, "preview": True}},
        "data_quality": {{"status": "fresh", "ok": True}},
        "provenance": {{"ok": True}},
        "folds": [],
        "diagnostics": [],
    }}

strategy_studio.views.strategy_studio.run_strategy_spec = fake_run
selected_spec = {{"id": "spec-run", "name": "RSI", "version": 1, "spec": {{
    "name": "RSI", "market": "us", "timeframe": "1d", "base_symbol": "QQQ",
    "validation": {{"mode": "single_pass"}},
}}}}
strategy_studio.render_strategy_lab("run", {{"strategy_studio": {{"ok": True}}}}, mode="research",
    catalog={{"specs": []}}, selected_spec=selected_spec)
"""
    try:
        at = AppTest.from_string(script, default_timeout=30)
        at.run()
        assert not at.exception, str(at.exception)

        run_button = next(button for button in at.button if button.key.endswith("run_strategy"))
        run_button.click().run()

        assert not at.exception, str(at.exception)
        assert at.session_state["test_run_mode"] == "single_pass"
        assert any(metric.value == "7" for metric in at.metric)
    finally:
        backend.run_strategy_spec = original_backend_run


def test_strategy_lab_prefers_current_stored_draft_over_catalog_record():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

prefix = strategy_studio._state_prefix("draft", "research")
catalog = {{
    "specs": [{{"id": "spec-1", "name": "Saved", "version": 1, "spec": {{
        "name": "Saved", "base_symbol": "QQQ"
    }}}}]
}}
selected = {{"id": "spec-1", "name": "Saved", "version": 1, "spec": {{
    "name": "Saved", "base_symbol": "QQQ"
}}}}
strategy_studio._set_draft_record(prefix, {{"id": "spec-1", "name": "Edited", "version": 2, "spec": {{
    "name": "Edited", "base_symbol": "QQQ", "version": 2
}}}})

record = strategy_studio._ensure_selected_record(prefix, catalog, selected)
assert record["spec"]["name"] == "Edited"
assert record["version"] == 2
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_strategy_editor_controls_keep_legacy_rule_specs_intact():
    from dashboard import strategy_studio

    legacy = {
        "name": "EMA legacy",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "indicators": [{"kind": "ema", "period": 20}],
        "rules": {"entry": [{"field": "close", "op": ">", "value": 1}]},
        "costs": {"fees_bps": 4, "slippage_bps": 6, "spread_bps": 2},
    }
    result = strategy_studio._apply_editor_controls(
        legacy,
        {
            "data_profile": "generic",
            "execution_profile": "bar",
            "validation_mode": "single_pass",
            "strategy_type": "rule",
            "provider": "rule",
            "portfolio_optimizer": "legacy_fixed",
            "cost_scenario": "default",
        },
    )
    assert result["indicators"] == legacy["indicators"]
    assert result["rules"] == legacy["rules"]
    assert "signal" not in result
    assert result["costs"] == legacy["costs"]


def test_strict_cpcv_gate_checks_all_chronology_aliases_and_nested_actual_evidence():
    from dashboard import strategy_studio

    fold = {
        "fold_id": "fold-1",
        "train_max": "2026-01-31T00:00:00+00:00",
        "test_min": "2026-02-01T00:00:00+00:00",
        "future_training": False,
        "no_future_training": True,
        "train_before_test": True,
        "train_max_before_test_min": True,
        "valid": True,
        "proof_valid": True,
        "chronology_evidence": {
            "fold_id": "fold-1",
            "train_max": "2026-01-31T00:00:00+00:00",
            "test_min": "2026-02-01T00:00:00+00:00",
            "future_training": False,
            "no_future_training": True,
            "train_before_test": True,
            "train_max_before_test_min": True,
            "valid": True,
            "proof_valid": True,
        },
    }
    result = {
        "ok": True,
        "validation_mode": "cpcv",
        "validation": {
            "validation_mode": "cpcv",
            "promotion_eligible": True,
            "aggregate": {
                "fold_count": 1,
                "cpcv_fold_count": 1,
                "provenance_ok": True,
                "cpcv_fold_ids": ["fold-1"],
                "cpcv_chronology_ok": True,
                "cpcv_chronology_evidence": [fold["chronology_evidence"]],
            },
            "folds": [fold],
        },
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
        "data_quality": {"status": "fresh", "ok": True},
        "provenance": {"ok": True, "checks": [{"fold": "fold-1", "ok": True, "provenance_ok": True}]},
        "errors": [],
    }
    assert strategy_studio._strict_validation_ready(result) is True

    fold["chronology_evidence"]["valid"] = False
    assert strategy_studio._strict_validation_ready(result) is False


def test_new_full_validation_presets_declare_label_horizon():
    from ml.strategy_studio.presets import builtin_strategy_presets

    expected = {
        "momentum_rank",
        "mean_reversion",
        "breakout_with_trailing_stop",
        "factor_ensemble",
        "kr_intraday_vwap",
    }
    presets = builtin_strategy_presets()

    for key in expected:
        horizon = presets[key]["validation"].get("label_horizon")
        assert isinstance(horizon, int)
        assert horizon > 0


def test_strategy_benchmark_is_preserved_and_forwarded_to_run(monkeypatch):
    from agent_console import strategy_studio as backend
    from agent_console import strategy_studio
    from ml.strategy_studio import StrategySpec

    original_run = backend.run_strategy_spec

    spec = StrategySpec.from_dict({
        "name": "KOSPI benchmark",
        "market": "kr",
        "base_symbol": "005930.KS",
        "benchmark": "^KS11",
        "validation": {"mode": "single_pass"},
    })
    assert spec.to_dict()["benchmark"] == "^KS11"

    received = {}

    def fake_preview(*args, **kwargs):
        received["benchmark"] = kwargs["benchmark"]
        return {
            "ok": True,
            "benchmark": {"symbol": kwargs["benchmark"], "available": True},
            "report": {},
            "warnings": [],
            "errors": [],
        }

    try:
        monkeypatch.setattr(strategy_studio, "preview_strategy_spec", fake_preview)
        result = strategy_studio.run_strategy_spec(spec.to_dict(), validation_mode="single_pass")
    finally:
        backend.run_strategy_spec = original_run

    assert received["benchmark"] == "^KS11"
    assert result["benchmark"]["symbol"] == "^KS11"


def test_cached_preview_evidence_is_invalidated_when_context_changes():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

prefix = strategy_studio._state_prefix("binding", "research")
spec = {{"name": "Binding", "base_symbol": "QQQ"}}
controls = {{"validation_mode": "single_pass", "data_profile": "generic"}}
strategy_studio._set_preview(
    prefix,
    {{"ok": True}},
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=1,
)
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=1,
) is not None
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload={{**spec, "name": "Changed"}},
    controls=controls,
    benchmark="QQQ",
    version=1,
) is None

strategy_studio._set_preview(
    prefix,
    {{"ok": True}},
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=1,
)
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls={{**controls, "validation_mode": "cpcv"}},
    benchmark="QQQ",
    version=1,
) is None

strategy_studio._set_preview(
    prefix,
    {{"ok": True}},
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=1,
)
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls=controls,
    benchmark="^KS11",
    version=1,
) is None

strategy_studio._set_preview(
    prefix,
    {{"ok": True}},
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=1,
)
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    version=2,
) is None
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_cached_preview_evidence_includes_selected_period():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

prefix = strategy_studio._state_prefix("period", "research")
spec = {{"name": "Period", "base_symbol": "QQQ"}}
controls = {{"validation_mode": "single_pass", "data_profile": "generic"}}
strategy_studio._set_preview(
    prefix,
    {{"ok": True}},
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    period="1y",
    version=1,
)
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    period="1y",
    version=1,
) is not None
assert strategy_studio._ensure_preview(
    prefix,
    None,
    spec_payload=spec,
    controls=controls,
    benchmark="QQQ",
    period="2y",
    version=1,
) is None
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_benchmark_state_is_replaced_when_switching_strategy():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

prefix = strategy_studio._state_prefix("benchmark", "research")
benchmark_key = strategy_studio._state_key(prefix, "benchmark")
st.session_state[benchmark_key] = "^KS11"
strategy_studio._set_draft_record(prefix, {{
    "id": "us-1", "name": "US", "version": 1,
    "spec": {{"name": "US", "base_symbol": "QQQ"}},
}})
assert st.session_state[benchmark_key] == "QQQ"

strategy_studio._set_draft_record(prefix, {{
    "id": "kr-1", "name": "KR", "version": 1,
    "spec": {{"name": "KR", "market": "kr", "base_symbol": "005930.KS", "benchmark": "^KS11"}},
}})
assert st.session_state[benchmark_key] == "^KS11"
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_strict_cpcv_gate_requires_nested_chronology_proof():
    from dashboard import strategy_studio

    fold = {
        "fold_id": "fold-1",
        "train_max": "2026-01-31T00:00:00+00:00",
        "test_min": "2026-02-01T00:00:00+00:00",
        "future_training": False,
        "strictly_chronological": True,
        "train_before_test": True,
        "valid": True,
    }
    result = {
        "ok": True,
        "validation_mode": "cpcv",
        "validation": {
            "validation_mode": "cpcv",
            "promotion_eligible": True,
            "aggregate": {
                "fold_count": 1,
                "provenance_ok": True,
                "cpcv_fold_ids": ["fold-1"],
                "cpcv_chronology_ok": True,
                "cpcv_chronology_evidence": [fold],
            },
            "folds": [fold],
        },
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
        "data_quality": {"status": "fresh", "ok": True},
        "provenance": {"ok": True, "checks": [{"fold": "fold-1", "ok": True, "provenance_ok": True}]},
        "errors": [],
    }

    assert strategy_studio._strict_validation_ready(result) is False


def test_strict_validation_gate_requires_provenance_checks_for_actual_fold_ids():
    from dashboard import strategy_studio

    result = {
        "ok": True,
        "validation_mode": "purged_walk_forward",
        "validation": {
            "validation_mode": "purged_walk_forward",
            "promotion_eligible": True,
            "aggregate": {"fold_count": 1},
            "folds": [{
                "path_id": "fold-1",
                "train_max": "2026-01-31T00:00:00+00:00",
                "test_min": "2026-02-01T00:00:00+00:00",
                "future_training": False,
                "no_future_training": True,
                "train_before_test": True,
                "train_max_before_test_min": True,
                "valid": True,
                "proof_valid": True,
            }],
        },
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
        "data_quality": {"status": "fresh", "ok": True},
        "provenance": {"ok": True, "checks": [{"fold": "different-fold", "ok": True, "provenance_ok": True}]},
        "errors": [],
    }

    assert strategy_studio._strict_validation_ready(result) is False


def test_strategy_lab_validation_preserves_requested_mode():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import strategy_studio

def fake_run(spec, *, period=None, validation_mode=None):
    st.session_state["requested_mode"] = validation_mode
    return {{
        "ok": False,
        "validation_mode": validation_mode,
        "validation": {{"promotion_eligible": False}},
        "promotion": {{"accepted": False, "activation_safe": False, "preview": validation_mode == "single_pass"}},
        "data_quality": {{"status": "unknown", "ok": False}},
        "provenance": {{"ok": False}},
        "report": {{"summary": {{"name": "Mode"}}}},
        "folds": [],
        "diagnostics": [],
    }}

strategy_studio.views.strategy_studio.run_strategy_spec = fake_run
strategy_studio.render_strategy_lab(
    "mode-preserve",
    {{"strategy_studio": {{"ok": True}}}},
    mode="research",
    catalog={{"specs": []}},
    selected_spec={{"name": "Mode", "spec": {{"name": "Mode", "base_symbol": "QQQ", "validation": {{"mode": "single_pass"}}}}}},
)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    validate_button = next(button for button in at.button if button.key.endswith("validate_spec"))
    validate_button.click().run()

    assert not at.exception, str(at.exception)
    assert at.session_state["requested_mode"] == "single_pass"


def test_factor_ensemble_equal_weight_fallback_excludes_unavailable_member(monkeypatch):
    import pandas as pd

    from ml.strategy_studio import StrategySpec
    from ml.strategy_studio import signals

    index = pd.date_range("2026-01-01", periods=1, freq="D")

    def panel_for(strategy, compiled):
        member_type = strategy.signal.get("type")
        if member_type == "model":
            return signals.SignalPanel.invalid("model", "model unavailable")
        value = 1.0 if strategy.signal.get("plugin") == "momentum" else 3.0
        frame = pd.DataFrame({"QQQ": [value]}, index=index)
        return signals.SignalPanel.from_score(member_type, frame, 1.0)

    monkeypatch.setattr(signals, "build_signal_panel", panel_for)
    strategy = StrategySpec.from_dict({
        "name": "Factor fallback",
        "market": "multi",
        "base_symbol": "QQQ",
        "signal": {
            "type": "ensemble",
            "aggregation": "equal_weight",
            "members": [
                {"type": "factor", "plugin": "momentum"},
                {"type": "factor", "plugin": "volatility"},
                {"type": "model", "ref": "quality_factor_v1", "fallback": "equal_weight"},
            ],
        },
    })

    panel = signals._ensemble_provider(strategy, object())

    assert panel.score.loc[index[0], "QQQ"] == 2.0
    assert any("equal_weight" in diagnostic for diagnostic in panel.diagnostics)
