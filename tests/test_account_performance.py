"""거래내역 기준 계좌 궤적 — 시간가중 수익률, 분할·배당, 미결제 체결, 시세 캐시, 트래커 요약."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from reports import account_performance, report_pdf_data

INDEX = pd.bdate_range("2026-03-02", periods=10)
DAYS = [ts.strftime("%Y%m%d") for ts in INDEX]


def _frame(raw: list[float], *, dividends: dict[int, float] | None = None, splits: dict[int, float] | None = None):
    frame = pd.DataFrame({"Close": raw, "Volume": 1000.0, "Raw": raw, "Dividends": 0.0, "Splits": 0.0}, index=INDEX)
    for column, events in (("Dividends", dividends), ("Splits", splits)):
        for position, value in (events or {}).items():
            frame.loc[INDEX[position], column] = value
    return frame


def _row(ticker: str, side: str, qty: float, price: float, settled: int) -> dict:
    """settled: 거래내역에 찍히는 날짜의 순번. 체결은 그보다 2거래일 앞이다."""
    return {"ticker": ticker, "side": side, "qty": qty, "price": price, "amount": qty * price, "date": DAYS[settled]}


def _prices(**frames) -> dict:
    return {"SPY": _frame([100.0] * 10), **frames}


def test_buying_and_selling_change_value_but_not_return():
    prices = _prices(A=_frame([100, 100, 100, 110, 110, 110, 121, 121, 121, 121]))
    ledger = [_row("A", "buy", 10, 100, 2), _row("A", "buy", 10, 110, 6), _row("A", "sell", 5, 121, 9)]
    account = account_performance.daily_account(ledger, prices)

    assert list(account["index"]) == list(INDEX)                         # 결제일 3/4 → 체결일 3/2
    assert account["value"] == pytest.approx([1000, 1000, 1000, 1100, 2200, 2200, 2420, 1815, 1815, 1815])
    assert account["net_invested"] == pytest.approx([1000] * 4 + [2100] * 3 + [1495] * 3)
    assert account["twr"] == pytest.approx([100, 100, 100, 110, 110, 110, 121, 121, 121, 121])
    assert account_performance.period_return(account["twr"], 4) == pytest.approx(10.0)
    assert account_performance.period_return(account["twr"], 10) is None


def test_trade_price_away_from_close_counts_as_that_days_gain():
    prices = _prices(A=_frame([100.0] * 10))
    account = account_performance.daily_account([_row("A", "buy", 10, 100, 2), _row("A", "buy", 10, 80, 6)], prices)

    # 80 에 산 10주가 종가 100 이 됐다: 손익 200 ÷ (전일 평가액 1000 + 그날 넣은 800)
    assert account["twr"][4] == pytest.approx(100 * (1 + 200 / 1800))


def test_later_split_converts_old_quantities_and_dividend_adds_net_income():
    # 3번째 날 2:1 분할. 시세는 이미 분할을 반영한 값이라 거래 수량만 지금 단위로 바꾸면 평가액이 끊기지 않는다.
    prices = _prices(B=_frame([50.0] * 10, splits={3: 2.0}, dividends={6: 1.0}))
    ledger = [_row("B", "buy", 10, 100, 2), _row("B", "buy", 4, 50, 7)]        # 뒤 매수는 분할 뒤 체결
    account = account_performance.daily_account(ledger, prices)

    assert account["value"] == pytest.approx([1000] * 5 + [1200] * 5)
    assert account["income"][-1] == pytest.approx(24 * 1.0 * (1 - account_performance.DIVIDEND_WITHHOLDING))
    assert account["twr"][-1] == pytest.approx(100 * (1 + 20.4 / 1200))


def test_unsettled_trades_follow_current_holdings_without_moving_return():
    prices = _prices(A=_frame([100, 100, 100, 110, 110, 110, 110, 110, 110, 120]))
    ledger = [_row("A", "buy", 10, 100, 2)]
    settled = account_performance.daily_account(ledger, prices)
    live = account_performance.daily_account(ledger, prices, holdings={"A": 15.0})

    assert live["value"][-1] == pytest.approx(15 * 120) and settled["value"][-1] == pytest.approx(10 * 120)
    assert live["net_invested"][-1] == pytest.approx(1000 + 5 * 120)
    assert live["twr"] == pytest.approx(settled["twr"])


def test_tickers_without_prices_are_left_out_and_named():
    prices = _prices(A=_frame([100.0] * 9 + [110.0]))
    ledger = [_row("A", "buy", 10, 100, 2), _row("GONE", "buy", 3, 40, 2)]
    account = account_performance.daily_account(ledger, prices)

    assert account["missing"] == ["GONE"]
    assert account["value"][0] == pytest.approx(1000) and account["net_invested"][0] == pytest.approx(1000)
    assert account_performance.daily_account(ledger, {"A": prices["A"]}) is None      # 거래일 달력이 없다
    assert account_performance.daily_account([], prices) is None


def test_trailing_summary_looks_back_by_calendar_days():
    prices = _prices(A=_frame([100, 100, 100, 110, 110, 110, 121, 121, 121, 108.9]))
    account = account_performance.daily_account([_row("A", "buy", 10, 100, 2)], prices)
    summary = account_performance.trailing_summary(account, days=(1, 7, 90))

    assert summary["ret_1d"] == pytest.approx(-10.0)
    assert summary["ret_7d"] == pytest.approx((108.9 / 110 - 1) * 100)     # 3/13(금)에서 7일 전 = 3/6(금)
    assert summary["ret_90d"] is None
    assert summary["ret_all"] == pytest.approx(8.9) and summary["first_date"] == "2026-03-02"
    assert summary["drawdown_from_peak"] == pytest.approx(-10.0) and summary["peak_date"] == "2026-03-10"


def test_tracker_returns_come_from_the_account_not_from_history_totals():
    import portfolio_tracker

    prices = _prices(A=_frame([100, 100, 100, 110, 110, 110, 121, 121, 121, 108.9]))
    account = account_performance.daily_account([_row("A", "buy", 10, 100, 2)], prices)
    # 히스토리 총액은 매수로 두 배가 됐다 — 수익률로 읽으면 +100% 다.
    history = [{"date": "2026-03-12", "total_usd": 500.0}, {"date": "2026-03-13", "total_usd": 1000.0}]

    assert portfolio_tracker.calc_performance(history, account)["ret_1d"] == pytest.approx(-10.0)
    empty = portfolio_tracker.calc_performance(history, None)
    assert empty["current"] == 1000.0 and empty["ret_1d"] is None and empty["ret_all"] is None


def test_price_fetch_retries_missing_symbols_then_fills_from_recent_cache(tmp_path):
    cache = tmp_path / "prices.json"
    full = {"SPY": _frame([100.0 + i for i in range(10)], dividends={4: 0.5}), "A": _frame([50.0] * 10, splits={3: 2.0})}
    calls = []

    def flaky(symbols, period):
        calls.append(list(symbols))
        return {s: full[s] for s in symbols if s == "SPY" or len(calls) > 1}

    def blocked(symbols, period):
        raise RuntimeError("rate limited")

    today = date(2026, 3, 16)
    first = report_pdf_data.fetch_price_history(["A", "SPY"], cache_path=cache, download=flaky, today=today, pause=0)
    assert calls == [["A", "SPY"], ["A"]] and set(first) == {"A", "SPY"}       # 빠진 심볼만 다시 받는다
    assert not first["A"].attrs.get("cached")

    later = report_pdf_data.fetch_price_history(["A", "SPY"], cache_path=cache, download=blocked,
                                                today=date(2026, 3, 18), pause=0)
    assert later["A"].attrs["cached"] == "2026-03-16"
    for symbol in full:
        pd.testing.assert_frame_equal(later[symbol], full[symbol], check_freq=False)

    model_inputs = {"report_date": "2026-03-16", "data": {"portfolio": []}, "investment_md": "", "market_md": ""}
    basis = report_pdf_data.build_model(**model_inputs, prices=later)["price_basis"]
    assert basis == {"asof": "3/13", "reused": 2, "fetched": "2026-03-16"}

    stale = report_pdf_data.fetch_price_history(["A", "SPY"], cache_path=cache, download=blocked,
                                                today=date(2026, 3, 30), pause=0)
    assert stale == {}                                                         # 7일 넘은 캐시는 쓰지 않는다
