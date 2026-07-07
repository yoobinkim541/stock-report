#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM-assisted portfolio decision layer.

This module keeps the deterministic report engine authoritative, then adds a
context-aware portfolio action and an optional one-shot LLM review.  The LLM
output is schema-checked and defaults to shadow mode so it cannot silently turn
into a trading engine.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

RISK_LEVELS = {"낮음", "주의", "높음"}
PORTFOLIO_ACTIONS = {
    "분할매수 검토",
    "유지",
    "추가매수 금지",
    "비중점검",
    "일부축소",
    "매도검토",
}


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _fmt_pct(value) -> str:
    number = _to_float(value)
    return "N/A" if number is None else f"{number:+.1f}%"


def _compact(text, limit=120) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def load_holding_context(path: str) -> dict:
    """Aggregate overseas general + fractional holdings by ticker."""
    try:
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
    except Exception:
        return {"total_usd": 0.0, "positions": {}}

    positions = {}
    for section, key in (("overseas_general", "holdings_usd"), ("overseas_fractional", "holdings")):
        for holding in snap.get(section, {}).get(key, []) or []:
            ticker = str(holding.get("ticker") or "").upper()
            if not ticker:
                continue
            pos = positions.setdefault(
                ticker,
                {"ticker": ticker, "shares": 0.0, "value_usd": 0.0, "cost_usd": 0.0, "name": ""},
            )
            pos["shares"] += _to_float(holding.get("shares"), 0.0) or 0.0
            pos["value_usd"] += _to_float(holding.get("value_usd"), 0.0) or 0.0
            pos["cost_usd"] += _to_float(holding.get("cost_usd"), 0.0) or 0.0
            if not pos["name"]:
                pos["name"] = holding.get("name") or ""

    total = sum(p["value_usd"] for p in positions.values())
    for pos in positions.values():
        cost = pos.get("cost_usd") or 0.0
        shares = pos.get("shares") or 0.0
        value = pos.get("value_usd") or 0.0
        pos["weight_pct"] = (value / total * 100.0) if total else 0.0
        pos["return_pct"] = ((value / cost - 1.0) * 100.0) if cost else None
        pos["avg_price_usd"] = (cost / shares) if shares else None
    return {"total_usd": total, "positions": positions}


def slim_earnings_context(summary: dict | None) -> dict:
    summary = summary or {}
    nxt = summary.get("next_earnings", {}) or {}
    cons = summary.get("consensus", {}) or {}
    val = summary.get("valuation", {}) or {}
    return {
        "next_earnings_date": nxt.get("date"),
        "days_until": nxt.get("days_until"),
        "revision_momentum": cons.get("revision_momentum"),
        "target_upside_pct": cons.get("target_upside_pct"),
        "forward_pe": val.get("forward_pe"),
        "per": val.get("per"),
        "roe": val.get("roe"),
        "div_yield": val.get("div_yield"),
    }


def infer_role(ticker: str, fund: dict | None = None) -> str:
    base = (ticker or "").upper().split(".")[0]
    notes = " ".join((fund or {}).get("notes", []) or []).lower()
    if base in {"SGOV", "BIL", "SHV", "USFR"}:
        return "현금성"
    if base in {"QQQI", "JEPQ", "QYLD"}:
        return "인컴"
    if base in {"SPMO", "MTUM", "QMOM"}:
        return "모멘텀"
    if base in {"UNH", "JNJ", "MRK", "PFE", "ABBV", "ABT", "CVS", "CI", "HUM"}:
        return "방어주/헬스케어"
    if base in {"MSFT", "GOOGL", "GOOG", "NVDA", "AAPL", "META", "AMZN", "ORCL", "SAP"}:
        return "성장/퀄리티"
    if "etf" in notes or "etn" in notes:
        return "ETF"
    return "일반"


def _risk_from_rule(rule: dict) -> str:
    status = ((rule or {}).get("risk") or {}).get("status")
    if status == "높음":
        return "높음"
    if status == "주의":
        return "주의"
    return "낮음"


def _action_from_rule(action: str) -> str:
    if action in ("강한 매수후보", "관심/분할매수"):
        return "분할매수 검토"
    if action in ("관심 유지", "보유", "현금성 유지", "인컴 유지", "모멘텀 유지"):
        return "유지"
    if action in ("추격 금지", "눌림 대기"):
        return "추가매수 금지"
    if action in ("비중축소 검토", "모멘텀 주의"):
        return "비중점검"
    if action in ("매도검토", "손절/매도검토", "데이터부족"):
        return "매도검토"
    return "비중점검"


def _raise_risk(current: str, candidate: str) -> str:
    order = {"낮음": 0, "주의": 1, "높음": 2}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def build_context_decision(result: dict, holding: dict | None = None,
                           earnings: dict | None = None, market: dict | None = None) -> dict:
    """Build deterministic context-aware action from rule output + portfolio context."""
    ticker = result.get("ticker", "")
    fund = result.get("fundamental", {}) or {}
    signal = result.get("signal", {}) or {}
    rule = result.get("decision_v2", {}) or {}
    holding = holding or {}
    earnings = earnings or {}
    price_info = signal.get("price_info", {}) or {}

    action = rule.get("action", "데이터부족")
    role = infer_role(ticker, fund)
    risk_level = _risk_from_rule(rule)
    portfolio_action = _action_from_rule(action)
    execution_plan = {
        "분할매수 검토": "소량 분할매수만 검토",
        "유지": "현 비중 유지",
        "추가매수 금지": "추격매수 금지, 눌림 후 재평가",
        "비중점검": "비중과 다음 이벤트 확인 후 일부축소 여부 결정",
        "일부축소": "일부 이익실현 후 코어 보유",
        "매도검토": "부분 매도 또는 손절 조건 확인",
    }[portfolio_action]

    weight = _to_float(holding.get("weight_pct"))
    ret = _to_float(holding.get("return_pct"))
    d1 = _to_float(price_info.get("1d_change_pct"))
    m1 = _to_float(price_info.get("1mo_change_pct"))
    days_until = _to_float(earnings.get("days_until"))
    revision = _to_float(earnings.get("revision_momentum"))
    target_upside = _to_float(earnings.get("target_upside_pct"))
    score = _to_float(fund.get("total_score"))
    grade = fund.get("grade", "N/A")

    reasons = []
    if score is not None:
        reasons.append(f"재무 {score:.0f}점({grade})")
    if role != "일반":
        reasons.append(f"{role} 역할")
    if weight is not None:
        reasons.append(f"포트 비중 {_fmt_pct(weight)}")
    if ret is not None:
        reasons.append(f"평가손익 {_fmt_pct(ret)}")
    if m1 is not None:
        reasons.append(f"1개월 {_fmt_pct(m1)}")
    if days_until is not None and days_until >= 0:
        reasons.append(f"실적 D-{int(days_until)}")
    if revision is not None:
        reasons.append(f"리비전 {revision:+.2f}")
    if target_upside is not None:
        reasons.append(f"목표가 괴리 {_fmt_pct(target_upside)}")

    do_not_do = []
    earnings_soon = days_until is not None and 0 <= days_until <= 14
    meaningful_gain = ret is not None and ret >= 15
    high_weight = weight is not None and weight >= 15
    recent_run = m1 is not None and m1 >= 10

    if role == "방어주/헬스케어" and action == "비중축소 검토" and risk_level == "낮음":
        portfolio_action = "비중점검"
        execution_plan = "방어주 코어는 유지하고 비중·실적 리스크만 점검"

    if high_weight and earnings_soon and (meaningful_gain or recent_run):
        portfolio_action = "일부축소"
        risk_level = _raise_risk(risk_level, "주의")
        execution_plan = "실적 전 20~30% 일부축소, 나머지 보유"
        do_not_do.extend(["전량매도", "실적 전 추가매수"])
    elif earnings_soon and recent_run and portfolio_action in ("분할매수 검토", "유지"):
        portfolio_action = "추가매수 금지"
        execution_plan = "실적 확인 전 추가매수 보류"
        do_not_do.append("실적 전 추가매수")

    if target_upside is not None and target_upside < 0:
        risk_level = _raise_risk(risk_level, "주의")
    if revision is not None and revision < 0 and earnings_soon:
        risk_level = _raise_risk(risk_level, "주의")

    triggers = []
    if earnings_soon:
        triggers.append("다음 실적에서 가이던스 유지/상향 여부")
    if revision is not None and revision < 0:
        triggers.append("리비전 모멘텀 회복 여부")
    if target_upside is not None and target_upside < 0:
        triggers.append("목표가 괴리 개선 여부")
    for risk in result.get("risks", []) or []:
        triggers.append(str(risk))
    triggers = list(dict.fromkeys(_compact(t, 80) for t in triggers if t))[:4]

    confidence = _to_float(rule.get("confidence"), 50) or 50
    if weight is not None:
        confidence += 4
    if earnings_soon:
        confidence += 2
    if portfolio_action == "일부축소":
        confidence += 3
    confidence = int(max(0, min(100, round(confidence))))

    return {
        "ticker": ticker,
        "risk_level": risk_level,
        "portfolio_action": portfolio_action,
        "execution_plan": execution_plan,
        "reasoning_summary": reasons[:6],
        "do_not_do": list(dict.fromkeys(do_not_do))[:3],
        "recheck_triggers": triggers,
        "confidence": confidence,
        "role": role,
        "source": "rule_context",
        "rule_action": action,
    }


def _llm_enabled() -> bool:
    return _env_bool("INVESTMENT_REPORT_LLM_DECISION_ENABLED", "0")


def _llm_mode() -> str:
    mode = os.getenv("INVESTMENT_REPORT_LLM_DECISION_MODE", "shadow").lower()
    return mode if mode in ("shadow", "apply") else "shadow"


def build_llm_decision_payload(items: list[dict], market: dict | None = None) -> dict:
    compact_items = []
    for item in items:
        compact_items.append({
            "ticker": item.get("ticker"),
            "company": item.get("company"),
            "rule_decision": item.get("rule_decision"),
            "context_decision": item.get("context_decision"),
            "holding": item.get("holding"),
            "earnings": item.get("earnings"),
            "price": item.get("price"),
            "top_reasons": item.get("top_reasons", [])[:3],
            "top_risks": item.get("top_risks", [])[:3],
        })
    return {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "market": market or {},
        "portfolio": compact_items,
        "allowed_risk_levels": sorted(RISK_LEVELS),
        "allowed_portfolio_actions": sorted(PORTFOLIO_ACTIONS),
    }


def build_llm_decision_prompt(payload: dict) -> str:
    return (
        "한국어 포트폴리오 의사결정 검토자. 입력 JSON 안의 숫자와 티커만 사용한다.\n"
        "기존 rule_decision은 1차 필터이고 context_decision은 포트 비중/실적/수익률 보정값이다.\n"
        "너는 context_decision을 검토해 더 정확한 portfolio_action과 실행계획을 JSON으로만 반환한다.\n"
        "전량매도는 risk_level 높음 또는 명확한 매도검토 조건이 있을 때만 허용한다.\n"
        "실적 14일 이내, 비중 15% 이상, 수익/단기반등이 큰 종목은 전량매도보다 일부축소/추가매수 금지를 우선 검토한다.\n"
        "출력은 반드시 다음 형식의 JSON object 하나만 사용한다:\n"
        "{\"decisions\":[{\"ticker\":\"UNH\",\"risk_level\":\"주의\",\"portfolio_action\":\"일부축소\","
        "\"execution_plan\":\"실적 전 20~30% 일부축소, 나머지 보유\","
        "\"reasoning_summary\":[\"포트 비중 +16.5%\"],\"do_not_do\":[\"전량매도\"],"
        "\"recheck_triggers\":[\"가이던스 유지 여부\"],\"confidence\":72}]}\n\n"
        "입력 JSON:\n"
        "<<<DATA_START>>>\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        "<<<DATA_END>>>"
    )


def _extract_json_object(text: str):
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object found")
    return json.loads(text[start:end + 1])


def validate_llm_decisions(raw, allowed_tickers: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("LLM output must be a JSON object")
    decisions = raw.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    cleaned = {}
    for item in decisions:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper()
        if ticker not in allowed_tickers:
            raise ValueError(f"unknown ticker: {ticker}")
        risk_level = item.get("risk_level")
        portfolio_action = item.get("portfolio_action")
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"invalid risk_level for {ticker}: {risk_level}")
        if portfolio_action not in PORTFOLIO_ACTIONS:
            raise ValueError(f"invalid portfolio_action for {ticker}: {portfolio_action}")
        confidence = int(max(0, min(100, _to_float(item.get("confidence"), 50) or 50)))
        cleaned[ticker] = {
            "ticker": ticker,
            "risk_level": risk_level,
            "portfolio_action": portfolio_action,
            "execution_plan": _compact(item.get("execution_plan"), 120),
            "reasoning_summary": [_compact(x, 80) for x in (item.get("reasoning_summary") or [])[:4]],
            "do_not_do": [_compact(x, 50) for x in (item.get("do_not_do") or [])[:3]],
            "recheck_triggers": [_compact(x, 80) for x in (item.get("recheck_triggers") or [])[:4]],
            "confidence": confidence,
            "source": "llm",
        }
    return cleaned


def run_llm_portfolio_decisions(items: list[dict], market: dict | None = None,
                                runner=subprocess.run) -> tuple[dict, str]:
    if not _llm_enabled():
        return {}, "disabled"
    if not items:
        return {}, "no items"

    payload = build_llm_decision_payload(items, market)
    prompt = build_llm_decision_prompt(payload)
    cmd = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "--provider",
        os.getenv("INVESTMENT_REPORT_LLM_DECISION_PROVIDER", os.getenv("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex")),
        "--model",
        os.getenv("INVESTMENT_REPORT_LLM_DECISION_MODEL", os.getenv("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini")),
        "-Q",
    ]
    timeout = _env_int("INVESTMENT_REPORT_LLM_DECISION_TIMEOUT", 90, 20)
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=timeout, cwd=os.path.dirname(os.path.abspath(__file__)))
    except Exception as exc:
        return {}, f"call failed: {_compact(exc)}"
    if getattr(result, "returncode", 1) != 0:
        return {}, f"call failed: {_compact(getattr(result, 'stderr', 'non-zero exit'))}"
    try:
        raw = _extract_json_object(getattr(result, "stdout", "") or "")
        allowed = {str(item.get("ticker") or "").upper() for item in items}
        return validate_llm_decisions(raw, allowed), "ok"
    except Exception as exc:
        return {}, f"guard rejected: {_compact(exc)}"


def merge_llm_decision(context_decision: dict, llm_decision: dict | None, mode: str | None = None) -> dict:
    mode = mode or _llm_mode()
    merged = dict(context_decision or {})
    if not llm_decision:
        return merged
    merged["llm_shadow"] = dict(llm_decision)
    merged["llm_mode"] = mode
    if mode != "apply":
        return merged
    if merged.get("risk_level") == "높음" and llm_decision.get("risk_level") != "높음":
        return merged
    for key in ("risk_level", "portfolio_action", "execution_plan", "reasoning_summary",
                "do_not_do", "recheck_triggers", "confidence"):
        if key in llm_decision:
            merged[key] = llm_decision[key]
    merged["source"] = "llm_apply"
    return merged
