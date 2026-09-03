from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_document, chart_workbench, chart_workbench_ui  # noqa: E402


def _hist(n=320, *, start=100.0, step=0.2):
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series([start + i * step for i in range(n)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000,
        },
        index=index,
    )


def test_analysis_snapshot_combines_all_analysis_sections():
    document = chart_document.default_chart_document("MSFT")
    document["source"] = {
        "name": "yfinance",
        "as_of": "2026-08-08T20:00:00Z",
        "freshness": "delayed",
        "quality": "indicative",
    }
    hist = _hist(step=0.4)

    def load_ohlc(symbol, timeframe):
        assert timeframe in {"5m", "1h", "1d", "1wk"}
        return _hist(step=0.1 if symbol == "QQQ" else 0.4)

    out = chart_workbench.build_analysis_snapshot(
        document,
        hist,
        ohlc_loader=load_ohlc,
        fundamental_loader=lambda symbol: {"symbol": symbol, "metrics": {"per": 31.2}},
        alert_loader=lambda symbol: [{"id": "a1", "symbol": symbol, "matched": False}],
        orderflow_loader=lambda symbol: {"ok": True, "symbol": symbol, "coverage": {"trade_events": 3}},
    )

    assert out["symbol"] == "MSFT"
    assert out["benchmark"] == "QQQ"
    assert out["relative_strength"]["ok"] is True
    assert out["seasonality"]["ok"] is True
    assert len(out["seasonality"]["months"]) == 12
    assert out["multi_timeframe"]["ok"] is True
    assert len(out["multi_timeframe"]["rows"]) == 4
    assert out["trend"]["count"] >= 1
    assert {"support", "resistance", "channel"} <= set(out["trend"]["by_kind"])
    assert out["fundamentals"]["metrics"]["per"] == 31.2
    assert out["alerts"][0]["id"] == "a1"
    assert out["orderflow"]["coverage"]["trade_events"] == 3
    assert out["data_quality"]["source"] == "yfinance"
    assert out["errors"] == {}


def test_analysis_snapshot_uses_kr_benchmark_and_survives_optional_failures():
    document = chart_document.default_chart_document("005930.KS")
    hist = _hist()

    def load_ohlc(symbol, timeframe):
        if symbol == "^KS11":
            return _hist(step=0.1)
        if timeframe == "1h":
            raise RuntimeError("intraday provider unavailable")
        return _hist()

    def fail(_symbol):
        raise RuntimeError("optional provider unavailable")

    out = chart_workbench.build_analysis_snapshot(
        document,
        hist,
        ohlc_loader=load_ohlc,
        fundamental_loader=fail,
        alert_loader=fail,
        orderflow_loader=fail,
    )

    assert out["benchmark"] == "^KS11"
    assert out["relative_strength"]["ok"] is True
    assert out["patterns"] == [] or isinstance(out["patterns"], list)
    rows = {row["timeframe"]: row for row in out["multi_timeframe"]["rows"]}
    assert rows["1h"]["ok"] is False
    assert rows["1d"]["ok"] is True
    assert out["fundamentals"] == {}
    assert out["alerts"] == []
    assert out["orderflow"] == {"ok": False, "reason": "provider_unavailable"}
    assert set(out["errors"]) == {"fundamentals", "alerts", "orderflow"}


def test_analysis_snapshot_prefers_explicit_benchmark_series():
    document = chart_document.default_chart_document("MSFT")
    document["series"].append(
        {
            "id": "peer",
            "kind": "peer",
            "symbol": "AAPL",
            "axis": "primary",
            "normalization": "visible_start",
            "visible": True,
        }
    )

    seen = []

    def load_ohlc(symbol, timeframe):
        seen.append((symbol, timeframe))
        return _hist()

    out = chart_workbench.build_analysis_snapshot(
        document,
        _hist(),
        ohlc_loader=load_ohlc,
        fundamental_loader=lambda _symbol: {},
        alert_loader=lambda _symbol: [],
        orderflow_loader=lambda _symbol: {"ok": False, "reason": "capture_empty"},
    )

    assert out["benchmark"] == "AAPL"
    assert ("AAPL", "1d") in seen


def test_workbench_chart_groups_cover_every_document_chart_type_once():
    flattened = [item for values in chart_workbench_ui.CHART_TYPE_GROUPS.values() for item in values]

    assert set(flattened) == set(chart_document.CHART_TYPES)
    assert len(flattened) == len(set(flattened))


def test_condition_draft_builds_valid_canonical_tree():
    condition = chart_workbench_ui.condition_from_draft(
        symbol="msft",
        timeframe="1h",
        field="close",
        operator="crossing_up",
        value=320.0,
        confirmation="bar_close",
        session="extended",
    )

    assert condition == {
        "op": "all",
        "children": [
            {
                "type": "price",
                "symbol": "MSFT",
                "timeframe": "1h",
                "field": "close",
                "operator": "crossing_up",
                "value": 320.0,
                "confirmation": "bar_close",
                "session": "extended",
            }
        ],
    }


def test_condition_draft_supports_non_price_operands():
    condition = chart_workbench_ui.condition_from_draft(
        symbol="AAPL",
        timeframe="1d",
        field="forward_pe",
        operator="less_than",
        value=25,
        confirmation="bar_close",
        session="regular",
        leaf_type="fundamental",
    )

    assert condition["children"][0]["type"] == "fundamental"
    assert condition["children"][0]["field"] == "forward_pe"


def test_analysis_rail_renders_orderflow_evidence_and_blocked_capabilities():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workbench_ui

chart_workbench_ui.render_analysis_rail({{
    "symbol": "005930.KS", "benchmark": "^KS11",
    "trend": {{}}, "patterns": [], "multi_timeframe": {{}}, "seasonality": {{}},
    "relative_strength": {{}}, "fundamentals": {{}}, "alerts": [], "errors": {{}},
    "data_quality": {{"source": "kis_ws", "freshness": "realtime", "as_of": "now"}},
    "orderflow": {{
        "ok": True,
        "coverage": {{"trade_events": 2, "book_events": 1, "max_depth": 2,
                     "storage_window": {{"returned_events": 3, "truncated": True,
                                         "scanned_bytes": 512, "file_bytes": 1024,
                                         "capture_complete": False,
                                         "capture_status": {{"dropped_events": 3,
                                                             "write_failures": 1}}}},
                     "capabilities": {{"footprint": False, "bid_ask_delta": False}}}},
        "book": {{"bids": [[71000, 80]], "asks": [[71100, 20]], "spread": 100,
                 "imbalance": 0.6, "age_seconds": 1}},
        "volume_profile": [{{"price": 71000, "volume": 25}}],
        "blocked": {{"footprint": "authoritative_aggressor_side_unavailable"}},
    }},
}})
"""
    at = AppTest.from_string(script, default_timeout=30).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in at.markdown)
    body += " " + " ".join(str(item.value) for item in at.caption)
    body += " " + " ".join(str(item.label) for item in at.metric)
    assert "오더플로" in body
    assert "풋프린트" in body
    assert "최근 3건 창" in body
    assert "일부만 표시" in body
    assert "캡처 3건 유실" in body
    assert "쓰기 재시도 1회" in body
    assert len(at.get("plotly_chart")) == 2
    assert any("kis_ws" in str(item.value) for item in at.json)


_VISION_SNAPSHOT = """{
    "symbol": "NVDA", "benchmark": "QQQ",
    "trend": {}, "patterns": [], "multi_timeframe": {}, "seasonality": {},
    "relative_strength": {}, "fundamentals": {}, "alerts": [], "errors": {},
    "data_quality": {"source": "yfinance", "freshness": "delayed", "as_of": "now"},
    "orderflow": {"ok": False, "reason": "capture_not_configured"},
}"""

_VISION_HIST_SNIPPET = """
import pandas as pd
_idx = pd.date_range("2025-01-01", periods=120, freq="B")
_close = [100.0 + i * 0.3 for i in range(120)]
hist = pd.DataFrame({
    "Open": _close, "High": [c + 1 for c in _close], "Low": [c - 1 for c in _close],
    "Close": [c + 0.1 for c in _close], "Volume": [1000 + i for i in range(120)],
}, index=_idx)
"""


def test_vision_section_shows_prompt_before_click():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
{_VISION_HIST_SNIPPET}
from dashboard import chart_workbench_ui
chart_workbench_ui.render_analysis_rail({_VISION_SNAPSHOT}, hist=hist)
"""
    at = AppTest.from_string(script, default_timeout=30).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in at.caption)
    assert "AI가 분석합니다" in body
    assert any("AI로 패턴 분석하기" in str(b.label) for b in at.button)


def test_vision_section_no_hist_shows_disabled_caption():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workbench_ui
chart_workbench_ui.render_analysis_rail({_VISION_SNAPSHOT})
"""
    at = AppTest.from_string(script, default_timeout=30).run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in at.caption)
    assert "차트 데이터가 없어 시각 분석" in body


def test_vision_section_button_click_shows_patterns(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from dashboard import chart_vision

    monkeypatch.setattr(
        chart_vision,
        "analyze_chart_patterns",
        lambda hist, ticker, **kwargs: {
            "ok": True,
            "patterns": [{
                "kind": "triangle_convergence", "confidence": 0.8,
                "description": "고점 하락·저점 상승 수렴",
                "implication": "변동성 축소 후 방향성 돌파 대기",
            }],
            "summary": "삼각수렴 진행 중",
            "ticker": ticker,
        },
    )

    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
{_VISION_HIST_SNIPPET}
from dashboard import chart_workbench_ui
chart_workbench_ui.render_analysis_rail({_VISION_SNAPSHOT}, hist=hist)
"""
    at = AppTest.from_string(script, default_timeout=30).run()
    at.button[0].click().run()

    assert not at.exception, str(at.exception)
    body = " ".join(str(item.value) for item in at.markdown)
    body += " " + " ".join(str(item.value) for item in at.caption)
    assert "triangle_convergence" in body
    assert "삼각수렴 진행 중" in body
    assert "변동성 축소 후 방향성 돌파 대기" in body


def test_vision_section_button_click_shows_warning_on_failure(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from dashboard import chart_vision

    monkeypatch.setattr(
        chart_vision,
        "analyze_chart_patterns",
        lambda hist, ticker, **kwargs: {
            "ok": False, "reason": "invalid_llm_response", "patterns": [], "summary": "",
        },
    )

    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
{_VISION_HIST_SNIPPET}
from dashboard import chart_workbench_ui
chart_workbench_ui.render_analysis_rail({_VISION_SNAPSHOT}, hist=hist)
"""
    at = AppTest.from_string(script, default_timeout=30).run()
    at.button[0].click().run()

    assert not at.exception, str(at.exception)
    assert any("invalid_llm_response" in str(w.value) for w in at.warning)


def test_orderflow_empty_state_message_is_reason_specific():
    from dashboard import chart_workbench_ui

    assert "ORDERFLOW_CAPTURE_ENABLED=true" in chart_workbench_ui._orderflow_empty_message(
        "capture_not_configured"
    )
    assert "ORDERFLOW_CAPTURE_ENABLED=true" not in chart_workbench_ui._orderflow_empty_message(
        "capture_empty"
    )
    assert "리플레이" in chart_workbench_ui._orderflow_empty_message("replay_isolated")
