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
