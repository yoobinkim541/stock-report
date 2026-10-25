"""providers/llm_related — 호출 계약 회귀 (리뷰 D-04/D-06/D-07/D-11, 무네트워크).

파서·기본 캐시 동작은 tests/test_dashboard.py 의 test_llm_related_* 가 검증한다.
"""
import functools
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import llm_direct  # noqa: E402
from providers import llm_related as lr  # noqa: E402

_PAYLOAD = '[{"ticker": "AMD", "relation": "경쟁사", "reason": "GPU"}]'


class _Res:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    for name in ("DASH_LLM_RELATED_PROVIDER", "DASH_LLM_RELATED_MODEL",
                 "DASH_LLM_RELATED_MAX_TOKENS", "DASH_LLM_RELATED_RETRY_MAX_TOKENS",
                 "INVESTMENT_REPORT_LLM_PROVIDER", "INVESTMENT_REPORT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_direct_api_receives_related_cap_and_reduced_402_retry_cap(isolated, monkeypatch):
    """D-06: 연관종목 직접 API 요청 body 의 max_tokens 가 1024 → 402 재시도 384 여야 한다."""
    monkeypatch.setenv("DASH_LLM_RELATED_PROVIDER", "openrouter")
    monkeypatch.setenv("DASH_LLM_RELATED_MODEL", "test/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_DIRECT_MAX_TOKENS", "4096")
    bodies = []

    class Resp:
        def __init__(self, status, data=None):
            self.status_code, self._data = status, data or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._data

    def post(url, json=None, timeout=None, headers=None):
        bodies.append(json)
        if len(bodies) == 1:
            return Resp(402)
        return Resp(200, {"choices": [{"message": {"content": _PAYLOAD}}]})

    runner = functools.partial(llm_direct.run, post=post,
                               fallback=lambda cmd, *a, **kw: _Res("", 1, "Error code: 402"))
    items, status = lr.related_tickers("NVDA", name="NVIDIA", runner=runner, force=True)
    assert status == "ok" and items[0]["ticker"] == "AMD"
    assert [b["max_tokens"] for b in bodies] == [1024, 384]


def test_402_retry_uses_only_the_remaining_budget(isolated, monkeypatch):
    """D-07: 402 재시도가 timeout 전체를 다시 받지 않는다."""
    clock = {"t": 500.0}
    monkeypatch.setattr(lr.time, "monotonic", lambda: clock["t"])
    timeouts = []

    def runner(cmd, **kw):
        timeouts.append(kw["timeout"])
        if len(timeouts) == 1:
            clock["t"] += 45
            return _Res("", 1, "Error code: 402")
        return _Res(_PAYLOAD)

    _items, status = lr.related_tickers("NVDA", runner=runner, force=True)
    assert status == "ok" and timeouts == [60, 15]


def test_status_does_not_echo_the_timed_out_command(isolated):
    """D-04: 실패 상태 문자열에 TimeoutExpired 명령 인자(프롬프트)를 넣지 않는다."""
    def timeout_runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    _items, status = lr.related_tickers("NVDA", name="NVIDIA", runner=timeout_runner, force=True)
    assert status == "fallback (call failed: TimeoutExpired)"


def test_concurrent_cache_miss_calls_the_model_once(isolated):
    """D-11: 같은 종목 동시 미스는 1번만 생성하고 나머지는 그 캐시를 재사용한다."""
    calls = []
    guard = threading.Lock()

    def runner(cmd, **kw):
        with guard:
            calls.append(cmd)
        time.sleep(0.4)
        return _Res(_PAYLOAD)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: lr.related_tickers("NVDA", runner=runner), range(4)))
    assert len(calls) == 1
    assert sorted(status for _, status in results) == ["cached", "cached", "cached", "ok"]
    assert all(items == json.loads(_PAYLOAD) for items, _ in results)
