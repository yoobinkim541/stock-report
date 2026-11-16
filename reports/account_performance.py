"""reports/account_performance.py — 브로커 거래내역으로 계좌의 일별 평가액·순투입·수익률을 다시 만든다.

portfolio_history 의 총액은 매수한 만큼 커지고 매도한 만큼 줄며, 스냅샷을 바로잡은 날은 그만큼
뛴다 — 수익률로 읽을 수 없다. 여기서는 키움 미국주식 거래내역(체결 수량·금액)과 일봉만 쓴다.

  평가액  그날까지의 보유 수량 × 종가(분할 반영, 배당 미반영)
  순투입  매수 금액 − 매도 금액
  손익    평가액 변화 − 순투입 + 배당(원천징수 후)
  수익률  하루 손익 ÷ (전일 평가액 + 당일 순매수) 를 이어 붙인 시간가중 수익률.
          돈을 넣고 빼거나 종목을 바꾸는 것만으로는 움직이지 않는다.

거래내역의 날짜는 결제 기준이라 체결일보다 2거래일 늦다(체결가가 그날 고가·저가 사이에 드는
비율로 확인: 2거래일 전 97%, 1·3거래일 전 82~83%). 수량은 체결 당시 단위라 이후 분할을 곱해
지금 단위로 맞춘다. 아직 결제되지 않은 최근 체결은 현재 보유 수량과의 차이로 마지막 날에 넣는다.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta

# crons/kiwoom_sync_rest.py::refresh_us_transaction_history 가 채우는 store 컬렉션.
TRANSACTIONS_COLLECTION = "kiwoom_us_transactions"
SETTLEMENT_LAG = 2
DIVIDEND_WITHHOLDING = 0.15
_SHARE_EPS = 1e-6
_MIN_CAPITAL = 1.0      # 이보다 작은 원금(USD)으로는 수익률을 내지 않는다 — 계좌 첫날의 0 나눗셈 방지


def load_transactions() -> list[dict]:
    import store

    return list(store.all(TRANSACTIONS_COLLECTION) or [])


def _signed(row: dict) -> float:
    return 1.0 if row.get("side") == "buy" else -1.0 if row.get("side") == "sell" else 0.0


def daily_account(transactions: list[dict], prices: dict, *, holdings: dict[str, float] | None = None,
                  calendar: str = "SPY") -> dict | None:
    """계좌의 일별 궤적. 계산할 수 없으면 None.

    prices 의 일봉은 Raw(분할만 반영한 종가)·Dividends·Splits 열을 가져야 한다. 가격이 없는 종목은
    평가액과 순투입 양쪽에서 빼고 missing 에 적는다. holdings(종목 → 현재 수량)가 있으면 마지막
    거래일의 수량을 거기에 맞춘다 — 차이는 그날 종가로 사고판 것으로 본다(수익률에 영향 없음).

    반환: index(거래일), value, net_invested(누적), twr(첫날 100), income(누적 배당), missing.
    """
    frame = prices.get(calendar)
    if frame is None or "Raw" not in getattr(frame, "columns", ()) or len(frame) < 2:
        return None
    index = frame.index
    sessions = [ts.strftime("%Y%m%d") for ts in index]
    count = len(sessions)

    tickers = sorted({str(r.get("ticker") or "") for r in transactions or []} | set(holdings or {}))
    closes: dict[str, list[float]] = {}
    payouts: dict[str, list[float]] = {}
    splits: dict[str, list[tuple[str, float]]] = {}
    missing = []
    for ticker in tickers:
        own = prices.get(ticker)
        if own is None or "Raw" not in getattr(own, "columns", ()) or own["Raw"].dropna().empty:
            missing.append(ticker)
            continue
        closes[ticker] = [float(v) for v in own["Raw"].reindex(index).ffill().bfill().tolist()]
        payouts[ticker] = [float(v) for v in own["Dividends"].reindex(index).fillna(0.0).tolist()]
        splits[ticker] = [(ts.strftime("%Y%m%d"), float(ratio)) for ts, ratio in own["Splits"].items() if ratio]

    opening: dict[str, float] = {}
    invested_before = 0.0
    changes: list[dict[str, float]] = [{} for _ in range(count)]
    flows = [0.0] * count
    for row in transactions or []:
        ticker, sign = str(row.get("ticker") or ""), _signed(row)
        quantity = float(row.get("qty") or 0.0)
        if ticker not in closes or not sign or quantity <= 0:
            continue
        position = bisect_right(sessions, str(row.get("date") or "")) - 1 - SETTLEMENT_LAG
        traded = sessions[position] if position >= 0 else str(row.get("date") or "")
        for split_day, ratio in splits[ticker]:
            if split_day > traded:
                quantity *= ratio
        amount = float(row.get("amount") or 0.0) or float(row.get("price") or 0.0) * float(row.get("qty") or 0.0)
        if position < 0:
            opening[ticker] = opening.get(ticker, 0.0) + sign * quantity
            invested_before += sign * amount
        else:
            changes[position][ticker] = changes[position].get(ticker, 0.0) + sign * quantity
            flows[position] += sign * amount

    shares = dict(opening)
    previous_value = sum(shares.get(t, 0.0) * closes[t][0] for t in closes)
    invested = invested_before
    income_total = 0.0
    level = 100.0
    value, net_invested, twr, income = [], [], [], []
    for day in range(count):
        dividends = sum(shares.get(t, 0.0) * payouts[t][day] for t in closes) * (1.0 - DIVIDEND_WITHHOLDING)
        for ticker, change in changes[day].items():
            shares[ticker] = shares.get(ticker, 0.0) + change
        flow = flows[day]
        current = sum(shares.get(t, 0.0) * closes[t][day] for t in closes)
        capital = previous_value + max(flow, 0.0)
        if day and capital >= _MIN_CAPITAL:
            level *= 1.0 + (current - previous_value - flow + dividends) / capital
        invested += flow
        if day == count - 1 and holdings is not None:      # 종가에 사고판 것으로 보므로 그날 수익률 밖이다
            for ticker in closes:
                gap = float(holdings.get(ticker) or 0.0) - shares.get(ticker, 0.0)
                if abs(gap) > _SHARE_EPS:
                    shares[ticker] = shares.get(ticker, 0.0) + gap
                    invested += gap * closes[ticker][day]
            current = sum(shares.get(t, 0.0) * closes[t][day] for t in closes)
        income_total += dividends
        value.append(current)
        net_invested.append(invested)
        twr.append(level)
        income.append(income_total)
        previous_value = current

    start = next((i for i, v in enumerate(value) if v >= _MIN_CAPITAL), None)
    if start is None or count - start < 2:
        return None
    base = twr[start]
    return {
        "index": index[start:], "value": value[start:], "net_invested": net_invested[start:],
        "twr": [v / base * 100.0 for v in twr[start:]], "income": income[start:], "missing": missing,
    }


def trailing_summary(account: dict, days: tuple[int, ...] = (1, 7, 30, 90)) -> dict:
    """마지막 거래일에서 달력일로 거슬러 잰 수익률(%)과 고점 대비 — 텍스트 리포트용."""
    index, twr = account["index"], account["twr"]
    stamps = [ts.strftime("%Y-%m-%d") for ts in index]
    out: dict = {}
    for span in days:
        cutoff = (index[-1] - timedelta(days=span)).strftime("%Y-%m-%d")
        position = bisect_right(stamps, cutoff) - 1
        out[f"ret_{span}d"] = (twr[-1] / twr[position] - 1.0) * 100.0 if position >= 0 else None
    peak = max(twr)
    out.update({
        "ret_all": twr[-1] - 100.0, "first_date": stamps[0],
        "drawdown_from_peak": (twr[-1] / peak - 1.0) * 100.0, "peak_date": stamps[twr.index(peak)],
        "net_invested": account["net_invested"][-1], "income": account["income"][-1],
    })
    return out


def period_return(series: list[float], sessions: int) -> float | None:
    """series 마지막 값의 sessions 거래일 전 대비 변화율(%)."""
    if len(series) <= sessions or not series[-1 - sessions]:
        return None
    return (series[-1] / series[-1 - sessions] - 1.0) * 100.0
