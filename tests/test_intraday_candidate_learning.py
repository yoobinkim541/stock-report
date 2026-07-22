def test_candidate_setup_summary_groups_by_setup_and_mode(tmp_path):
    from ml.intraday_candidate_ledger import CandidateLedger
    from crons.intraday_mock_learn import candidate_setup_summary

    ledger = CandidateLedger("kr", base_dir=tmp_path)
    ledger.log_candidate({"id": "c1", "setup_type": "vwap_reclaim", "sample_mode": "observe_only"})
    ledger.log_candidate({"id": "c2", "setup_type": "vwap_reclaim", "sample_mode": "micro"})
    ledger.log_candidate({"id": "c3", "setup_type": "volume_shock", "sample_mode": "normal"})
    ledger.log_outcome({"candidate_id": "c1", "horizon_min": 15, "net_return_est": 0.01, "success": True})
    ledger.log_outcome({"candidate_id": "c2", "horizon_min": 15, "net_return_est": -0.02, "success": False})

    summary = candidate_setup_summary("kr", base_dir=tmp_path)

    assert summary["vwap_reclaim"]["n_candidates"] == 2
    assert summary["vwap_reclaim"]["n_outcomes_15m"] == 2
    assert summary["vwap_reclaim"]["avg_net_return_15m"] == -0.005
    assert summary["vwap_reclaim"]["modes"] == {"observe_only": 1, "micro": 1}
    assert summary["volume_shock"]["n_candidates"] == 1
