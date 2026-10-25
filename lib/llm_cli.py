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

공용 호출 계약 (모든 LLM CLI/폴백 경로가 공유):
  - `run_cli`: subprocess.run 호환. CLI 를 새 세션(프로세스 그룹)에서 띄우고 timeout·중단 시
    그룹 전체를 TERM → grace → KILL 로 정리한다(MCP·브라우저 등 손자 프로세스 잔류 방지).
    timeout 예외에는 실행 파일 이름만 담는다 — 프롬프트(명령 인자)가 로그로 새지 않게.
  - timeout 은 진입점의 단일 예산이다. 폴백은 남은 시간만 쓰며 CLI 내부 여유를 위해 늘리지 않는다.
  - 실패 상태 문자열·로그에는 예외 종류/종료코드만 남기고 원시 stderr·응답 본문은 남기지 않는다.
  - `cache_fill_lock`·`write_cache_json`: 24h LLM 디스크 캐시의 키별 프로세스 간 single-flight 와
    고유 임시 파일 atomic write.

확인: `uv run python -m lib.llm_cli --check` — hermes/agy 설치·버전·백업 게이트 상태 출력.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_CLI = os.getenv("LLM_BACKUP_CLI", "agy")

# TERM 후 KILL 까지의 유예 — TERM 을 무시하는 후손도 이 시간 뒤엔 강제 종료된다.
PROCESS_GROUP_GRACE = 3.0


def backup_enabled() -> bool:
    return os.getenv("LLM_BACKUP_ENABLED", "false").lower() == "true"


def terminate_process_group(proc, grace: float | None = None) -> None:
    """proc 의 프로세스 그룹 전체(CLI 가 띄운 MCP 자식 포함)에 SIGTERM, grace 초 뒤 SIGKILL."""
    killpg = getattr(os, "killpg", None)

    def _signal(sig):
        try:
            if killpg is None:
                proc.kill()
            else:
                killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    _signal(signal.SIGTERM)
    timer = threading.Timer(PROCESS_GROUP_GRACE if grace is None else grace, _signal,
                            args=(getattr(signal, "SIGKILL", signal.SIGTERM),))
    timer.daemon = True
    timer.start()


def _close_pipes(proc) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()


def run_cli(cmd, *, timeout=None, check: bool = False, capture_output: bool = False,
            input=None, on_spawn=None, **kwargs):
    """subprocess.run 호환 CLI 실행 — 프로세스 그룹 단위 수명관리.

    on_spawn(proc) 은 Popen 직후 호출된다(race 의 loser 취소처럼 외부에서 그룹을 끊어야 할 때).
    """
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    try:
        if on_spawn is not None:
            on_spawn(proc)
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc)
        try:
            # KILL 이 나간 뒤 파이프가 닫힐 때까지만 기다린다(그룹 밖으로 도망간 후손이 파이프를
            # 쥐고 있어도 무한 대기하지 않도록 상한을 둔다).
            proc.communicate(timeout=PROCESS_GROUP_GRACE + 2)
        except subprocess.TimeoutExpired:
            _close_pipes(proc)
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
        name = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else "cli"
        raise subprocess.TimeoutExpired([str(name)], timeout) from None
    except BaseException:
        terminate_process_group(proc)
        _close_pipes(proc)
        raise
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _scratch_dir() -> str:
    """agy 실행용 빈 격리 디렉토리 — 레포 파일 도구 접근 차단 (에이전트 CLI 안전핀)."""
    d = os.path.join(tempfile.gettempdir(), "llm-backup-scratch")
    os.makedirs(d, exist_ok=True)
    return d


def backup_chat(prompt: str, *, timeout: float = 120, runner=None) -> tuple[str | None, str]:
    """agy --print 백업 호출 — (텍스트|None, 상태노트). 게이트 off/실패 → (None, 사유).

    timeout 은 호출부가 남긴 예산 그대로다(최소값으로 늘리지 않는다).
    반환 텍스트는 호출부의 기존 출력 검증(guard/파서)을 반드시 통과해야 채택된다.
    """
    if not backup_enabled():
        return None, "backup off"
    if timeout is None or timeout < 1:
        return None, "backup 예산 없음"
    run = runner or run_cli
    cmd = [BACKUP_CLI, "--print", prompt, "--print-timeout", f"{max(1, int(timeout))}s"]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_scratch_dir())
    except FileNotFoundError:
        return None, f"backup {BACKUP_CLI} 미설치"
    except Exception as e:
        return None, f"backup 호출 실패: {type(e).__name__}"
    if getattr(result, "returncode", 1) != 0:
        return None, f"backup 비정상 종료: rc={getattr(result, 'returncode', 1)}"
    text = (getattr(result, "stdout", "") or "").strip()
    if not text:
        return None, "backup 빈 출력"
    return text, f"backup:{BACKUP_CLI}"


def chat_once(prompt: str, *, model: str = "gpt-5-mini", provider: str = "openai-codex",
              timeout: int = 90, runner=None) -> tuple[str | None, str]:
    """전체 체인 1회 호출 — hermes 1차 → (게이트 시) agy 백업. (텍스트|None, 상태).

    새 코드용 공용 진입점 (기존 4개 호출부는 자체 hermes cmd 유지 — 회귀 방지).
    hermes 와 백업은 timeout 하나를 나눠 쓴다.
    """
    from lib import llm_direct
    run = runner or llm_direct.run   # openrouter면 API 직접 호출
    started = time.monotonic()
    deadline = started + timeout
    try:
        result = run(["hermes", "chat", "-q", prompt, "--provider", provider,
                      "--model", model, "-Q"],
                     capture_output=True, text=True, timeout=timeout)
        if getattr(result, "returncode", 1) == 0:
            text = (getattr(result, "stdout", "") or "").strip()
            if text:
                return text, "hermes"
    except Exception as e:
        logger.info("hermes 호출 실패: provider=%s error=%s elapsed=%.1fs",
                    provider, type(e).__name__, time.monotonic() - started)
    return backup_chat(prompt, timeout=deadline - time.monotonic(), runner=runner)


DEFAULT_STRUCTURED_PROVIDERS = ("codex", "openrouter", "hermes")
STRUCTURED_PROVIDERS = frozenset({"codex", "openrouter", "hermes", "agy"})
_AGY_DEFAULT_MODELS = "gemini-3.8-flash-high,gemini-3.8-flash-medium,gemini-4-argon-low"
_AGY_COOLDOWN: dict[str, float] = {}
_AGY_QUOTA_COOLDOWN_SECONDS = 600


def _agy_quota_error(text: str) -> bool:
    return "RESOURCE_EXHAUSTED" in text or "quota reached" in text.lower()


def agy_structured_call(prompt: str, *, timeout: float, runner=None) -> str | None:
    """Server-local Antigravity CLI, trying each model in turn.

    Quota is per model, so an exhausted model is skipped (and remembered for a
    while) instead of failing the whole stage. Source text is untrusted, so tool
    permissions are never auto-approved: the CLI runs sandboxed in an empty
    scratch directory and a tool request simply yields empty output.
    """
    run = runner or run_cli
    binary = os.getenv("WIKI_AGY_BIN") or shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    scratch = os.getenv("WIKI_AGY_SCRATCH") or os.path.join(tempfile.gettempdir(), "llm-agy-scratch")
    os.makedirs(scratch, exist_ok=True)
    deadline = time.monotonic() + timeout
    for model in [m.strip() for m in os.getenv("WIKI_AGY_MODELS", _AGY_DEFAULT_MODELS).split(",") if m.strip()]:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            return None
        if _AGY_COOLDOWN.get(model, 0.0) > time.monotonic():
            continue
        cmd = [binary, "--model", model, "--sandbox", "--disable-slash-commands",
               "--print-timeout", f"{max(1, int(remaining))}s", "-p", prompt]
        try:
            result = run(cmd, capture_output=True, text=True, timeout=remaining, cwd=scratch)
        except (OSError, subprocess.SubprocessError):
            continue
        output = (getattr(result, "stdout", "") or "").strip()
        failure = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
        if getattr(result, "returncode", 1) != 0 and _agy_quota_error(failure):
            _AGY_COOLDOWN[model] = time.monotonic() + _AGY_QUOTA_COOLDOWN_SECONDS
            continue
        if getattr(result, "returncode", 1) == 0 and output:
            return output
    return None


def structured_providers() -> list[str] | None:
    """WIKI_STRUCTURED_PROVIDERS → 검증된 순서 목록. 알 수 없는 이름이 하나라도 있으면 None.

    대소문자·공백은 정규화하고 중복은 첫 위치만 남긴다. 오타를 Hermes 로 조용히 보내지 않도록
    구성 오류는 fail-closed(어떤 제공자도 호출하지 않음)다.
    """
    raw = os.getenv("WIKI_STRUCTURED_PROVIDERS", ",".join(DEFAULT_STRUCTURED_PROVIDERS))
    names: list[str] = []
    for item in raw.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if name not in STRUCTURED_PROVIDERS:
            logger.warning("WIKI_STRUCTURED_PROVIDERS 구성 오류: 알 수 없는 제공자 %r — 호출하지 않음",
                           name[:40])
            return None
        if name not in names:
            names.append(name)
    return names


def structured_chat(prompt: str, *, max_timeout: int = 180) -> str | None:
    """Bounded Codex → direct OpenRouter → Hermes JSON-object fallback.

    No parallel calls or web search; agy is an opt-in stage via WIKI_STRUCTURED_PROVIDERS. Each provider gets at most 60
    seconds within a shared, hard-capped 180-second budget. Codex's existing
    unsupported-model retry shares that same provider deadline. Domain schemas
    and source attribution remain the caller's responsibility; invalid JSON is
    never salvaged. Provider errors and source bodies are deliberately not logged.
    Unknown provider names are a configuration error: nothing is called.
    """
    from agent_console import agent
    from lib import llm_direct
    from lib.structured_output import parse_object

    if os.getenv("AGENT_CONSOLE_LLM_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return None
    providers = structured_providers()
    if not providers:
        return None
    deadline = time.monotonic() + max(0, min(max_timeout, int(os.getenv("WIKI_STRUCTURED_DEADLINE", "180"))))
    for provider in providers:
        remaining = min(int(os.getenv("WIKI_PROVIDER_TIMEOUT", "60")), deadline - time.monotonic())
        if remaining < 1:
            return None
        stage_deadline = time.monotonic() + remaining
        try:
            if provider == "agy":
                text = agy_structured_call(prompt, timeout=remaining)
            elif provider == "openrouter":
                if not llm_direct.enabled():
                    continue
                text = llm_direct.direct_chat(
                    prompt,
                    os.getenv("WIKI_STRUCTURED_OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
                    timeout=remaining, structured_output=True, max_tokens=8192,
                )
            else:
                call = agent._try_codex_chat if provider == "codex" else agent._try_hermes_chat
                text = call(prompt, max_timeout=remaining, deadline=stage_deadline,
                            search=False, reasoning_effort="medium", structured_output=True)
            if time.monotonic() <= deadline and isinstance(text, str) and parse_object(text) is not None:
                return text
        except Exception:
            # Continue once to the next provider; never print private payloads.
            continue
    return None


@contextlib.contextmanager
def cache_fill_lock(cache_path, *, wait: float):
    """캐시 키별 프로세스 간 single-flight — `<cache>.lock` 에 flock.

    같은 키를 동시에 채우려는 요청(스레드·워커·크론)은 한 번에 하나만 LLM 을 호출하고, 나머지는
    잠금을 얻은 뒤 신선도를 재확인해 그 결과를 재사용한다. 다른 키는 다른 잠금 파일이라 서로
    직렬화되지 않는다. wait 초 안에 잠금을 못 얻으면(보유자 정지) False 를 내주고 호출부가
    잠금 없이 진행한다 — 교착보다 중복 호출 1회가 낫다. fcntl 이 없는 OS 에서도 False 다.
    """
    try:
        import fcntl
    except ImportError:
        yield False
        return
    lock_path = Path(str(cache_path) + ".lock")
    fd = None
    acquired = False
    try:
        from lib import file_cache

        file_cache.harden_dir(lock_path.parent)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + max(0.0, float(wait))
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    logger.warning("LLM 캐시 잠금 대기 초과: %s — 잠금 없이 진행", lock_path.name)
                    break
                time.sleep(0.05)
    except OSError as exc:
        logger.warning("LLM 캐시 잠금 실패: %s (%s)", lock_path.name, type(exc).__name__)
    try:
        yield acquired
    finally:
        if fd is not None:
            if acquired:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def write_cache_json(path, obj) -> bool:
    """디렉터리 하드닝 + 고유 임시 파일 → os.replace. 실패는 비밀 없는 경고로 남기고 False."""
    from lib import file_cache

    path = Path(path)
    tmp = None
    try:
        file_cache.harden_dir(path.parent)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning("LLM 캐시 저장 실패: %s (%s)", path.name, type(exc).__name__)
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return False


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
