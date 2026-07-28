"""Intraday position lifecycle tests: partial exits before final exits."""

from ml.intraday_lifecycle import apply_filled_leg, evaluate_exit_plan, initialize_lifecycle


def _pos(**overrides):
    pos = {
        "ticker": "005930",
        "qty": 10,
        "entry_price": 100.0,
        "risk_per_share": 2.0,
        "stop": 98.0,
        "target": 104.0,
        "entry_min": 600,
    }
    pos.update(overrides)
    return pos


def test_lifecycle_takes_half_profit_at_one_r_and_moves_stop_to_breakeven():
    pos = initialize_lifecycle(_pos())

    legs = evaluate_exit_plan(pos, {"h": 102.2, "l": 100.5, "c": 101.8}, 0.7, 610, 930, {})

    assert [leg["reason"] for leg in legs] == ["partial_target"]
    assert legs[0]["qty"] == 5
    assert legs[0]["ref_price"] == 102.0
    assert pos["qty"] == 10
    apply_filled_leg(pos, legs[0], {})
    assert pos["qty"] == 5
    assert pos["lifecycle"]["partial_target_taken"] is True
    assert pos["stop"] == 100.0


def test_lifecycle_can_take_half_stop_then_full_stop_on_remaining_quantity():
    pos = initialize_lifecycle(_pos())

    first = evaluate_exit_plan(pos, {"h": 100.4, "l": 98.8, "c": 99.0}, 0.7, 610, 930, {})
    apply_filled_leg(pos, first[0], {})
    second = evaluate_exit_plan(pos, {"h": 99.2, "l": 97.6, "c": 97.8}, 0.7, 611, 930, {})

    assert [leg["reason"] for leg in first] == ["partial_stop"]
    assert first[0]["qty"] == 5
    assert first[0]["ref_price"] == 99.0
    assert [leg["reason"] for leg in second] == ["stop"]
    assert second[0]["qty"] == 5
    assert second[0]["final"] is True


def test_lifecycle_prefers_stop_when_stop_and_target_touch_same_bar():
    pos = initialize_lifecycle(_pos())

    legs = evaluate_exit_plan(pos, {"h": 104.5, "l": 97.5, "c": 100.0}, 0.7, 610, 930, {})

    assert [leg["reason"] for leg in legs] == ["stop"]
    assert legs[0]["qty"] == 10
    assert legs[0]["final"] is True


def test_intraday_engine_keeps_position_after_partial_exit_and_logs_final_total(monkeypatch, tmp_path):
    from crons import intraday_mock_track as eng
    from ml.adaptive import Ledger

    fills = iter([
        {"price": 102.0, "penalty": 0.0, "ok": True, "mode": "shadow"},
        {"price": 104.0, "penalty": 0.0, "ok": True, "mode": "shadow"},
    ])
    monkeypatch.setattr(eng, "_fill", lambda *a, **k: next(fills))
    monkeypatch.setattr(eng, "_record_event", lambda *a, **k: None)

    ledger = Ledger("kr_intraday", base_dir=tmp_path)
    state = eng._blank_state()
    key = "KR:005930"
    state["positions"][key] = initialize_lifecycle(_pos(
        decision_id="d1",
        shadow=True,
        penalty_entry=0.0,
        entry_epoch=0,
    ))

    partial_ok = eng._do_exit(
        state, key, state["positions"][key], "partial_target", 102.0, "KR", {},
        ledger, qty=5, final=False, notes=[],
    )
    assert partial_ok is True
    assert state["positions"][key]["qty"] == 5
    assert state["positions"][key]["lifecycle"]["partial_target_taken"] is True
    assert state["positions"][key]["stop"] == 100.0
    final_ok = eng._do_exit(
        state, key, state["positions"][key], "target", 104.0, "KR", {},
        ledger, qty=5, final=True, notes=[],
    )

    assert final_ok is True
    assert key not in state["positions"]
    outcomes = ledger.read_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["qty"] == 10
    assert outcomes[0]["exit_reason"] == "target"
    assert outcomes[0]["exit_legs"][0]["reason"] == "partial_target"


def test_intraday_run_records_us_entry_skip_diagnostics(monkeypatch, tmp_path):
    import json
    import pandas as pd
    from crons import intraday_mock_track as eng
    from providers import intraday_bars as ib
    from providers import intraday_universe as iu
    from providers import realtime_quotes as rq
    import ml.intraday_signal as intraday_signal

    now = pd.Timestamp("2026-07-27 10:10:00", tz="America/New_York")
    idx = pd.date_range(now - pd.Timedelta(minutes=34), periods=35, freq="min")
    bars = pd.DataFrame({
        "Open": [100.0] * 35,
        "High": [101.0] * 35,
        "Low": [99.0] * 35,
        "Close": [100.5] * 35,
        "Volume": [1000.0] * 35,
    }, index=idx)

    monkeypatch.setattr(eng, "_LEDGER_BASE", str(tmp_path))
    monkeypatch.setattr(eng, "_market_open", lambda mk: mk == "US")
    monkeypatch.setattr(eng, "_now_local", lambda mk: now.to_pydatetime())
    monkeypatch.setattr(eng, "_news_events", lambda now_epoch: [])
    monkeypatch.setattr(iu, "refresh", lambda mk, keep=None, **k: ["QQQ"])
    monkeypatch.setattr(ib, "load_bars", lambda sym, date=None, **k: bars)
    monkeypatch.setattr(ib, "available_dates", lambda base_dir=None: [])
    monkeypatch.setattr(intraday_signal, "compute_intraday_features",
                        lambda df: pd.DataFrame({"atr": [1.0] * len(df)}, index=df.index))
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "heartbeat_age", lambda cache=None: 1.0)
    monkeypatch.setattr(rq, "is_fresh", lambda sym, **k: False)
    monkeypatch.setattr(rq, "best", lambda sym, side, **k: 100.5)
    monkeypatch.setattr(eng, "_symbol_axes", lambda sym, mk, df, feats, **k: {
        "orb": 1.0, "vwap": 1.0, "volspike": 1.0, "ofi": None,
        "news": None, "ema": 1.0, "rsi": 0.8, "bb": 0.7,
        "_meta": {"close": 100.5, "atr": 1.0, "bar_ts": idx[-1].isoformat(),
                  "epoch_min": int(idx[-1].timestamp() // 60),
                  "symbol": sym, "vol_z_tod": None, "regime_er": None},
    })
    monkeypatch.setitem(eng._OPEN_MIN, "US", 9 * 60 + 30)
    monkeypatch.setitem(eng._CLOSE_MIN, "US", 16 * 60)

    cfg = {**eng.load_cfg(), "markets": ["US"], "shadow": True,
           "sleeve_frac": 0.20, "risk_per_trade": 0.02, "daily_loss_halt": 0.01,
           "min_notional": {"KR": 0.0, "US": 0.0}, "entry_cutoff_min": 30,
           "stale_flat_min": 100_000}

    eng.run_market("US", eng._blank_state(), cfg)

    rows = [
        json.loads(line)
        for line in (tmp_path / "us_intraday_diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["market"] == "US"
    assert rows[-1]["skip_reasons"]["stale_data"] == 1
    assert rows[-1]["candidates"] == 1
    assert rows[-1]["entries"] == 0
