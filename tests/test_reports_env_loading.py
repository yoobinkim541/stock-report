"""tests/test_reports_env_loading.py — `-m reports.*` 크론이 .env 를 읽는지 (무네트워크).

감사(2026-08-23): wiki_distillation.log 가 수 주째 "STOCK_BOT_TOKEN 없음 — 텔레그램
발송 skip" 을 반복 기록 중이었다. 원인은 `crons/*.py` 는 관례적으로

    from dotenv import load_dotenv
    load_dotenv()

를 모듈 최상단에서 호출하는데, 크론탭이 `-m` 으로 직접 띄우는 `reports/*.py` 계열은
이 호출이 없다는 것 — `uv run` 은 `.env` 를 프로세스 환경에 자동 주입하지 않으므로
`os.getenv("STOCK_BOT_TOKEN")` 이 항상 None 이 되어 알림이 조용히 스킵된다.

실측(수정 전): `uv run python -c "import reports.institution_watch; import os;
print(os.getenv('STOCK_BOT_TOKEN'))"` → None (반면 .env 파일에는 실제로 설정돼 있음).

이 갭은 institution_watch(방금 이식한 신규편입 알림)에도 그대로 적용돼, 배포와
동시에 알림이 무음이 될 뻔했다.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = [
    "reports.institution_watch",     # STOCK_BOT_TOKEN — 신규편입 알림
    "reports.wiki_distillation",     # STOCK_BOT_TOKEN — 증류 카드 알림
    "reports.source_pipeline",       # STOCK_COLLECTOR_ARCA_PAGES 등 오버라이드
    "reports.source_wiki_curator",   # SOURCE_WIKI_LLM_ENABLED 등 오버라이드
]


def _source_calls_load_dotenv(mod_dotted: str) -> bool:
    rel = mod_dotted.replace(".", "/") + ".py"
    src = (ROOT / rel).read_text(encoding="utf-8")
    return "load_dotenv(" in src


def test_env_dependent_report_modules_call_load_dotenv():
    """crons/*.py 관례와 동일하게, .env 값을 읽는 reports/*.py 도 직접 로드해야 한다."""
    missing = [m for m in MODULES if not _source_calls_load_dotenv(m)]
    assert not missing, (
        f"{missing} — .env 를 읽는데 load_dotenv() 호출이 없음. "
        "uv run 은 .env 를 자동 주입하지 않아 os.getenv 가 조용히 None 이 된다."
    )


def test_importing_institution_watch_makes_env_file_values_visible(tmp_path, monkeypatch):
    """실제 프로세스 재현 — 모듈 임포트만으로 .env 의 값이 os.environ 에 들어와야 한다."""
    env_file = tmp_path / ".env"
    env_file.write_text("STOCK_BOT_TOKEN=probe-token-xyz\n", encoding="utf-8")

    code = (
        "import sys, os; sys.path.insert(0, %r); os.chdir(%r);\n"
        "import reports.institution_watch\n"
        "print(os.getenv('STOCK_BOT_TOKEN'))\n"
    ) % (str(ROOT), str(tmp_path))
    # ⚠️ 다른 테스트가 이미 같은 pytest 프로세스에서 진짜 프로젝트 .env 를 로드해뒀을 수
    # 있다 — subprocess.run 이 그 os.environ 을 그대로 상속하면 load_dotenv 의 "기존 값
    # 덮어쓰지 않음" 기본 동작 때문에 여기 넣은 probe 값이 안 먹힌다. 자식 env 를 명시적으로
    # 비워서(STOCK_BOT_TOKEN 제거) 격리한다.
    child_env = {k: v for k, v in os.environ.items() if k != "STOCK_BOT_TOKEN"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=60, cwd=str(tmp_path), env=child_env)
    assert r.returncode == 0, f"실행 실패: {r.stderr[-500:]}"
    assert "probe-token-xyz" in r.stdout, (
        f".env 값이 os.environ 에 안 실림 — load_dotenv 누락. stdout={r.stdout!r}"
    )


def test_importing_wiki_distillation_makes_env_file_values_visible(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("STOCK_BOT_TOKEN=probe-token-abc\n", encoding="utf-8")

    code = (
        "import sys, os; sys.path.insert(0, %r); os.chdir(%r);\n"
        "import reports.wiki_distillation\n"
        "print(os.getenv('STOCK_BOT_TOKEN'))\n"
    ) % (str(ROOT), str(tmp_path))
    child_env = {k: v for k, v in os.environ.items() if k != "STOCK_BOT_TOKEN"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=60, cwd=str(tmp_path), env=child_env)
    assert r.returncode == 0, f"실행 실패: {r.stderr[-500:]}"
    assert "probe-token-abc" in r.stdout
