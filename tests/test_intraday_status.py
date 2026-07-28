"""Read-only intraday status summaries."""

import json


def test_intraday_summary_includes_latest_diagnostics(monkeypatch, tmp_path):
    from lib import intraday_status as status

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"counters": {"US": {"sleeve_pnl_cum": 0.0}}}), encoding="utf-8")
    diag_path = tmp_path / "us_intraday_diagnostics.jsonl"
    diag_path.write_text(
        json.dumps({
            "market": "US",
            "candidates": 3,
            "entries": 0,
            "skip_reasons": {"stale_data": 2, "confirm_wait": 1},
            "status": "ok",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(status, "_STATE_PATH", str(state_path))
    monkeypatch.setattr(status, "_DATA_DIR", str(tmp_path))

    summary = status.intraday_summary("US")
    lines = status.intraday_section("US")

    assert summary["diagnostics"]["top_skip"] == [("stale_data", 2), ("confirm_wait", 1)]
    assert any("진입진단 후보 3 · 진입 0" in line for line in lines)
    assert any("stale_data 2" in line for line in lines)
