"""lib/llm_direct.py — `hermes chat --provider openrouter` 배치 호출을 OpenRouter API 직접 호출로 대신 실행.

배경(2026-10-09 실측): 뉴스 라벨·속보 판정·대시보드 해설 같은 한 번짜리 JSON 작업도 `hermes chat -Q`로
부르면 Hermes 에이전트 전체(시스템 프롬프트·도구 목록 약 2만 토큰 + 기본 reasoning_effort xhigh)가 함께
실려, 한 줄 답에 호출당 $0.001 안팎이 든다. 에이전트 기능(도구·메모리)이 필요 없는 작업이므로 모델 API를
바로 부르면 입력은 프롬프트 크기, 출력은 답 크기로 줄어든다.

동작:
  - 호출부는 기존 hermes 명령(list)을 그대로 만든다. 이 모듈의 `run`을 기본 runner로 쓰면
    `hermes chat -q PROMPT --provider openrouter --model M` 형태만 직접 호출로 바꾸고, 나머지 명령은
    `subprocess.run`에 그대로 넘긴다(openai-codex 등 기존 경로 불변).
  - 직접 호출이 실패하면(키 없음·HTTP 오류·빈 응답) 원래 hermes 명령을 실행해 Hermes의 대체 체인에 맡긴다.
  - 반환값은 subprocess.CompletedProcess 이므로 호출부의 returncode/stdout 처리 코드를 바꿀 필요가 없다.

설정:
  LLM_DIRECT_OPENROUTER   기본 true. false 면 항상 hermes 명령 실행(이전 동작).
  LLM_DIRECT_MAX_TOKENS   기본 4096. 출력 상한.
  OPENROUTER_API_KEY      필수(없으면 hermes 명령으로 실행).
"""
from __future__ import annotations

import logging
import os
import subprocess

import requests

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def enabled() -> bool:
    return os.getenv("LLM_DIRECT_OPENROUTER", "true").lower() != "false"


def _arg(cmd: list[str], *names: str) -> str | None:
    for name in names:
        if name in cmd:
            i = cmd.index(name)
            if i + 1 < len(cmd):
                return cmd[i + 1]
    return None


def parse_openrouter_chat(cmd) -> tuple[str, str] | None:
    """직접 호출로 바꿀 수 있는 hermes 명령이면 (prompt, model), 아니면 None."""
    if not isinstance(cmd, (list, tuple)) or len(cmd) < 3:
        return None
    cmd = [str(c) for c in cmd]
    if os.path.basename(cmd[0]) != "hermes" or cmd[1] != "chat":
        return None
    if _arg(cmd, "--provider") != "openrouter" or "--image" in cmd:
        return None
    prompt, model = _arg(cmd, "-q", "--query"), _arg(cmd, "--model", "-m")
    if not prompt or not model:
        return None
    return prompt, model


def direct_chat(prompt: str, model: str, *, timeout: float | None = None, post=None) -> str:
    """OpenRouter chat completions 1회. 성공 시 응답 텍스트, 실패 시 예외."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY 없음")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.getenv("LLM_DIRECT_MAX_TOKENS", "4096")),
        # 배치 작업은 한 번짜리 형식 출력이라 추론을 끈다(출력 토큰 비용의 대부분이 추론이었음)
        "reasoning": {"enabled": False},
    }
    resp = (post or requests.post)(
        OPENROUTER_URL, json=body, timeout=timeout or 120,
        headers={"Authorization": f"Bearer {key}", "X-Title": "stock-report"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:200])
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise RuntimeError("빈 응답")
    return text.strip()


def run(cmd, *args, post=None, fallback=None, **kwargs):
    """subprocess.run 호환 runner. openrouter hermes 명령만 직접 호출로 바꾼다."""
    fallback = fallback or subprocess.run
    parsed = parse_openrouter_chat(cmd) if enabled() else None
    if parsed is None:
        return fallback(cmd, *args, **kwargs)
    prompt, model = parsed
    try:
        text = direct_chat(prompt, model, timeout=kwargs.get("timeout"), post=post)
    except Exception as exc:  # noqa: BLE001 — 실패 시 기존 hermes 경로(대체 체인 포함)로
        logger.info("OpenRouter 직접 호출 실패 → hermes로 실행: %s", str(exc)[:160])
        return fallback(cmd, *args, **kwargs)
    return subprocess.CompletedProcess(cmd, 0, text + "\n", "")
