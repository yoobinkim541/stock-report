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


# --- C-03 / C-14 / C-01 / C-02 / C-12 회귀 -------------------------------------

def test_analysis_snapshot_relative_strength_uses_daily_bars_for_intraday_chart():
    """C-12: 1h 차트에서도 상대강도는 거래일 단위 일봉끼리 비교한다."""
    document = chart_document.default_chart_document("MSFT")
    document["timeframe"] = "1h"
    days = pd.bdate_range("2026-01-01", periods=100)
    intraday = _hist(100, step=0.4)
    intraday.index = pd.date_range("2026-05-01 13:30", periods=100, freq="h", tz="UTC")
    asset_daily = _hist(100, step=0.4)
    asset_daily.index = (days + pd.Timedelta(hours=14)).tz_localize("UTC")
    bench_daily = _hist(100, step=0.1)
    bench_daily.index = days.tz_localize("UTC")
    seen = []

    def load_ohlc(symbol, timeframe):
        seen.append((symbol, timeframe))
        if timeframe != "1d":
            return intraday
        return bench_daily if symbol == "QQQ" else asset_daily

    out = chart_workbench.build_analysis_snapshot(
        document, intraday, ohlc_loader=load_ohlc,
        fundamental_loader=lambda _symbol: {}, alert_loader=lambda _symbol: [],
    )

    assert ("MSFT", "1d") in seen
    assert out["relative_strength"]["ok"] is True, out["relative_strength"]
    assert out["relative_strength"]["trading_days"] == 100
    assert out["context"] == {
        "timeframe": "1h", "session": "regular", "timezone": "America/New_York", "replay_cursor": None,
    }


_IDENTITY_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
import pandas as pd
import streamlit as st
from dashboard import chart_workbench_ui
variant = st.session_state.get("variant", 0)
values = list(range(100, 200)) + ([200] if variant == 2 else [])
hist = pd.DataFrame({{
    "Open": values, "High": [v + 1 for v in values], "Low": [v - 1 for v in values],
    "Close": values, "Volume": [1000] * len(values),
}}, index=pd.date_range("2026-08-03 13:30", periods=len(values), freq="h", tz="UTC"))
snapshot = {{
    "symbol": "AAPL", "benchmark": "QQQ", "trend": {{}}, "patterns": [], "multi_timeframe": {{}},
    "seasonality": {{}}, "relative_strength": {{}}, "fundamentals": {{}}, "alerts": [], "errors": {{}},
    "data_quality": {{}}, "orderflow": {{"ok": False, "reason": "capture_not_configured"}},
    "context": {{"timeframe": "1d" if variant == 1 else "1h", "session": "regular", "replay_cursor": None}},
}}
chart_workbench_ui.render_analysis_rail(snapshot, hist=hist)
"""


def _rendered_text(at):
    parts = [str(item.value) for item in at.markdown]
    parts += [str(item.value) for item in at.caption]
    parts += [str(item.value) for item in at.warning]
    return " ".join(parts)


def test_vision_cache_is_keyed_by_analysis_identity_and_reanalysis_is_explicit(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from dashboard import chart_vision

    calls = []

    def _analyze(hist, ticker, **kwargs):
        calls.append(kwargs.get("identity"))
        return {
            "ok": True, "summary": f"분석 {len(calls)}회차", "identity": kwargs.get("identity"),
            "patterns": [{"kind": "triangle_convergence", "confidence": 0.8, "description": "", "implication": ""}],
        }

    monkeypatch.setattr(chart_vision, "analyze_chart_patterns", _analyze)
    at = AppTest.from_string(_IDENTITY_SCRIPT.format(root=ROOT), default_timeout=60).run()
    assert [b.label for b in at.button] == ["AI로 패턴 분석하기"]

    at.button[0].click().run()
    assert len(calls) == 1
    assert calls[0]["timeframe"] == "1h"
    assert "분석 1회차" in _rendered_text(at)

    at.run()  # 같은 identity 재실행/재방문 — 캐시만 사용
    assert len(calls) == 1
    assert [b.label for b in at.button] == ["다시 분석"]
    assert "분석 1회차" in _rendered_text(at)

    at.button[0].click().run()  # 명시적 다시 분석만 새 호출
    assert len(calls) == 2
    assert "분석 2회차" in _rendered_text(at)

    for variant in (1, 2):  # 다른 봉 단위, 같은 날 새 봉
        at.session_state["variant"] = variant
        at.run()
        assert not at.exception, str(at.exception)
        assert "분석 2회차" not in _rendered_text(at)
        assert [b.label for b in at.button] == ["AI로 패턴 분석하기"]
    assert len(calls) == 2

    at.session_state["variant"] = 0
    at.run()
    assert "분석 2회차" in _rendered_text(at)
    assert len(calls) == 2


def test_vision_section_warns_on_schema_violation_and_hides_ungrounded_numbers(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from dashboard import chart_vision

    monkeypatch.setenv("CHART_VISION_BUDGET_PATH", str(tmp_path / "vision-budget.json"))

    responses = {
        "bad": '{"patterns": [{"kind": "wedge", "confidence": "certain"}], "summary": ""}',
        "ungrounded": (
            '{"patterns": [{"kind": "wedge", "confidence": 0.6, '
            '"description": "현재가 999999, RSI 999, 지지선 -100", "implication": ""}], '
            '"summary": "목표가 888888"}'
        ),
    }
    mode = {"value": "bad"}
    monkeypatch.setattr(chart_vision, "_try_vision_prompt", lambda prompt, image_path, **_: responses[mode["value"]])

    at = AppTest.from_string(_IDENTITY_SCRIPT.format(root=ROOT), default_timeout=60).run()
    at.button[0].click().run()
    assert not at.exception, str(at.exception)
    assert any("invalid_llm_schema" in str(w.value) for w in at.warning)
    assert "뚜렷한 고전 패턴이 식별되지 않았습니다" not in _rendered_text(at)

    mode["value"] = "ungrounded"
    at.button[0].click().run()
    assert not at.exception, str(at.exception)
    body = _rendered_text(at)
    assert "wedge" in body
    assert "999999" not in body and "888888" not in body and "-100" not in body
    assert "미검증 수치 주장 2건" in body
    assert "관측값(봉 데이터 계산): 현재가 199" in body
    assert "RSI(14) 100.0" in body


# --- C-fix2: 리플레이 경계·AI 호출 상한 ---------------------------------------

def _cutoff_daily(after_cutoff_asset=200.0, after_cutoff_bench=100.0):
    """2026-04-22 까지 자산 100(벤치와 동일), 이후 값만 다른 일봉 — 커서 뒤 값이 새면 상대강도가 바뀐다."""
    days = pd.bdate_range("2025-11-03", "2026-05-29")
    cutoff = pd.Timestamp("2026-04-22")
    asset = [100.0 if day <= cutoff else after_cutoff_asset for day in days]
    bench = [100.0 if day <= cutoff else after_cutoff_bench for day in days]

    def frame(values):
        return pd.DataFrame({"Open": values, "High": values, "Low": values, "Close": values,
                             "Volume": 1_000}, index=days)

    return frame(asset), frame(bench)


@pytest.mark.parametrize("timeframe, cursor, expected_as_of", [
    # 1h 커서 4/22 16:00Z(12:00 ET) — 그날 일봉은 16:00 ET 마감 전이라 제외된다.
    ("1h", "2026-04-22T16:00:00+00:00", "2026-04-21"),
    # W-FRI 주봉 커서 4/17 — 그 주 금요일 마감까지만.
    ("1wk", "2026-04-17T00:00:00", "2026-04-17"),
])
def test_C_fix2_replay_snapshot_cuts_every_loaded_series_at_cursor_close(timeframe, cursor, expected_as_of):
    hourly = _hist(300)
    hourly.index = pd.date_range("2026-04-01 13:30", periods=300, freq="h", tz="UTC")

    def snapshot(asset_after, bench_after, *, replay=True):
        asset_daily, bench_daily = _cutoff_daily(asset_after, bench_after)

        def load(symbol, tf):
            if tf == "1d":
                return bench_daily if symbol == "QQQ" else asset_daily
            return hourly

        document = chart_document.default_chart_document("MSFT")
        document["timeframe"] = timeframe
        document["replay"] = {"active": replay, "cursor": cursor if replay else None}
        return chart_workbench.build_analysis_snapshot(
            document, hourly, ohlc_loader=load,
            fundamental_loader=lambda _symbol: {}, alert_loader=lambda _symbol: [],
        )

    replay = snapshot(200.0, 100.0)["relative_strength"]
    changed_future = snapshot(50.0, 300.0)["relative_strength"]
    live = snapshot(200.0, 100.0, replay=False)["relative_strength"]

    assert replay["ok"] is True, replay
    assert replay["as_of"] == expected_as_of
    assert replay["relative_strength_60d"] == 0.0
    assert changed_future == replay  # 커서 뒤 자산·벤치 값을 크게 바꿔도 동일
    assert live["as_of"] == "2026-05-29" and live["relative_strength_60d"] == pytest.approx(1.0)


def test_C_fix2_vision_button_is_disabled_when_daily_budget_is_used_up(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from dashboard import chart_vision

    monkeypatch.setenv("CHART_VISION_BUDGET_PATH", str(tmp_path / "vision-budget.json"))
    monkeypatch.setenv("CHART_VISION_DAILY_LIMIT", "1")
    calls = []
    monkeypatch.setattr(chart_vision, "_try_vision_prompt",
                        lambda prompt, image_path, **_: calls.append(1) or '{"patterns": [], "summary": ""}')

    at = AppTest.from_string(_IDENTITY_SCRIPT.format(root=ROOT), default_timeout=60).run()
    assert at.button[0].disabled is False
    assert "오늘 AI 시각 분석 0/1회 사용" in _rendered_text(at)
    at.button[0].click().run()
    assert not at.exception, str(at.exception)
    assert len(calls) == 1

    at.session_state["variant"] = 2  # 새 봉 → 새 identity 라도 오늘 상한은 소진
    at.run()
    assert at.button[0].disabled is True
    assert "오늘 AI 시각 분석 1/1회 사용" in _rendered_text(at)
    assert len(calls) == 1
