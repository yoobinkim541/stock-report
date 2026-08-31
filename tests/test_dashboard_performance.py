"""대시보드 첫 렌더 성능 계약.

절대 실행시간보다 네트워크 경계와 중복 호출을 고정한다. 실제 provider는
테스트에서 주입하고, Streamlit AppTest는 페이지가 무예외로 그려지는지만 확인한다.
"""
from __future__ import annotations

import json
import os
import sys
import time

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _page_script(extra: str = "") -> str:
    # 기존 페이지 공통 스텁을 재사용해 성능 계약만 좁게 검증한다.
    from tests.test_dashboard_pages import _STUBS

    return _STUBS + "\n" + extra


def test_cached_holdings_deduplicates_provider_calls(monkeypatch):
    """공용 holdings cache는 같은 런에서 provider를 한 번만 호출해야 한다."""
    from dashboard import cached, data

    calls = []
    rows = [{"ticker": "MSFT", "name": "Microsoft", "value": 100,
             "ret": 1.0, "weight": 100.0}]
    monkeypatch.setattr(data, "load_holdings", lambda: calls.append(1) or rows)
    cached.holdings.clear()

    assert cached.holdings() == rows
    assert cached.holdings() == rows
    assert len(calls) == 1


def test_home_uses_shared_holdings_boundary():
    """홈은 data.load_holdings를 직접 호출하지 않고 공용 cache 결과를 사용한다."""
    script = _page_script(
        """
rows = [{"ticker": "MSFT", "name": "Microsoft", "value": 100,
         "ret": 1.0, "weight": 100.0}]
cached.holdings = lambda: rows
data.load_holdings = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("홈에서 provider 직접 호출"))
cached.portfolio_summary = lambda: {"total_usd": 100.0, "pnl_usd": 1.0,
                                    "return_pct": 1.0, "cash_usd": 0.0}
data.load_kr_holdings = lambda *a, **k: {}
from dashboard.pages import home
home.render()
"""
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_home_first_paint_skips_heavy_market_loaders():
    """첫 홈 렌더는 상세 시장 네트워크 loader를 호출하지 않아야 한다."""
    script = _page_script(
        """
def _unexpected(*args, **kwargs):
    raise AssertionError("첫 렌더에서 상세 시장 loader 호출")
cached.market_indicators = _unexpected
cached.macro_assets = _unexpected
cached.sp500_valuation = _unexpected
from dashboard.pages import home
home.render()
"""
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert any("시장 지표" in str(block.value) for block in at.markdown)
    assert any("상세 시장 데이터" in str(block.value) for block in at.info)


def test_home_detail_load_is_explicit_and_reuses_cached_results():
    """상세 버튼 전에는 loader가 금지되고, 클릭 후에만 실행된다."""
    script = _page_script(
        """
def _loader(kind, value):
    if not st.session_state.get("_home_market_details_loaded"):
        raise AssertionError("상세 버튼 전 loader 실행")
    return value
cached.market_indicators = lambda: _loader("indicators", {"fear_greed": None, "indices": []})
cached.macro_assets = lambda: _loader("macro", [])
cached.sp500_valuation = lambda: _loader("valuation", {})
cached.accumulation = lambda: {}
cached.market_temp_history = lambda: []
from dashboard.pages import home
home.render()
"""
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.session_state["_home_market_details_loaded"] = False
    at.run()
    assert not at.exception, str(at.exception)
    at.button(key="_home_load_market_details").click().run()
    assert not at.exception, str(at.exception)
    assert at.session_state["_home_market_details_loaded"] is True
    assert any("상세 시장 데이터 로드됨" in str(block.value) for block in at.caption)


def test_entry_app_defers_networked_accumulation_rail():
    """엔트리 첫 렌더는 모으기 가격 계산을 열지 않고 명시적 버튼만 보여준다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    at = AppTest.from_file(os.path.join(root, "dashboard", "app.py"), default_timeout=20)
    at.session_state["_authed"] = True
    at.run()

    assert not at.exception, str(at.exception)
    assert at.button(key="_show_accum_rail_btn")


def test_market_tape_prefers_fresh_snapshot(monkeypatch, tmp_path):
    """신선한 마퀴 snapshot은 yfinance fallback 없이 source/freshness를 보존한다."""
    from dashboard import views

    path = tmp_path / "market_tape.json"
    rows = [{"label": "S&P500", "value": 6000.0, "chg": 12.0, "pct": 0.2}]
    path.write_text(json.dumps({"rows": rows, "source": "snapshot-cron",
                                "asof": "2026-08-31T09:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(views, "_TAPE_SNAP", str(path))
    monkeypatch.setattr(views, "_market_tape_live", lambda: (_ for _ in ()).throw(
        AssertionError("신선 snapshot에서 live 호출")))
    now = time.time()
    os.utime(path, (now, now))

    out = views.market_tape()

    assert out and out[0]["label"] == "S&P500"
    assert out[0]["source"] == "snapshot-cron"
    assert out[0]["freshness"] == "fresh"


def test_market_tape_missing_snapshot_uses_live_and_writes_atomic_snapshot(monkeypatch, tmp_path):
    """snapshot이 없을 때만 live를 호출하고 다음 렌더용 snapshot을 기록한다."""
    from dashboard import views

    path = tmp_path / "market_tape.json"
    rows = [{"label": "VIX", "value": 18.0, "chg": -1.0, "pct": -5.2}]
    calls = []
    monkeypatch.setattr(views, "_TAPE_SNAP", str(path))
    monkeypatch.setattr(views, "_market_tape_live", lambda: calls.append(1) or rows)

    out = views.market_tape()

    assert out and len(calls) == 1
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["rows"][0]["label"] == "VIX"
    assert out[0]["source"] == "yfinance" and out[0]["freshness"] == "fresh"


def test_market_tape_stale_snapshot_is_graceful_without_live(monkeypatch, tmp_path):
    """stale 마퀴는 첫 렌더에서 live를 부르지 않고 stale 경계를 보존한다."""
    from dashboard import views

    path = tmp_path / "market_tape.json"
    rows = [{"label": "VIX", "value": 18.0, "chg": -1.0, "pct": -5.2}]
    path.write_text(json.dumps({"rows": rows, "source": "snapshot-cron"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(views, "_TAPE_SNAP", str(path))
    monkeypatch.setattr(views, "_market_tape_live", lambda: (_ for _ in ()).throw(
        AssertionError("stale snapshot에서 live 호출")))

    out = views.market_tape()

    assert out and out[0]["freshness"] == "stale"


def test_market_tape_first_paint_without_snapshot_skips_live(monkeypatch, tmp_path):
    """마퀴 snapshot이 없으면 첫 렌더는 live 네트워크를 기다리지 않는다."""
    from dashboard import views

    monkeypatch.setattr(views, "_TAPE_SNAP", str(tmp_path / "missing.json"))
    monkeypatch.setattr(views, "_market_tape_live", lambda: (_ for _ in ()).throw(
        AssertionError("첫 렌더에서 market tape live 호출")))

    assert views.market_tape(allow_live=False) == []


def test_stale_heatmap_does_not_block_first_paint(monkeypatch, tmp_path):
    """첫 렌더의 stale/partial heatmap은 503종목 live fallback을 실행하지 않는다."""
    from dashboard import views

    path = tmp_path / "sp500_heatmap.json"
    partial = [{"ticker": "AAPL", "name": "Apple", "sector_kr": "기술",
                "market_cap": 1e9, "pct": 1.0}]
    path.write_text(json.dumps(partial), encoding="utf-8")
    old = time.time() - 7200
    os.utime(path, (old, old))
    monkeypatch.setattr(views, "_HEATMAP_SNAP", str(path))
    monkeypatch.setattr(views, "_sp500_heatmap_live", lambda: (_ for _ in ()).throw(
        AssertionError("첫 렌더에서 stale heatmap live 호출")))

    assert views.sp500_heatmap(allow_live=False) == []


def test_stale_complete_heatmap_is_returned_without_live_fallback(monkeypatch, tmp_path):
    """완전한 stale snapshot도 첫 렌더에서 live 503종목 호출 없이 표시한다."""
    from dashboard import views

    path = tmp_path / "sp500_heatmap.json"
    rows = [{"ticker": f"T{i}", "name": f"Ticker {i}", "sector_kr": "기술",
             "market_cap": 1e9 + i, "pct": 0.0} for i in range(400)]
    path.write_text(json.dumps(rows), encoding="utf-8")
    old = time.time() - 7200
    os.utime(path, (old, old))
    monkeypatch.setattr(views, "_HEATMAP_SNAP", str(path))
    monkeypatch.setattr(views, "_sp500_heatmap_live", lambda: (_ for _ in ()).throw(
        AssertionError("첫 렌더에서 stale heatmap live 호출")))

    assert views.sp500_heatmap(allow_live=False) == rows
    assert views.heatmap_status("S&P 500")["freshness"] == "stale"
