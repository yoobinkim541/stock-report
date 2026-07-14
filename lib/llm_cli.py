"""lib/llm_cli.py — LLM CLI 공용 러너: hermes 1차 + Antigravity CLI(agy) 백업.

hermes(openai-codex 경유)가 죽으면(미설치·인증 만료·프로바이더 장애) 챗봇 /ask·리포트
overlay·속보 2차판정·뉴스 라벨이 전부 무LLM 폴백으로 강등된다 → **백업 체인**으로 가용성 확보:

  1차: hermes chat (기존 그대로 — 각 호출부가 자기 cmd/설정 유지)
  2차: agy --print '<prompt>' (Google Antigravity CLI — 비대화식·플레인텍스트 stdout)

안전:
  - 백업은 `LLM_BACKUP_ENABLED=true` opt-in (기본 off — 기존 동작 완전 불변).
  - agy 는 에이전트라 작업 디렉토리의 파일 도구를 쓸 수 있음 → **빈 스크래치 cwd 에서 실행**
    (레포 접근 차단). 파일 편집이 필요한 advisor 는 백업 모드에서 답변 전용으로 강등(정직 표기).
  - 출력은 각 호출부의 기존 검증(fact guard·형식 파서·enum 검증)을 그대로 통과해야 채택.

확인: `uv run python -m lib.llm_cli --check` — hermes/agy 설치·버전·백업 게이트 상태 출력.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

BACKUP_CLI = os.getenv("LLM_BACKUP_CLI", "agy")


def backup_enabled() -> bool:
    return os.getenv("LLM_BACKUP_ENABLED", "false").lower() == "true"


def _scratch_dir() -> str:
    """agy 실행용 빈 격리 디렉토리 — 레포 파일 도구 접근 차단 (에이전트 CLI 안전핀)."""
    d = os.path.join(tempfile.gettempdir(), "llm-backup-scratch")
    os.makedirs(d, exist_ok=True)
    return d


def backup_chat(prompt: str, *, timeout: int = 120, runner=None) -> tuple[str | None, str]:
    """agy --print 백업 호출 — (텍스트|None, 상태노트). 게이트 off/실패 → (None, 사유).

    반환 텍스트는 호출부의 기존 출력 검증(guard/파서)을 반드시 통과해야 채택된다.
    """
    if not backup_enabled():
        return None, "backup off"
    run = runner or subprocess.run
    cmd = [BACKUP_CLI, "--print", prompt, "--print-timeout", f"{max(30, int(timeout))}s"]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout + 30,
                     cwd=_scratch_dir())
    except FileNotFoundError:
        return None, f"backup {BACKUP_CLI} 미설치"
    except Exception as e:
        return None, f"backup 호출 실패: {str(e)[:120]}"
    if getattr(result, "returncode", 1) != 0:
        return None, f"backup 비정상 종료: {str(getattr(result, 'stderr', ''))[:120]}"
    text = (getattr(result, "stdout", "") or "").strip()
    if not text:
        return None, "backup 빈 출력"
    return text, f"backup:{BACKUP_CLI}"


def status() -> dict:
    """hermes/백업 CLI 설치·버전 상태 (진단용 — --check)."""
    out = {"backup_enabled": backup_enabled(), "backup_cli": BACKUP_CLI}
    for name, cmd in (("hermes", ["hermes", "--version"]),
                      (BACKUP_CLI, [BACKUP_CLI, "--version"])):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out[name] = (r.stdout or r.stderr or "").strip().splitlines()[0][:80] \
                if r.returncode == 0 else f"오류(rc={r.returncode})"
        except FileNotFoundError:
            out[name] = "미설치"
        except Exception as e:
            out[name] = f"확인 실패: {str(e)[:60]}"
    return out


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        st = status()
        print("LLM CLI 상태")
        print(f"  hermes        : {st.get('hermes')}")
        print(f"  {st['backup_cli']:14s}: {st.get(st['backup_cli'])}")
        print(f"  백업 게이트    : {'ON' if st['backup_enabled'] else 'OFF (LLM_BACKUP_ENABLED=true 로 활성)'}")
        sys.exit(0)
    print(__doc__)
