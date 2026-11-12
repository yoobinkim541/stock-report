"""하단 마퀴는 토스 코인 4종을 기존 yfinance 배치에 싣는다. 종목당 호출이 아니다."""
from __future__ import annotations

import pandas as pd

from dashboard import views


def test_tape_lists_toss_crypto_in_one_download(monkeypatch):
    seen = {}

    def download(symbols, **kwargs):
        seen["symbols"] = list(symbols)
        seen["calls"] = seen.get("calls", 0) + 1
        cols = pd.MultiIndex.from_product([list(symbols), ["Close"]])
        return pd.DataFrame(columns=cols)

    import yfinance as yf
    monkeypatch.setattr(yf, "download", download)

    assert views._market_tape_live() == []
    assert seen["calls"] == 1
    assert {"BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD"} <= set(seen["symbols"])
    assert len(seen["symbols"]) == len(views._TAPE_SYMS)
