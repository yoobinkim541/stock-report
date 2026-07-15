#!/usr/bin/env python3
"""단기 레버리지 가상체결 — 기초자산 신호 매핑과 손실예산 사이징."""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bars(symbol: str, *, price: float = 100.0, tz: str = "America/New_York"):
    now = datetime.now(ZoneInfo(tz)).replace(second=0, microsecond=0)
    idx = pd.date_range(now - timedelta(minutes=34), periods=35, freq="min")
    px = price
    rows = []
    for i in range(35):
        nxt = px + (0.15 if i > 30 else 0.02)
        rows.append({"Open": px, "High": max(px, nxt) + 0.05, "Low": min(px, nxt) - 0.05,
                     "Close": nxt, "Volume": 1000.0 + i})
        px = nxt
    return pd.DataFrame(rows, index=idx)


def test_position_size_respects_remaining_loss_budget():
    from ml import intraday_axes as ax

    qty = ax.position_size(
        sleeve_nav=100_000,
        risk_frac=0.02,
        price=100,
        stop=95,
        friction=1,
        loss_budget=120,
    )

    assert qty == 20
    assert qty * (100 - 95 + 1) <= 120


def test_zero_max_trades_means_loss_budget_controls_entries():
    from ml import intraday_axes as ax

    base = {"halt": False, "now_min": 600, "close_min": 930, "flat_buffer_min": 15,
            "entry_cutoff_min": 30, "trades_today": 99, "max_trades": 0,
            "cooldown_ok": True, "held": False, "fresh": True,
            "spread": 1.0, "spread_cap": 5.0, "loss_budget": 100.0, "qty": 1}

    assert ax.entry_guards(dict(base)) == (True, "ok")
    assert ax.entry_guards({**base, "loss_budget": 0.0}) == (False, "loss_budget")


def test_entry_risk_mult_opens_explore_band():
    from crons import intraday_mock_track as eng

    params = {"theta_entry": 0.55}
    cfg = {
        "explore_enabled": True,
        "explore_entry": {"KR": 0.40, "US": 0.48},
        "explore_risk_mult": {"KR": 0.35, "US": 0.50},
    }

    assert eng._entry_risk_mult(0.60, params, cfg, "KR") == (1.0, "normal")
    assert eng._entry_risk_mult(0.41, params, cfg, "KR") == (0.35, "explore")
    assert eng._entry_risk_mult(0.47, params, cfg, "US") == (0.0, "skip")
    assert eng._entry_risk_mult(0.50, params, cfg, "US") == (0.50, "explore")
    assert eng._entry_risk_mult(0.50, params, {**cfg, "explore_enabled": False}, "KR") == (0.0, "skip")


def test_zero_max_concurrent_means_loss_budget_controls_open_positions():
    from crons import intraday_mock_track as eng

    assert eng._max_concurrent("KR", {"max_concurrent": {"KR": 0}}) == 0
    assert eng._max_concurrent("US", {"max_concurrent": {"US": 4}}) == 4


def test_us_spread_guard_uses_hard_cap_while_soft_cap_can_stay_tight():
    from crons import intraday_mock_track as eng
    from ml import intraday_axes as ax

    cfg = {"spread_cap": {"US": 5.0}, "spread_hard_cap": {"US": 50.0}}
    base = {"halt": False, "now_min": 600, "close_min": 930, "flat_buffer_min": 15,
            "entry_cutoff_min": 30, "trades_today": 0, "max_trades": 0,
            "cooldown_ok": True, "held": False, "fresh": True,
            "loss_budget": 100.0, "qty": 1}

    cap = eng._entry_spread_cap(100.0, "US", cfg)

    assert cap == 50.0
    assert ax.entry_guards({**base, "spread": 25.0, "spread_cap": cap}) == (True, "ok")
    assert ax.entry_guards({**base, "spread": 60.0, "spread_cap": cap}) == (False, "spread")


def test_open_window_guard_blocks_first_minutes_then_releases():
    """개장 첫 N분(open_buffer_min)은 진입 보류 — 개장 동시호가 스프레드 방어."""
    from ml import intraday_axes as ax

    US_OPEN = 9 * 60 + 30
    base = {"halt": False, "close_min": 16 * 60, "flat_buffer_min": 15,
            "entry_cutoff_min": 30, "trades_today": 0, "max_trades": 0,
            "cooldown_ok": True, "held": False, "fresh": True,
            "spread": 1.0, "spread_cap": 50.0, "loss_budget": 100.0, "qty": 1,
            "open_min": US_OPEN, "open_buffer_min": 3}

    assert ax.entry_guards({**base, "now_min": US_OPEN}) == (False, "open_window")
    assert ax.entry_guards({**base, "now_min": US_OPEN + 1}) == (False, "open_window")
    assert ax.entry_guards({**base, "now_min": US_OPEN + 2}) == (False, "open_window")
    assert ax.entry_guards({**base, "now_min": US_OPEN + 3}) == (True, "ok")
    assert ax.entry_guards({**base, "now_min": US_OPEN + 30}) == (True, "ok")


def test_open_window_guard_is_noop_without_open_min_key():
    """open_min 을 안 넘기는 구 호출부는 기존 동작 그대로(하위호환)."""
    from ml import intraday_axes as ax

    base = {"halt": False, "now_min": 9 * 60 + 30,
            "close_min": 16 * 60, "flat_buffer_min": 15, "entry_cutoff_min": 30,
            "trades_today": 0, "max_trades": 0, "cooldown_ok": True, "held": False,
            "fresh": True, "spread": 1.0, "spread_cap": 50.0, "loss_budget": 100.0, "qty": 1}

    assert ax.entry_guards(dict(base)) == (True, "ok")


def test_load_cfg_open_buffer_min_defaults_and_env_override(monkeypatch):
    from crons import intraday_mock_track as eng

    monkeypatch.delenv("INTRADAY_OPEN_BUFFER_MIN", raising=False)
    monkeypatch.delenv("INTRADAY_OPEN_BUFFER_MIN_KR", raising=False)
    monkeypatch.delenv("INTRADAY_OPEN_BUFFER_MIN_US", raising=False)
    cfg = eng.load_cfg()
    assert cfg["open_buffer_min"] == {"KR": 2, "US": 3}

    monkeypatch.setenv("INTRADAY_OPEN_BUFFER_MIN_US", "5")
    cfg2 = eng.load_cfg()
    assert cfg2["open_buffer_min"]["US"] == 5
    assert cfg2["open_buffer_min"]["KR"] == 2   # KR 은 별도 override 없이 기본 유지


def test_intraday_universe_expands_leverage_watchlist(monkeypatch):
    from providers import intraday_universe as iu

    monkeypatch.delenv("INTRADAY_LEVERAGE_ENABLED", raising=False)
    monkeypatch.setenv("INTRADAY_LEVERAGE_MAP", "QQQ:TQQQ,NVDA:NVDL")

    assert iu.expand_with_leverage(["QQQ", "NVDA", "AAPL"]) == ["QQQ", "NVDA", "AAPL", "TQQQ", "NVDL"]


def test_intraday_us_signal_executes_leveraged_etf_with_loss_budget(monkeypatch, tmp_path):
    from crons import intraday_mock_track as eng
    from providers import intraday_bars as ib
    from providers import intraday_universe as iu
    from providers import realtime_quotes as rq
    import ml.intraday_signal as intraday_signal

    qqq = _bars("QQQ", price=100.0)
    tqqq = _bars("TQQQ", price=50.0)
    bars = {"QQQ": qqq, "TQQQ": tqqq}
    events = []

    monkeypatch.setattr(eng, "_LEDGER_BASE", str(tmp_path / "ledger"))
    monkeypatch.setattr(eng, "_market_open", lambda mk: mk == "US")
    monkeypatch.setattr(eng, "_news_events", lambda now_epoch: [])
    monkeypatch.setattr(eng, "_record_event", lambda *a, **k: events.append({"sym": a[0], "side": a[2], **k}))
    monkeypatch.setattr(iu, "refresh", lambda mk, keep=None, **k: ["QQQ"])
    monkeypatch.setattr(iu, "current_universe", lambda mk: ["QQQ"])
    monkeypatch.setattr(ib, "load_bars", lambda sym, date=None, **k: bars.get(ib.base_symbol(sym), pd.DataFrame()))
    monkeypatch.setattr(ib, "available_dates", lambda base_dir=None: [])
    monkeypatch.setattr(intraday_signal, "compute_intraday_features",
                        lambda df: pd.DataFrame({"atr": [1.0] * len(df)}, index=df.index))
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "heartbeat_age", lambda cache=None: 1.0)
    monkeypatch.setattr(rq, "is_fresh", lambda sym, **k: True)
    monkeypatch.setattr(rq, "best", lambda sym, side, **k: float(bars[sym]["Close"].iloc[-1]) + (0.01 if side == "buy" else -0.01))

    def fake_axes(sym, mk, df, feats, **_kwargs):
        if df is None or df.empty:
            return None
        return {
            "orb": 1.0, "vwap": 1.0, "volspike": 1.0, "ofi": None,
            "news": None, "ema": 1.0, "rsi": 0.8, "bb": 0.7,
            "_meta": {"close": float(df["Close"].iloc[-1]), "atr": 1.0,
                      "bar_ts": df.index[-1].isoformat(),
                      "epoch_min": int(df.index[-1].timestamp() // 60),
                      "symbol": sym, "vol_z_tod": None, "regime_er": None},
        }

    monkeypatch.setattr(eng, "_symbol_axes", fake_axes)
    monkeypatch.setitem(eng._OPEN_MIN, "US", qqq.index[0].hour * 60 + qqq.index[0].minute)
    monkeypatch.setitem(eng._CLOSE_MIN, "US", qqq.index[-1].hour * 60 + qqq.index[-1].minute + 180)

    cfg = {**eng.load_cfg(), "markets": ["US"], "shadow": True,
           "leverage_enabled": True, "leverage_map": {"QQQ": "TQQQ"},
           "sleeve_frac": 0.20, "risk_per_trade": 0.02, "daily_loss_halt": 0.01,
           "min_notional": {"KR": 0.0, "US": 0.0}, "entry_cutoff_min": 30}
    state = eng._blank_state()

    notes = eng.run_market("US", state, cfg)

    pos = state["positions"].get("US:TQQQ")
    assert pos is not None
    assert pos["signal_ticker"] == "QQQ"
    assert pos["entry_mode"] == "normal"
    assert pos["qty"] * pos["risk_per_share"] <= pos["loss_budget_entry"] + 1e-9
    assert any(e["sym"] == "TQQQ" and e["side"] == "buy" for e in events)
    assert any("QQQ 신호" in n for n in notes)
