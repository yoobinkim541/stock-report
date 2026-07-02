import order_generator


def test_order_rows_have_aligned_columns(monkeypatch):
    monkeypatch.setattr(order_generator, "fetch_qqq_data", lambda: {"drawdown_pct": -30.0})
    monkeypatch.setattr(order_generator, "fetch_rsi", lambda ticker: 40.0)
    monkeypatch.setattr(order_generator, "fetch_vix", lambda: 25.0)
    monkeypatch.setattr(order_generator, "fetch_exchange_rate", lambda: 1545.0)
    monkeypatch.setattr(order_generator, "classify_market", lambda qqq, rsi, vix: ("bear", 5))
    monkeypatch.setattr(order_generator, "calculate_dca", lambda market_type, phase_key, fx, drawdown_pct=None: {
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


def test_order_passes_drawdown_to_dca(monkeypatch):
    """/order 주문서가 낙폭 정지 가드용 drawdown_pct 를 calculate_dca 로 전달하는지 (감사 확정 회귀).

    drawdown_pct 를 넘기지 않으면 leverage_dca_guard 의 낙폭 정지(-55%)가 발동하지 않아
    극단 낙폭에서도 5× 배율 주문서가 나간다.
    """
    seen = {}
    monkeypatch.setattr(order_generator, "fetch_qqq_data", lambda: {"drawdown_pct": -60.0})
    monkeypatch.setattr(order_generator, "fetch_rsi", lambda ticker: 40.0)
    monkeypatch.setattr(order_generator, "fetch_vix", lambda: 25.0)
    monkeypatch.setattr(order_generator, "fetch_exchange_rate", lambda: 1545.0)
    monkeypatch.setattr(order_generator, "classify_market", lambda qqq, rsi, vix: ("bear", 5))

    def _spy(market_type, phase_key, fx, drawdown_pct=None):
        seen["dd"] = drawdown_pct
        return {"total_krw": 100000, "by_ticker": {"MSFT": 100000}}
    monkeypatch.setattr(order_generator, "calculate_dca", _spy)
    monkeypatch.setattr(order_generator, "fetch_prices", lambda tickers: {"MSFT": 400.0})

    order_generator.generate()
    assert seen["dd"] == -60.0   # None 이면 낙폭 정지 우회(버그) → 실패
