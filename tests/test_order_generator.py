import order_generator


def test_order_rows_have_aligned_columns(monkeypatch):
    monkeypatch.setattr(order_generator, "fetch_qqq_data", lambda: {"drawdown_pct": -30.0})
    monkeypatch.setattr(order_generator, "fetch_rsi", lambda ticker: 40.0)
    monkeypatch.setattr(order_generator, "fetch_vix", lambda: 25.0)
    monkeypatch.setattr(order_generator, "fetch_exchange_rate", lambda: 1545.0)
    monkeypatch.setattr(order_generator, "classify_market", lambda qqq, rsi, vix: ("bear", 5))
    monkeypatch.setattr(order_generator, "calculate_dca", lambda market_type, phase_key, fx: {
        "total_krw": 200000,
        "by_ticker": {
            "NOW": 46000,
            "ORCL": 46000,
            "NVDA": 40000,
            "MSFT": 28000,
            "GOOGL": 20000,
            "CRM": 14000,
            "UNH": 6000,
        },
    })
    monkeypatch.setattr(order_generator, "fetch_prices", lambda tickers: {
        "NOW": 117.90,
        "ORCL": 230.33,
        "NVDA": 214.75,
        "MSFT": 427.34,
        "GOOGL": 358.99,
        "CRM": 190.61,
        "UNH": 377.00,
    })

    report = order_generator.generate()
    lines = report.splitlines()
    rows = [line for line in lines if "@$" in line]

    assert len(rows) == 7

    # 모든 데이터 행에서 "원" / "주" / "@$" 위치가 동일해야 함
    ref_won = order_generator._display_width(rows[0].split("원", 1)[0])
    ref_joo = order_generator._display_width(rows[0].split("주", 1)[0])
    ref_at  = order_generator._display_width(rows[0].split("@$", 1)[0])

    for row in rows[1:]:
        assert order_generator._display_width(row.split("원", 1)[0]) == ref_won
        assert order_generator._display_width(row.split("주", 1)[0]) == ref_joo
        assert order_generator._display_width(row.split("@$", 1)[0]) == ref_at
