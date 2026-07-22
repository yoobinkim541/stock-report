from pathlib import Path


def test_candidate_ledger_dedupes_candidates_and_outcomes(tmp_path):
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    rec = {
        "id": "2026-07-22:KR:005930:101500:vwap_reclaim",
        "date": "2026-07-22",
        "market": "KR",
        "ticker": "005930",
        "setup_type": "vwap_reclaim",
        "sample_mode": "observe_only",
        "entry_price": 73000.0,
        "bar_ts": "2026-07-22T10:15:00+09:00",
    }

    assert ledger.log_candidate(rec) == rec["id"]
    assert ledger.log_candidate(dict(rec)) == rec["id"]
    assert len(ledger.read_candidates()) == 1

    outcome = {
        "candidate_id": rec["id"],
        "horizon_min": 15,
        "entry_price": 73000.0,
        "exit_price": 73800.0,
        "gross_return": 0.010959,
        "net_return_est": 0.0067,
        "mfe": 0.014,
        "mae": -0.004,
        "success": True,
    }
    ledger.log_outcome(outcome)
    ledger.log_outcome(dict(outcome))

    assert len(ledger.read_outcomes()) == 1
    assert ledger.pending((5, 15)) == [(rec, 5)]
