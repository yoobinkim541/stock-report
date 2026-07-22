from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


def test_candidate_backfill_writes_forward_returns(tmp_path, monkeypatch):
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    cid = ledger.log_candidate({
        "id": "2026-07-22:KR:005930:600:vwap_reclaim",
        "date": "2026-07-22",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 100.0,
        "estimated_cost": 0.2,
        "bar_ts": "2026-07-22T09:30:00+09:00",
    })

    idx = pd.date_range(datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")), periods=20, freq="min")
    bars = pd.DataFrame({
        "Open": [100.0] * 20,
        "High": [101.0 + i * 0.1 for i in range(20)],
        "Low": [99.5] * 20,
        "Close": [100.0 + i * 0.2 for i in range(20)],
        "Volume": [1000] * 20,
    }, index=idx)

    import crons.intraday_candidate_backfill as backfill
    monkeypatch.setattr(backfill, "_load_bars", lambda ticker, market, date=None: bars)

    assert backfill.run_market("kr", base_dir=tmp_path, horizons=(5, 15)) == 2
    assert backfill.run_market("kr", base_dir=tmp_path, horizons=(5, 15)) == 0

    rows = ledger.read_outcomes()
    assert {(r["candidate_id"], r["horizon_min"]) for r in rows} == {(cid, 5), (cid, 15)}
    assert rows[0]["gross_return"] > 0
    assert rows[0]["net_return_est"] < rows[0]["gross_return"]


def test_candidate_backfill_loads_bars_by_candidate_date(tmp_path, monkeypatch):
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    ledger.log_candidate({
        "id": "2026-07-21:KR:005930:600:vwap_reclaim",
        "date": "2026-07-21",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 100.0,
        "estimated_cost": 0.1,
        "bar_ts": "2026-07-21T09:30:00+09:00",
    })
    ledger.log_candidate({
        "id": "2026-07-22:KR:005930:600:vwap_reclaim",
        "date": "2026-07-22",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 100.0,
        "estimated_cost": 0.1,
        "bar_ts": "2026-07-22T09:30:00+09:00",
    })

    def bars_for(day, close_step):
        idx = pd.date_range(datetime.fromisoformat(day + "T09:30:00+09:00"), periods=10, freq="min")
        return pd.DataFrame({
            "Open": [100.0] * 10,
            "High": [101.0] * 10,
            "Low": [99.0] * 10,
            "Close": [100.0 + i * close_step for i in range(10)],
            "Volume": [1000] * 10,
        }, index=idx)

    loaded = []
    by_date = {
        "2026-07-21": bars_for("2026-07-21", 0.1),
        "2026-07-22": bars_for("2026-07-22", 0.3),
    }

    import crons.intraday_candidate_backfill as backfill

    def fake_load(ticker, market, date=None):
        loaded.append((ticker, market, date))
        return by_date[date]

    monkeypatch.setattr(backfill, "_load_bars", fake_load)

    assert backfill.run_market("kr", base_dir=tmp_path, horizons=(5,)) == 2

    rows = sorted(ledger.read_outcomes(), key=lambda r: r["candidate_id"])
    assert {date for _, _, date in loaded} == {"2026-07-21", "2026-07-22"}
    assert rows[0]["exit_price"] == 100.5
    assert rows[1]["exit_price"] == 101.5
