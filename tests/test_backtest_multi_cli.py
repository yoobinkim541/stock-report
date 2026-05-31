import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import backtest_multi as bm


def test_main_accepts_custom_start_date():
    calls = {"download_all": None, "build_report": None, "send_telegram": 0}

    old_argv = sys.argv
    old_download_all = bm.download_all
    old_add_signals = bm.add_signals
    old_run_all = bm.run_all
    old_build_report = bm.build_report
    old_send_telegram = bm.send_telegram

    try:
        sys.argv = ["backtest_multi.py", "--start", "2024-01-01"]

        def fake_download_all(start):
            calls["download_all"] = start
            return pd.DataFrame(
                {"QQQ": [100.0, 101.0]},
                index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
            )

        def fake_add_signals(df):
            return df

        def fake_run_all(df):
            return {"strategy": [{"final": 123.0}]}

        def fake_build_report(period_logs):
            calls["build_report"] = list(period_logs.keys())
            return "report text"

        def fake_send_telegram(text):
            calls["send_telegram"] += 1

        bm.download_all = fake_download_all
        bm.add_signals = fake_add_signals
        bm.run_all = fake_run_all
        bm.build_report = fake_build_report
        bm.send_telegram = fake_send_telegram

        bm.main()

        assert calls["download_all"] == "2024-01-01"
        assert calls["build_report"] == ["사용자 지정 (2024-01-01~2026)"]
        assert calls["send_telegram"] == 0
    finally:
        sys.argv = old_argv
        bm.download_all = old_download_all
        bm.add_signals = old_add_signals
        bm.run_all = old_run_all
        bm.build_report = old_build_report
        bm.send_telegram = old_send_telegram
