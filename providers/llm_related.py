"""providers/llm_related.py — 🤖 LLM 연관 종목 추천 (대시보드 종목분석 · 표시 전용).

현 종목의 프로필(이름·섹터 힌트·매크로 여부)을 주고 "이 종목을 보는 투자자가 함께
검토할 만한 종목" 3~5개를 LLM 에게 받아온다. **환각 방어**: 응답 티커는
ticker_names.normalize_input 화이트리스트/형식 검증을 통과한 것만 채택(못 찾으면 폐기),
관계 유형은 enum 검증. LLM 은 아이디어 생성기일 뿐 — 출력엔 정직 라벨 필수
(검증 안 된 참고용·매매신호 아님 — 판단 반영 금지, CLAUDE.md LLM 원칙).

호출 경로는 프로젝트 표준(hermes chat · openai-codex)과 동일. 24h 디스크 캐시
(회당 비용·지연 통제 — 대시보드 버튼 게이트와 이중 방어). graceful: 실패 시 (None, 사유).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from lib import file_cache

CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache/llm_related"))
CACHE_TTL_H = 24.0
RELATIONS = ("경쟁사", "대체재", "공급망", "같은 테마", "헤지/역상관")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def build_prompt(ticker: str, name: str, context: str = "") -> str:
    """프롬프트 (순수). JSONL 강제 + 티커 형식 제약 + 관계 enum."""
    ctx = f"\n참고 컨텍스트: {context[:400]}" if context else ""
    return (
        f"당신은 주식 리서치 보조다. 종목 {name} ({ticker}) 을 보는 투자자가 비교 검토할 만한 "
        f"연관 종목 3~5개를 골라라.{ctx}\n"
        "규칙:\n"
        f"- relation 은 반드시 다음 중 하나: {', '.join(RELATIONS)}\n"
        "- ticker 는 미국 상장 티커(예: AMD, BRK-B) 또는 한국 6자리+.KS(예: 005930.KS)만.\n"
        "- 지수·선물·비상장·펀드클래스 금지. 자기 자신 금지. 확실치 않은 티커는 내지 마라.\n"
        "- reason 은 한국어 한 문장(40자 이내), 과장·수익 약속 금지.\n"
        "출력: 설명 없이 JSON 배열 한 개만. 원소 형식 "
        '{"ticker": "AMD", "relation": "경쟁사", "reason": "GPU 시장 직접 경쟁"}'
    )


def parse_related(text: str, self_ticker: str) -> list[dict]:
    """LLM 출력 → 검증된 추천 목록 (순수·환각 방어).

    - JSON 배열 추출(코드펜스/잡설 허용) → 원소별 검증
    - ticker: ticker_names.normalize_input 통과분만 (형식 밖·마크업 폐기)
    - relation: enum 밖이면 "연관" 으로 강등, reason 은 80자 절단
    - 자기 자신·중복 제거, 최대 5개
    """
    import ticker_names
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    self_norm = (self_ticker or "").upper()
    out: list[dict] = []
    seen: set[str] = set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        tk = ticker_names.normalize_input(str(it.get("ticker") or ""))
        if not tk or tk.upper() == self_norm or tk in seen:
            continue
        rel = str(it.get("relation") or "")
        out.append({
            "ticker": tk,
            "relation": rel if rel in RELATIONS else "연관",
            "reason": str(it.get("reason") or "")[:80],
        })
        seen.add(tk)
        if len(out) >= 5:
            break
    return out


def _cache_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())
    return CACHE_DIR / f"{safe}.json"


def related_tickers(ticker: str, name: str = "", context: str = "",
                    runner=subprocess.run, force: bool = False) -> tuple[list[dict] | None, str]:
    """연관 종목 추천 — (목록|None, 상태). 24h 디스크 캐시·graceful.

    상태: "ok" | "cached" | "disabled" | "call failed: …" | "empty".
    """
    if os.getenv("DASH_LLM_RELATED_ENABLED", "1").lower() in ("0", "false", "no"):
        return None, "disabled"
    cp = _cache_path(ticker)
    if not force and file_cache.is_fresh(cp, CACHE_TTL_H):
        cached = file_cache.read_json(cp)
        if isinstance(cached, list) and cached:
            return cached, "cached"
    prompt = build_prompt(ticker, name or ticker, context)
    cmd = ["hermes", "chat", "-q", prompt,
           "--provider", _env("DASH_LLM_RELATED_PROVIDER",
                              _env("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex")),
           "--model", _env("DASH_LLM_RELATED_MODEL",
                           _env("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini")),
           "-Q"]
    try:
        timeout = max(20, int(os.getenv("DASH_LLM_RELATED_TIMEOUT", "60")))
    except ValueError:
        timeout = 60
    try:
        res = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return None, f"call failed: {str(exc)[:80]}"
    if getattr(res, "returncode", 1) != 0:
        return None, f"call failed: {str(getattr(res, 'stderr', ''))[:80] or 'non-zero exit'}"
    items = parse_related(getattr(res, "stdout", "") or "", ticker)
    if not items:
        return None, "empty"
    try:
        file_cache.harden_dir(CACHE_DIR)
        _tmp = cp.with_suffix(".tmp")
        _tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.replace(_tmp, cp)
    except Exception:
        pass                                     # 캐시 실패해도 결과는 반환
    return items, "ok"
