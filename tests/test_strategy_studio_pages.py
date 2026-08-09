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
