#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM rationale helper for KR/US mock-trading reports.

The trading loops stay deterministic.  This helper only adds a short,
schema-checked explanation section to read-only mock reports.
"""
from __future__ import annotations

import json
import os
import subprocess

ALLOWED_KEYS = ("summary", "position_notes", "decision_notes", "risk_checks", "confidence")


def _env_bool(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _compact(text, limit=110) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _safe_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError):
        return None


def build_payload(*, market: str, nav=None, day_ret=None, cum_ret=None, benchmark_ret=None,
                  excess=None, strat_mdd=None, benchmark_mdd=None, cash=None,
                  positions=None, recent_decisions=None, scorecard=None,
                  trading_cost=None, turnover=None) -> dict:
    positions = positions or []
    recent_decisions = recent_decisions or []
    return {
        "market": market,
        "nav": nav,
        "day_ret_pct": day_ret,
        "cum_ret_pct": cum_ret,
        "benchmark_ret_pct": benchmark_ret,
        "excess_pctp": excess,
        "strategy_mdd_pct": strat_mdd,
        "benchmark_mdd_pct": benchmark_mdd,
        "cash": cash,
        "positions": positions[:8],
        "recent_decisions": recent_decisions[:8],
        "scorecard": scorecard or {},
        "trading_cost": trading_cost,
        "turnover_pct": turnover,
    }


def build_prompt(payload: dict) -> str:
    return (
        "한국어 모의투자 리포트 판단근거 작성자. 입력 JSON 안의 숫자·티커·종목명만 사용한다.\n"
        "주문 지시를 만들지 말고, 현황 보고서에 붙일 사후 설명만 작성한다.\n"
        "입력에 없는 원인/뉴스/전망/숫자/티커를 만들지 않는다. 모르면 확인 필요라고 쓴다.\n"
        "출력은 JSON object 하나만 반환한다. 키는 summary, position_notes, decision_notes, risk_checks, confidence만 허용한다.\n"
        "각 배열은 최대 2개, 각 문장은 짧게 쓴다.\n"
        "예시: {\"summary\":\"전략은 QQQ 대비 열위지만 MDD는 안정적입니다.\","
        "\"position_notes\":[\"NVDA 비중은 수익 기여 확인 필요\"],"
        "\"decision_notes\":[\"최근 편입은 정책점수 상위 근거\"],"
        "\"risk_checks\":[\"회전율 상승 시 비용 차감 성과 확인\"],\"confidence\":70}\n\n"
        "입력 JSON:\n"
        "<<<DATA_START>>>\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        "<<<DATA_END>>>"
    )


def _extract_json_object(text: str) -> dict:
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object found")
    return json.loads(text[start:end + 1])


def validate_output(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("output must be object")
    unknown = set(raw) - set(ALLOWED_KEYS)
    if unknown:
        raise ValueError("unknown keys: " + ", ".join(sorted(unknown)))
    confidence = int(max(0, min(100, _safe_float(raw.get("confidence")) or 50)))
    return {
        "summary": _compact(raw.get("summary"), 100),
        "position_notes": [_compact(x, 90) for x in (raw.get("position_notes") or [])[:2]],
        "decision_notes": [_compact(x, 90) for x in (raw.get("decision_notes") or [])[:2]],
        "risk_checks": [_compact(x, 90) for x in (raw.get("risk_checks") or [])[:2]],
        "confidence": confidence,
    }


def format_section(result: dict, *, html: bool = False) -> list[str]:
    title = "🧠 LLM 판단근거"
    lines = [title]
    if result.get("summary"):
        lines.append(f"- {result['summary']}")
    for key in ("position_notes", "decision_notes", "risk_checks"):
        for item in result.get(key, []) or []:
            lines.append(f"- {item}")
    lines.append(f"- 신뢰도 {result.get('confidence', 50)}/100")
    return lines


def run(payload: dict, *, runner=subprocess.run) -> tuple[dict | None, str]:
    if not _env_bool("MOCK_REPORT_LLM_ENABLED", "1"):
        return None, "disabled"
    prompt = build_prompt(payload)
    cmd = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "--provider",
        os.getenv("MOCK_REPORT_LLM_PROVIDER", os.getenv("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex")),
        "--model",
        os.getenv("MOCK_REPORT_LLM_MODEL", os.getenv("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini")),
        "-Q",
    ]
    timeout = _env_int("MOCK_REPORT_LLM_TIMEOUT", 75, 20)
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return None, f"call failed: {_compact(exc, 80)}"
    if getattr(result, "returncode", 1) != 0:
        return None, f"call failed: {_compact(getattr(result, 'stderr', 'non-zero exit'), 80)}"
    try:
        return validate_output(_extract_json_object(getattr(result, "stdout", "") or "")), "ok"
    except Exception as exc:
        return None, f"guard rejected: {_compact(exc, 80)}"
