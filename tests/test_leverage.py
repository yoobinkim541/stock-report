"""tests/test_leverage.py — 레버리지 ETF 백테스터 + ML 신호 단위 테스트"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── leverage_backtester ────────────────────────────────────────────────────────

def test_rolling_drawdown_basic():
    from ml.leverage_backtester import rolling_drawdown
    idx   = pd.date_range("2024-01-01", periods=5, freq="B")
    close = pd.Series([100, 110, 105, 90, 95], index=idx, dtype=float)
    dd    = rolling_drawdown(close)
    assert float(dd.iloc[0]) == pytest.approx(0.0)
    assert float(dd.iloc[2]) < 0          # 110→105 하락
    assert float(dd.min()) < -0.15        # 110→90: -18%


def test_compute_entry_stats_returns_instruments():
    from ml.leverage_backtester import (
        EntryEvent, compute_entry_stats, INSTRUMENTS, HORIZONS,
    )
    # 가짜 이벤트 10개
    events = []
    for i in range(10):
        fwd = {name: {h: 0.05 * (i + 1) / 10 for h in HORIZONS}
               for name in INSTRUMENTS}
        events.append(EntryEvent(
            date=pd.Timestamp(f"2023-0{i%9+1}-01"),
            drawdown=-0.10 - i * 0.01,
            vix=20.0,
            rsi=45.0,
            fg_proxy=40.0,
            ma200_gap=-0.05,
            forward_returns=fwd,
            features={"drawdown": -0.10},
        ))

    stats = compute_entry_stats(events, drawdown_range=(-0.20, -0.05))
    assert len(stats) == len(INSTRUMENTS)
    for name in INSTRUMENTS:
        s = stats[name]
        assert s.n_entries == 10
        assert all(np.isfinite(v) for v in s.median_ret.values())
        assert all(0.0 <= v <= 1.0 for v in s.hit_rate.values())


def test_kelly_weight_range():
    from ml.leverage_signal import _kelly_weight
    # 좋은 조건 — 양수
    w1 = _kelly_weight(0.7, 0.15, 0.05)
    assert 0.0 <= w1 <= 0.40
    # 나쁜 조건 — 0
    w2 = _kelly_weight(0.3, 0.02, 0.20)
    assert w2 == 0.0
    # edge cases
    assert _kelly_weight(0.0, 0.1, 0.1) == 0.0
    assert _kelly_weight(1.0, 0.1, 0.0) == 0.0


def test_entry_advice_logic():
    from ml.leverage_signal import _entry_advice
    # 폭락 구간 → 적극 진입
    a1 = _entry_advice(-0.30, 35.0, 25.0, 15.0)
    assert "적극" in a1 or "분할" in a1
    # 고평가 구간 → 보류
    a2 = _entry_advice(-0.01, 12.0, 72.0, 85.0)
    assert "보류" in a2


def test_next_entry_levels():
    from ml.leverage_signal import _next_entry_levels
    # -8% 낙폭일 때 다음 타점은 -10%, -15%, -20%...
    levels = _next_entry_levels(-0.08)
    assert all(l < -0.08 for l in levels)
    assert len(levels) <= 4
    # 이미 -35% 낙폭 → 타점 없거나 매우 적음
    levels2 = _next_entry_levels(-0.35)
    assert len(levels2) <= 2


def test_format_leverage_report_structure():
    from ml.leverage_signal import EntrySignal, InstrumentSignal, format_leverage_report

    insts = {
        name: InstrumentSignal(
            name=name, recommended_weight=0.2, expected_ret_30d=0.05,
            expected_ret_90d=0.12, downside_p25_30d=-0.03, hit_rate_30d=0.65,
            max_hist_dd=-0.35, risk_reward_30d=0.14, ml_pred_30d=0.04,
        )
        for name in ["SGOV", "QLD", "TQQQ", "SOXL", "UPRO"]
    }

    sig = EntrySignal(
        current_drawdown=-0.12, current_vix=22.0, current_rsi=42.0,
        fg_proxy=38.0, ma200_gap=-0.05, bucket_label="-15%~-10%",
        n_similar=25, instruments=insts, total_weight=1.0,
        entry_advice="⚡ 분할 진입",
        next_entry_levels=[-0.15, -0.20, -0.25],
        stop_signal="VIX > 40 → 축소",
        timestamp="2026-06-07 17:00 KST",
    )
    report = format_leverage_report(sig)
    assert "레버리지 ETF" in report
    assert "QLD" in report
    assert "TQQQ" in report
    assert "SGOV" in report
    assert "손익비" in report
    assert "청산" in report
    assert len(report) > 200


def test_leverage_bot_command_wiring():
    from telegram_bot import _COMMAND_HANDLERS, BOT_COMMANDS
    assert "/leverage" in _COMMAND_HANDLERS
    cmds = [c["command"] for c in BOT_COMMANDS]
    assert "leverage" in cmds


# ── 비판 리뷰 회귀 테스트 (2026-06-10) ────────────────────────────────────────

def _synth_prices(n: int = 504, seed: int = 7) -> dict:
    idx = pd.date_range("2021-01-04", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    return {
        "QQQ":  pd.Series(100 * (1 + rng.normal(0.0003, 0.012, n)).cumprod(), index=idx),
        "QLD":  pd.Series(100 * (1 + rng.normal(0.0006, 0.024, n)).cumprod(), index=idx),
        "SGOV": pd.Series(100 * 1.00018 ** np.arange(n), index=idx),
        "^VIX": pd.Series(rng.uniform(15, 35, n), index=idx),
    }


_ENGINE_PARAMS = {
    "instrument": "QLD", "min_dd": -0.05, "max_vix_entry": 40.0,
    "min_rsi_entry": 50.0, "lev_weight": 0.30, "sgov_floor": 0.40,
    "exit_ma": 20, "exit_vix": 38.0, "trailing_stop": -0.10, "hold_days_max": 63,
}


def test_backtest_engine_no_idle_cash():
    """무진입 전략은 SGOV 수익률을 따라가야 함 — 유휴현금(0% 수익) 이중계상 방지."""
    from ml.leverage_optimizer import BacktestEngine
    px = _synth_prices()
    params = {**_ENGINE_PARAMS, "min_dd": -0.99}   # 진입 조건 사실상 불가능
    r = BacktestEngine(params, px).run()
    sgov_cagr = 1.00018 ** 252 - 1
    assert r.n_trades == 0
    assert abs(r.cagr - sgov_cagr) < 0.01   # 절반이 현금으로 잠기면 ~2.3%로 추락


def test_backtest_engine_eval_start():
    """eval_start 지정 시 신호는 전체 히스토리, 평가는 eval_start 이후만."""
    from ml.leverage_optimizer import BacktestEngine
    px  = _synth_prices()
    idx = px["QQQ"].index
    r = BacktestEngine(_ENGINE_PARAMS, px, eval_start=idx[252]).run()
    assert r.equity.index[0] >= idx[252]
    assert abs(float(r.equity.iloc[0]) - 1.0) < 0.05


def test_entry_signal_gate_zeroes_weights():
    """Optuna 진입 조건 미충족 시 권장 비중도 0이어야 함 (조언-비중 자기모순 방지)."""
    from ml.leverage_signal import build_entry_signal
    context = {
        "current_drawdown": -0.02,                       # 낙폭 미달
        "current_feats": {"vix": 18.0, "rsi": 60.0, "fg_proxy": 55.0,
                          "ma200_gap": 0.03},
        "current_stats": {},
        "current_bucket": (-0.05, 0.0),
        "n_similar": 0,
        "opt_min_dd": -0.10, "opt_max_vix_entry": 35.0, "opt_min_rsi_entry": 40.0,
        "opt_lev_weight": 0.25,
    }
    sig = build_entry_signal(context, model=None)
    lev_sum = sum(inst.recommended_weight
                  for name, inst in sig.instruments.items() if name != "SGOV")
    assert lev_sum == pytest.approx(0.0)
    assert sig.instruments["SGOV"].recommended_weight == pytest.approx(1.0)
    assert "보류" in sig.entry_advice or "미충족" in sig.entry_advice


def test_ranker_labels_alignment_stable():
    """동일 날짜 내 라벨 순서가 입력 순서와 일치해야 함 (비안정 정렬 회귀 방지)."""
    from ml.ranker import _make_ranker_labels
    dates  = pd.to_datetime(["2024-01-02"] * 8 + ["2024-01-03"] * 8)
    excess = np.array([0.08, -0.04, 0.02, -0.01, 0.06, -0.06, 0.01, 0.03] * 2)
    labels, groups = _make_ranker_labels(excess, pd.Index(dates))
    assert list(groups) == [8, 8]
    # 날짜가 이미 정렬돼 있으므로 stable 정렬이면 입력 순서 보존 → 두 날짜 라벨 동일
    assert list(labels[:8]) == list(labels[8:])
    # 최고 excess 행이 최상위 버킷
    assert labels[0] == 3 and labels[5] == 0
