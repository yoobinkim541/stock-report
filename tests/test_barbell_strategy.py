import barbell_strategy


def test_phase5_emergency_money_columns_align(monkeypatch):
    sent = []
    monkeypatch.setattr(barbell_strategy, "send_telegram", lambda msg: sent.append(msg) or True)

    ok = barbell_strategy.send_phase5_emergency(
        -30.0,
        1545.0,
        {"sgov_usd": 1205.0, "qqqi_usd": 2056.0, "total_usd": 12345.0},
    )

    assert ok is True
    msg = sent[0]
    lines = msg.splitlines()
    money_lines = [line for line in lines if "$" in line and ("투입 가능 금액" in line or "기준" in line)]
    assert len(money_lines) == 3

    ref = barbell_strategy._display_width(money_lines[0].split("$", 1)[0])
    for line in money_lines[1:]:
        assert barbell_strategy._display_width(line.split("$", 1)[0]) == ref


def test_smart_rebalancing_passes_drawdown_to_dca(monkeypatch):
    """스마트 리밸런싱이 낙폭 정지 가드용 drawdown_pct 를 calculate_dca 로 전달 (감사 확정 회귀).

    안전마진 배율(≤1.0 감액 전용)은 낙폭 정지를 대체하지 못하므로, drawdown_pct 를 넘기지 않으면
    -55% 이하 크래시에서 base_dca 가 5× 기반으로 산출된다.
    """
    seen = {}

    def _spy(market_type, phase_key, exchange_rate=1380.0, drawdown_pct=None):
        seen["dd"] = drawdown_pct
        return {"total_krw": 100000, "by_ticker": {}, "multiplier": 1.0}
    monkeypatch.setattr(barbell_strategy, "calculate_dca", _spy)
    monkeypatch.setattr(barbell_strategy, "calculate_safety_margin",
                        lambda *a, **k: {"multiplier": 1.0, "score": 70})
    monkeypatch.setattr(barbell_strategy, "calculate_position_analysis", lambda *a, **k: [])
    monkeypatch.setattr(barbell_strategy, "calculate_sgov_target", lambda *a, **k: {})

    port = {"total_usd": 10000.0, "sgov_usd": 1000.0}
    barbell_strategy.calculate_smart_rebalancing(port, "bear", 5, 1380.0, drawdown_pct=-60.0)
    assert seen["dd"] == -60.0
