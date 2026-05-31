import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import investment_report as ir


def test_fetch_arca_markdown_returns_none_after_retries_on_network_error():
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("network down")

    old_get = ir.requests.get
    old_sleep = ir.time.sleep
    try:
        ir.requests.get = fake_get
        ir.time.sleep = lambda _seconds: None
        result = ir._fetch_arca_markdown(page=1)
        assert result is None
        assert calls["count"] == 3
    finally:
        ir.requests.get = old_get
        ir.time.sleep = old_sleep
