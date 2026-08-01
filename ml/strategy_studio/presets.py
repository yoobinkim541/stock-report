from __future__ import annotations


def builtin_strategy_presets() -> dict[str, dict]:
    return {
        "rsi_cash": {
            "name": "RSI 현금화 프리셋",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "universe": {"type": "list", "symbols": ["QQQ"]},
            "indicators": [
                {"name": "rsi_5", "kind": "rsi", "period": 5, "source": "close", "output": "rsi_5"},
            ],
            "rules": {
                "entry": [
                    {
                        "field": "rsi_5",
                        "op": "<=",
                        "value": 35,
                        "action": "enter_long",
                        "target_weight": 1.0,
                        "label": "rsi_oversold_entry",
                    }
                ],
                "exit": [
                    {
                        "field": "rsi_5",
                        "op": ">=",
                        "value": 65,
                        "action": "exit_all",
                        "label": "rsi_overbought_exit",
                    }
                ],
                "trim": [
                    {
                        "field": "rsi_5",
                        "op": ">=",
                        "value": 55,
                        "action": "trim_half",
                        "factor": 0.5,
                        "label": "rsi_trim",
                    }
                ],
            },
            "sizing": {"type": "fixed_pct", "position_pct": 1.0, "max_position_pct": 1.0},
            "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 3},
            "optimization": {
                "params": {"entry_rsi": [25, 30, 35], "exit_rsi": [60, 65, 70]},
                "objective": "sharpe",
            },
            "validation": {"mode": "single_pass", "min_trades": 3},
            "metadata": {
                "preset": True,
                "tags": ["preset", "rsi", "cash"],
                "description": "기존 RSI 현금화 흐름을 전략 스튜디오로 가져온 기본 예제",
            },
        },
        "ema_trend": {
            "name": "EMA 추세 프리셋",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "universe": {"type": "list", "symbols": ["QQQ"]},
            "indicators": [
                {"name": "ema_fast", "kind": "ema", "period": 20, "source": "close", "output": "ema_fast"},
                {"name": "ema_slow", "kind": "ema", "period": 50, "source": "close", "output": "ema_slow"},
            ],
            "rules": {
                "entry": [
                    {"all": [{"field": "close", "op": "cross_above", "ref": "ema_fast"}], "label": "fast_breakout"}
                ],
                "exit": [
                    {"all": [{"field": "close", "op": "cross_below", "ref": "ema_slow"}], "label": "trend_fail"}
                ],
            },
            "sizing": {"type": "fixed_pct", "position_pct": 1.0, "max_position_pct": 1.0},
            "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 3},
            "optimization": {"params": {"fast": [10, 20, 30], "slow": [40, 50, 60]}, "objective": "sharpe"},
            "validation": {"mode": "single_pass", "min_trades": 3},
            "metadata": {"preset": True, "tags": ["preset", "ema", "trend"]},
        },
        "bollinger_reversion": {
            "name": "볼린저 평균회귀 프리셋",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "universe": {"type": "list", "symbols": ["QQQ"]},
            "indicators": [
                {"name": "bb_mid", "kind": "bollinger", "period": 20, "std_mult": 2.0, "source": "close", "output": "bb_mid"},
                {"name": "rsi_14", "kind": "rsi", "period": 14, "source": "close", "output": "rsi_14"},
            ],
            "rules": {
                "entry": [
                    {
                        "all": [
                            {"field": "close", "op": "cross_below", "ref": "bb_mid_lower"},
                            {"field": "rsi_14", "op": "<=", "value": 35},
                        ],
                        "action": "enter_long",
                        "label": "bb_reversion_entry",
                    }
                ],
                "exit": [
                    {
                        "any": [
                            {"field": "close", "op": "cross_above", "ref": "bb_mid"},
                            {"field": "rsi_14", "op": ">=", "value": 60},
                        ],
                        "action": "exit_all",
                        "label": "bb_reversion_exit",
                    }
                ],
            },
            "sizing": {"type": "fixed_pct", "position_pct": 1.0, "max_position_pct": 1.0},
            "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 3},
            "optimization": {"params": {"period": [14, 20, 30], "rsi": [30, 35, 40]}, "objective": "sharpe"},
            "validation": {"mode": "single_pass", "min_trades": 3},
            "metadata": {
                "preset": True,
                "tags": ["preset", "bollinger", "mean-reversion"],
                "description": "밴드 이탈 후 과매도 구간을 잡아 반등을 노리는 평균회귀 예시",
            },
        },
        "breakout_momentum": {
            "name": "돌파 모멘텀 프리셋",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "universe": {"type": "list", "symbols": ["QQQ"]},
            "indicators": [
                {"name": "high20", "kind": "rolling", "period": 20, "method": "max", "source": "close", "output": "high20"},
                {"name": "dd20", "kind": "drawdown", "source": "close", "output": "dd20"},
            ],
            "rules": {
                "entry": [
                    {
                        "field": "close",
                        "op": "cross_above",
                        "ref": "high20",
                        "action": "enter_long",
                        "label": "breakout_entry",
                    }
                ],
                "exit": [
                    {
                        "any": [
                            {"field": "dd20", "op": "<=", "value": -0.08},
                            {"field": "close", "op": "cross_below", "ref": "high20"},
                        ],
                        "action": "exit_all",
                        "label": "breakout_exit",
                    }
                ],
            },
            "sizing": {"type": "fixed_pct", "position_pct": 1.0, "max_position_pct": 1.0},
            "costs": {"fees_bps": 5, "slippage_bps": 6, "spread_bps": 3},
            "optimization": {"params": {"lookback": [10, 20, 55], "stop": [0.05, 0.08, 0.1]}, "objective": "sharpe"},
            "validation": {"mode": "single_pass", "min_trades": 3},
            "metadata": {
                "preset": True,
                "tags": ["preset", "breakout", "momentum"],
                "description": "20일 고점 돌파와 최대 낙폭 제한으로 추세 가속을 따라가는 예시",
            },
        },
        "vwap_intraday": {
            "name": "VWAP 장중 회귀 프리셋",
            "market": "us",
            "timeframe": "5m",
            "base_symbol": "QQQ",
            "universe": {"type": "list", "symbols": ["QQQ"]},
            "indicators": [
                {"name": "vwap_20", "kind": "vwap", "period": 20, "source": "close", "output": "vwap_20"},
                {"name": "vol_z", "kind": "volume_zscore", "period": 20, "source": "volume", "output": "vol_z"},
                {"name": "rsi_14", "kind": "rsi", "period": 14, "source": "close", "output": "rsi_14"},
            ],
            "rules": {
                "entry": [
                    {
                        "all": [
                            {"field": "close", "op": "cross_below", "ref": "vwap_20"},
                            {"field": "rsi_14", "op": "<=", "value": 35},
                        ],
                        "action": "enter_long",
                        "label": "vwap_entry",
                    }
                ],
                "exit": [
                    {
                        "any": [
                            {"field": "close", "op": "cross_above", "ref": "vwap_20"},
                            {"field": "rsi_14", "op": ">=", "value": 60},
                        ],
                        "action": "exit_all",
                        "label": "vwap_exit",
                    }
                ],
            },
            "sizing": {"type": "fixed_pct", "position_pct": 1.0, "max_position_pct": 1.0},
            "costs": {"fees_bps": 4, "slippage_bps": 6, "spread_bps": 2},
            "optimization": {"params": {"period": [10, 20, 30], "rsi": [30, 35, 40]}, "objective": "sharpe"},
            "validation": {"mode": "single_pass", "min_trades": 3},
            "metadata": {
                "preset": True,
                "tags": ["preset", "intraday", "vwap"],
                "description": "장중 VWAP 재평균회귀 예시. OHLCV가 필요하다.",
            },
        },
    }
