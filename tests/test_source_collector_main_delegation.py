"""tests/test_source_collector_main_delegation.py — 스크립트 이중 로드로 전역이 갈리던 문제.

감사(2026-08-22): 헬스체크가 "polymarket 61h 공백" 을 반복 경보했는데, 실제로는
HTTP 451(법적 지역 차단)이라 복구 불가능한 상태였고 이를 blocked 로 표시하는 코드도
이미 있었다. 그런데도 경보가 계속 뜬 이유는 **같은 파일이 두 개의 모듈 객체로 로드**됐기 때문:

    크론: `python reports/source_collector.py`  → 모듈명 `__main__`
    내부: `reports/source_pipeline.py` 가 `reports.source_collector` 를 import

두 사본은 전역을 공유하지 않는다. 그래서 `__main__` 사본의 fetch_polymarket_events 가
`_SOURCE_AVAILABILITY["polymarket"] = {"availability": "blocked", ...}` 를 채워도,
집계 코드는 `reports.source_collector._SOURCE_AVAILABILITY`(다른 dict)를 읽어 비어 있었다.
→ availability=available 로 기록 → stale_sources() 의 blocked 스킵이 무력화 → 영구 오경보.

실측(수정 전 로그):
    WARNING polymarket 수집 실패: HTTP 451 ...
    INFO 수집 polymarket: fetched=0 persisted=0 availability=available   ← 451 인데 available
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_script_run_delegates_to_package_main():
    """스크립트로 실행하면 **패키지 사본의 main** 이 호출돼야 한다(전역 일원화).

    실제 수집을 돌리지 않도록 패키지 사본의 main 을 센티넬로 갈아끼우고 확인한다.
    """
    code = (
        "import runpy, sys; sys.path.insert(0, %r);\n"
        "import reports.source_collector as pkg;\n"
        "pkg.main = lambda *a, **k: (print('PKG_MAIN_CALLED'), 0)[1];\n"
        "try:\n"
        "    runpy.run_path(%r, run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
    ) % (str(ROOT), str(ROOT / "reports" / "source_collector.py"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=180, cwd=str(ROOT))
    assert r.returncode == 0, f"실행 실패: {r.stderr[-500:]}"
    assert "PKG_MAIN_CALLED" in r.stdout, (
        "스크립트가 자기 사본의 main 을 직접 부르면 전역이 갈린다 — 패키지 위임 필요: "
        f"{r.stdout[-300:]}"
    )


def test_main_guard_delegates_to_package_module():
    """__main__ 가드가 패키지 사본으로 위임하는 구조를 유지해야 한다(회귀 방어)."""
    src = (ROOT / "reports" / "source_collector.py").read_text(encoding="utf-8")
    tail = src[src.rindex('if __name__ == "__main__":'):]
    assert "reports.source_collector" in tail, (
        "__main__ 에서 패키지 사본으로 위임하지 않으면 전역이 갈린다"
    )


def test_blocked_availability_reaches_health_stats():
    """451 로 blocked 가 된 소스는 집계 stats 에 blocked 로 실려야 한다."""
    import tempfile

    from reports import source_collector as sc
    from reports.source_pipeline import ProviderSpec, run_providers

    def _blocked_fetch(*a, **k):
        sc._SOURCE_AVAILABILITY["dummysrc"] = {
            "availability": "blocked",
            "availability_reason": "HTTP 451: test",
        }
        return []

    sc._SOURCE_AVAILABILITY.pop("dummysrc", None)
    spec = ProviderSpec("dummysrc", ("dummysrc",), "prediction", _blocked_fetch, mutable=True)
    try:
        with tempfile.TemporaryDirectory() as d:
            res = run_providers(registry=[spec], cache_dir=d)
        assert res["providers"]["dummysrc"]["availability"] == "blocked"
    finally:
        sc._SOURCE_AVAILABILITY.pop("dummysrc", None)


def test_stale_sources_skips_blocked():
    """blocked 소스는 공백 경보 대상에서 제외돼야 한다(경보 스팸 방지)."""
    from reports.source_collector import stale_sources

    health = {
        "polymarket": {"last_success": "2026-08-20T11:05:52+09:00", "availability": "blocked"},
        "othersrc": {"last_success": "2026-08-20T11:05:52+09:00"},
    }
    bad = {s["source"] for s in stale_sources(health)}
    assert "polymarket" not in bad
    assert "othersrc" in bad          # 대조군 — 정상 소스는 그대로 잡힌다
