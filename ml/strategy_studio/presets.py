from __future__ import annotations


def builtin_strategy_presets() -> dict[str, dict]:
    presets = {
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
        "mean_reversion": {
            "name": "볼린저·RSI 평균회귀",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "benchmark": "QQQ",
            "data_profile": "global_swing",
            "execution_profile": "global_swing",
            "universe": {"type": "list", "symbols": ["QQQ"], "point_in_time": True},
            "indicators": [
                {"name": "bb_20", "kind": "bollinger", "period": 20, "std_mult": 2.0, "source": "close", "output": "bb_20"},
                {"name": "rsi_14", "kind": "rsi", "period": 14, "source": "close", "output": "rsi_14"},
                {"name": "atr_14", "kind": "atr", "period": 14, "source": "close", "output": "atr_14"},
            ],
            "signal": {"type": "rule", "plugin": "bollinger", "min_confidence": 0.55},
            "rules": {
                "entry": [{"all": [{"field": "rsi_14", "op": "<=", "value": 35}, {"field": "close", "op": "cross_below", "ref": "bb_lower"}], "action": "enter_long", "label": "mean_reversion_entry"}],
                "exit": [{"any": [{"field": "rsi_14", "op": ">=", "value": 60}, {"field": "close", "op": "cross_above", "ref": "bb_mid"}], "action": "exit_all", "label": "mean_reversion_exit"}],
            },
            "sizing": {"type": "risk_budget", "risk_pct": 0.01, "max_position_pct": 0.35},
            "portfolio": {"optimizer": "risk_budget", "max_position_pct": 0.35, "target_volatility": 0.15, "max_turnover": 0.25},
            "execution": {"profile": "global_swing", "stop_policy": "atr_trailing", "time_stop_bars": 15},
            "costs": {"fees_bps": 2, "slippage_bps": 5, "spread_bps": 2},
            "validation": {"mode": "purged_walk_forward", "min_trades": 30, "min_test_periods": 4, "benchmarks": ["buy_and_hold", "rsi_baseline"]},
            "promotion": {"environment": "sandbox"},
            "metadata": {
                "preset": True,
                "tags": ["preset", "mean-reversion", "atr"],
                "description": "볼린저 밴드와 RSI 과매도 조건에 ATR 위험예산을 결합한 설명 가능한 평균회귀 기준선",
                "universe_warning": "단일 QQQ 유니버스는 시점별 구성종목 검증 대상이 아니며, 결과는 해당 ETF 가격에 한정됩니다.",
            },
        },
        "breakout_with_trailing_stop": {
            "name": "돌파·ATR 트레일링 스톱",
            "market": "us",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "benchmark": "QQQ",
            "data_profile": "global_swing",
            "execution_profile": "global_swing",
            "universe": {"type": "screen", "definition": "liquid_large_caps", "point_in_time": False},
            "indicators": [
                {"name": "high_55", "kind": "rolling", "period": 55, "method": "max", "source": "close", "output": "high_55"},
                {"name": "atr_14", "kind": "atr", "period": 14, "source": "close", "output": "atr_14"},
                {"name": "dd_20", "kind": "drawdown", "source": "close", "output": "dd_20"},
            ],
            "signal": {"type": "rule", "plugin": "rolling", "breakout_lookback": 55},
            "rules": {
                "entry": [{"field": "close", "op": "cross_above", "ref": "high_55", "action": "enter_long", "label": "breakout_entry"}],
                "exit": [{"any": [{"field": "dd_20", "op": "<=", "value": -0.10}, {"field": "close", "op": "cross_below", "ref": "high_55"}], "action": "exit_all", "label": "breakout_exit"}],
            },
            "sizing": {"type": "risk_budget", "risk_pct": 0.01, "max_position_pct": 0.20},
            "portfolio": {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.20, "max_gross_exposure": 1.0, "max_turnover": 0.30},
            "execution": {"profile": "global_swing", "stop_policy": "atr_trailing", "atr_multiple": 3.0, "time_stop_bars": 40},
            "costs": {"fees_bps": 2, "slippage_bps": 6, "spread_bps": 3},
            "validation": {"mode": "cpcv", "strictly_chronological": True, "min_trades": 30, "min_test_periods": 4, "benchmarks": ["buy_and_hold", "equal_weight"]},
            "promotion": {"environment": "sandbox"},
            "metadata": {
                "preset": True,
                "tags": ["preset", "breakout", "trailing-stop"],
                "description": "55일 고점 돌파에 ATR 트레일링 스톱과 시간 청산을 결합한 추세 추종 예시",
                "universe_warning": "스크리닝 유니버스의 point-in-time 구성 이력이 없어 생존편향 경고가 표시됩니다.",
            },
        },
        "factor_ensemble": {
            "name": "모멘텀·변동성·퀄리티 앙상블",
            "market": "multi",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "benchmark": "QQQ",
            "data_profile": "global_swing",
            "execution_profile": "global_swing",
            "universe": {"type": "screen", "definition": "liquid_large_caps", "point_in_time": False},
            "features": [{"plugin": "momentum", "lookbacks": [60, 126]}, {"plugin": "volatility", "lookback": 20}, {"plugin": "quality", "source": "fundamentals"}],
            "signal": {
                "type": "ensemble",
                "aggregation": "equal_weight",
                "members": [{"type": "factor", "plugin": "momentum"}, {"type": "factor", "plugin": "volatility"}, {"type": "model", "ref": "quality_factor_v1", "fallback": "equal_weight"}],
                "min_confidence": 0.55,
            },
            "portfolio": {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.15, "max_gross_exposure": 1.0, "max_turnover": 0.25},
            "execution": {"profile": "global_swing", "partial_fill": True, "time_stop_bars": 20},
            "costs": {"fees_bps": 2, "slippage_bps": 5, "spread_bps": 2},
            "validation": {"mode": "purged_walk_forward", "min_trades": 30, "min_test_periods": 4, "benchmarks": ["buy_and_hold", "equal_weight", "rsi_baseline"]},
            "promotion": {"environment": "sandbox"},
            "metadata": {
                "preset": True,
                "tags": ["preset", "factor", "ensemble", "quality-model-slot"],
                "description": "모멘텀·변동성·퀄리티 슬롯을 동일 가중으로 합산하고 품질 모델이 없으면 equal-weight로 대체하는 앙상블 기준선",
                "universe_warning": "현재 팩터 유니버스는 point-in-time 재무·구성 이력이 없어 생존편향과 공개시점 경고가 표시됩니다.",
            },
        },
        "kr_intraday_vwap": {
            "name": "국내 5분 VWAP 리클레임",
            "market": "kr",
            "timeframe": "5m",
            "base_symbol": "005930.KS",
            "benchmark": "^KS11",
            "data_profile": "kr_intraday",
            "execution_profile": "kr_intraday",
            "universe": {"type": "screen", "definition": "stocks_in_play", "point_in_time": False},
            "features": [{"plugin": "vwap", "lookback": 20}, {"plugin": "volume_shock", "lookback": 20}, {"plugin": "market_breadth", "source": "krx"}],
            "indicators": [{"name": "vwap_20", "kind": "vwap", "period": 20, "source": "close", "output": "vwap_20"}, {"name": "volume_z", "kind": "volume_zscore", "period": 20, "source": "volume", "output": "volume_z"}],
            "signal": {"type": "rule", "plugin": "vwap", "min_confidence": 0.55},
            "rules": {
                "entry": [{"all": [{"field": "close", "op": "cross_above", "ref": "vwap_20"}, {"field": "volume_z", "op": ">=", "value": 1.0}], "action": "enter_long", "label": "vwap_reclaim"}],
                "exit": [{"field": "close", "op": "cross_below", "ref": "vwap_20", "action": "exit_all", "label": "vwap_failure"}],
            },
            "sizing": {"type": "risk_budget", "risk_pct": 0.005, "max_position_pct": 0.10},
            "portfolio": {"optimizer": "cost_aware_risk_budget", "max_position_pct": 0.10, "max_gross_exposure": 1.0, "max_turnover": 0.30},
            "execution": {"profile": "kr_intraday", "latency_ms": 500, "max_participation_rate": 0.10, "partial_fill": True, "time_stop_bars": 24},
            "costs": {"fees_bps": 3, "slippage_bps": 5, "spread_bps": 5},
            "validation": {"mode": "purged_walk_forward", "embargo_bars": 5, "min_trades": 100, "min_test_periods": 4, "benchmarks": ["buy_and_hold", "equal_weight", "rsi_baseline"]},
            "promotion": {"environment": "sandbox"},
            "metadata": {
                "preset": True,
                "tags": ["preset", "kr", "intraday", "vwap"],
                "description": "5분봉 VWAP 회복과 거래량 충격을 국내 장중 체결 프로필로 검증하는 예시",
                "universe_warning": "장중 stocks_in_play 유니버스의 과거 구성 이력이 없어 생존편향 경고가 표시됩니다.",
            },
        },
        "momentum_rank": {
            "name": "20/60일 모멘텀 랭크",
            "market": "multi",
            "timeframe": "1d",
            "base_symbol": "QQQ",
            "benchmark": "QQQ",
            "data_profile": "global_swing",
            "execution_profile": "global_swing",
            "universe": {"type": "screen", "definition": "liquid_large_caps", "point_in_time": False},
            "features": [{"plugin": "momentum", "lookbacks": [20, 60]}, {"plugin": "liquidity", "lookback": 20}],
            "signal": {"type": "factor", "plugin": "cross_sectional_rank", "lookbacks": [20, 60], "top_n": 10},
            "portfolio": {"optimizer": "equal_weight", "max_position_pct": 0.15, "max_gross_exposure": 1.0, "max_turnover": 0.30},
            "execution": {"profile": "global_swing", "partial_fill": True, "time_stop_bars": 20},
            "costs": {"fees_bps": 2, "slippage_bps": 5, "spread_bps": 2},
            "validation": {"mode": "purged_walk_forward", "min_trades": 30, "min_test_periods": 4, "benchmarks": ["buy_and_hold", "equal_weight", "rsi_baseline"]},
            "promotion": {"environment": "sandbox"},
            "metadata": {
                "preset": True,
                "tags": ["preset", "momentum", "cross-sectional"],
                "description": "20일·60일 수익률을 횡단면 순위화하고 상위 종목을 동일 비중으로 보유하는 기준선",
                "universe_warning": "현재 스크리닝 유니버스는 point-in-time 구성 이력이 없어 생존편향 경고가 표시됩니다.",
            },
        },
    }
    return {key: value for key, value in presets.items() if not key.startswith("__remove_")}
