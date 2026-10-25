#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM rationale helper for KR/US mock-trading reports.

The trading loops stay deterministic.  This helper only adds a short,
schema-checked explanation section to read-only mock reports.

Numbers are never taken from LLM prose.  The payload is turned into a table of
facts (``fact_table``): each fact binds an entity (portfolio or one ticker/code),
a metric key, the signed value and its unit, e.g.
``positions.AAPL.weight_pct → "AAPL 비중 40.00%"``.  The LLM may cite a value only
as a reference ``{positions.AAPL.weight_pct}``; ``validate_output(raw, payload)``
replaces every reference with the payload value rendered deterministically as
``[AAPL 비중 40.00%]``, so a sign flip, a unit swap (NAV→%), another ticker's value
or a count reused as a metric cannot be published.

Publication rules (anything else is ``guard rejected`` and the report shows
``fallback_section``, stating that only the quantitative sections above are the basis):

- schema: every key in ``ALLOWED_KEYS`` present; ``summary`` a non-empty string;
  note fields lists of non-empty strings; ``confidence`` a number 0..100.
- references: every ``{ref}`` must be a key of ``fact_table(payload)``.
- no literal digits outside references and grounded tickers/6-digit KR codes.
- tickers: Latin upper-case tokens and 6-digit KR codes must appear in the
  payload strings (or be a generic metric/benchmark acronym).
- direction words next to a signed fact (상승/이익/우위… vs 하락/손실/열위…) must
  agree with the fact's sign.
- no assertive forecast words (확정·보장·반드시·무조건·틀림없…).
"""
from __future__ import annotations

import json
import os
import re

from lib import llm_cli

ALLOWED_KEYS = ("summary", "position_notes", "decision_notes", "risk_checks", "confidence")
NOTE_KEYS = ("position_notes", "decision_notes", "risk_checks")
# 티커처럼 보이지만 종목이 아닌 일반 지표/벤치마크 약어.
GENERIC_TOKENS = frozenset({
    "MDD", "NAV", "IC", "ETF", "LLM", "USD", "KRW", "QQQ", "KOSPI", "KOSDAQ", "PCT", "OOS", "AI",
    "KS", "KQ",
})
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?:\.[A-Z]{1,2})?(?![A-Za-z0-9])")
_KR_CODE_RE = re.compile(r"(?<![0-9])\d{6}(?![0-9])")
_REF_RE = re.compile(r"\{([^{}\s]{1,80})\}")
_DIGIT_RE = re.compile(r"\d+(?:[.,]\d+)*")
_ASSERTIVE_RE = re.compile(r"(?<!미)확정|보장|반드시|무조건|틀림없|확실히")
_UP_WORDS = ("상승", "이익", "플러스", "흑자", "우위", "개선", "증가", "호조", "초과 달성")
_DOWN_WORDS = ("하락", "손실", "마이너스", "적자", "열위", "악화", "감소", "부진")
_CLAUSE_BREAK = re.compile(r"[,.;·!?\n]|지만|반면|그러나")
_DIRECTION_WINDOW = 10

# payload 최상위 지표: key → (라벨, 형식)
_TOP_FACTS = {
    "nav": ("NAV", "money"),
    "cash": ("현금", "money"),
    "day_ret_pct": ("일간 수익률", "signed_pct"),
    "cum_ret_pct": ("누적 수익률", "signed_pct"),
    "benchmark_ret_pct": ("벤치마크 수익률", "signed_pct"),
    "excess_pctp": ("벤치마크 대비 초과수익", "signed_pctp"),
    "strategy_mdd_pct": ("전략 MDD", "pct"),
    "benchmark_mdd_pct": ("벤치마크 MDD", "pct"),
    "trading_cost": ("누적 거래비용", "money"),
    "turnover_pct": ("회전율", "pct"),
}
# positions / recent_decisions 항목 지표 (종목 단위)
_ITEM_FACTS = {
    "weight_pct": ("비중", "pct"),
    "return_pct": ("평가수익률", "signed_pct"),
    "value": ("평가금액", "money"),
    "policy_score": ("정책점수", "score"),
    "financial_score": ("재무점수", "score"),
    "price": ("신호 가격", "money"),
    "qty": ("주문 수량", "count"),
}
_SCORECARD_FACTS = {
    "buy_hit": ("편입 적중률", "pct"),
    "sell_hit": ("퇴출 적중률", "pct"),
    "n_buy": ("편입 평가 건수", "count"),
    "n_sell": ("퇴출 평가 건수", "count"),
    "ic": ("실현 IC", "signed_ratio"),
}
_LIST_GROUPS = ("positions", "recent_decisions")
_SIGNED_KINDS = frozenset({"signed_pct", "signed_pctp", "signed_ratio"})


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
    cut = cleaned[: limit - 1]
    # 렌더된 사실 값([라벨 값]) 중간에서 자르면 숫자가 잘려 다른 값처럼 보인다 → 괄호 앞에서 자른다.
    if cut.rfind("[") > cut.rfind("]"):
        cut = cut[: cut.rfind("[")]
    return cut.rstrip() + "…"


def _safe_float(value):
    if isinstance(value, bool):
        return None
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


def _format_value(value: float, kind: str, market: str) -> str:
    if kind == "money":
        if market == "KR":
            return f"₩{value:,.0f}"
        if market == "US":
            return f"${value:,.2f}"
        return f"{value:,.2f}"
    if kind == "signed_pct":
        return f"{value:+.2f}%"
    if kind == "signed_pctp":
        return f"{value:+.2f}%p"
    if kind == "pct":
        return f"{value:.2f}%"
    if kind == "signed_ratio":
        return f"{value:+.3f}"
    if kind == "count":
        return f"{value:,.0f}"
    return f"{value:.2f}"


def _entity(item: dict) -> tuple[str, str] | None:
    """목록 항목의 (참조 id, 표시명). 식별자가 없으면 None."""
    ident = str(item.get("ticker") or item.get("code") or item.get("symbol") or "").strip()
    if not ident or not re.fullmatch(r"[A-Za-z0-9._\-]{1,20}", ident):
        return None
    name = str(item.get("name") or "").strip()
    display = f"{name}({ident})" if name and name != ident else ident
    return ident, display


def fact_table(payload: dict) -> dict[str, dict]:
    """payload → {ref: {label, value, kind, text}}. 같은 ref 에 다른 값이 있으면(모호) 제외."""
    payload = payload or {}
    market = str(payload.get("market") or "").upper()
    facts: dict[str, dict] = {}
    ambiguous: set[str] = set()

    def add(ref: str, label: str, value, kind: str) -> None:
        number = _safe_float(value)
        if number is None:
            return
        if ref in facts and facts[ref]["value"] != number:
            ambiguous.add(ref)
            return
        facts[ref] = {"label": label, "value": number, "kind": kind,
                      "text": f"{label} {_format_value(number, kind, market)}"}

    for key, (label, kind) in _TOP_FACTS.items():
        add(key, label, payload.get(key), kind)
    scorecard = payload.get("scorecard") if isinstance(payload.get("scorecard"), dict) else {}
    for key, (label, kind) in _SCORECARD_FACTS.items():
        add(f"scorecard.{key}", label, scorecard.get(key), kind)
    for group in _LIST_GROUPS:
        for item in payload.get(group) or []:
            if not isinstance(item, dict):
                continue
            entity = _entity(item)
            if entity is None:
                continue
            ident, display = entity
            for key, (label, kind) in _ITEM_FACTS.items():
                add(f"{group}.{ident}.{key}", f"{display} {label}", item.get(key), kind)
    for ref in ambiguous:
        facts.pop(ref, None)
    return facts


def _context_view(value):
    """프롬프트 문맥용 payload — 숫자는 사실표 참조로만 쓰게 하려고 문자열 필드만 남긴다."""
    if isinstance(value, dict):
        out = {k: _context_view(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    if isinstance(value, (list, tuple)):
        out = [_context_view(v) for v in value]
        return [v for v in out if v not in (None, {}, [])]
    if isinstance(value, str):
        return value
    return None


def build_prompt(payload: dict) -> str:
    facts = {ref: fact["text"] for ref, fact in fact_table(payload).items()}
    return (
        "한국어 모의투자 리포트 판단근거 작성자. 주문 지시를 만들지 말고, 현황 보고서에 붙일 사후 설명만 작성한다.\n"
        "입력에 없는 원인/뉴스/전망/티커를 만들지 않는다. 모르면 확인 필요라고 쓴다.\n"
        "숫자는 절대 직접 쓰지 않는다. 수치가 필요하면 아래 FACTS 의 키를 중괄호로 그대로 인용한다"
        " (예: {cum_ret_pct}). 인용은 검증된 '[라벨 값]' 으로 자동 치환된다. "
        "FACTS 에 없는 키, 직접 쓴 숫자, 값의 부호와 반대되는 표현(음수인데 상승/이익 등)이 있으면 전체가 폐기된다.\n"
        "출력은 JSON object 하나만 반환한다. 키 summary, position_notes, decision_notes, risk_checks, confidence를 모두 넣고 다른 키는 쓰지 않는다.\n"
        "summary는 문자열, 세 notes는 문자열 배열(최대 2개), confidence는 0~100 숫자다.\n"
        "확정·보장·반드시 같은 단정 표현을 쓰지 않는다. 각 문장은 짧게 쓴다.\n"
        "예시: {\"summary\":\"전략은 {excess_pctp}로 QQQ 대비 열위지만 MDD는 안정적입니다.\","
        "\"position_notes\":[\"NVDA 는 {positions.NVDA.weight_pct}로 수익 기여 확인 필요\"],"
        "\"decision_notes\":[\"최근 편입은 정책점수 상위 근거\"],"
        "\"risk_checks\":[\"회전율 상승 시 비용 차감 성과 확인\"],\"confidence\":70}\n\n"
        "FACTS:\n"
        f"{json.dumps(facts, ensure_ascii=False)}\n\n"
        "문맥 JSON (숫자 제외):\n"
        "<<<DATA_START>>>\n"
        f"{json.dumps(_context_view(payload), ensure_ascii=False, default=str)}\n"
        "<<<DATA_END>>>"
    )


def _extract_json_object(text: str) -> dict:
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object found")
    return json.loads(text[start:end + 1])


def _payload_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _payload_strings(k) + _payload_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _payload_strings(v)]
    return []


def _grounding(payload: dict) -> tuple[set[str], set[str]]:
    strings = _payload_strings(payload)
    tickers = {t for s in strings for t in _TICKER_RE.findall(s.upper())}
    tickers |= {t.split(".", 1)[0] for t in tickers}
    codes = {c for s in strings for c in _KR_CODE_RE.findall(s)}
    return tickers, codes


def _direction_window(text: str, start: int, end: int) -> str:
    """사실 참조 앞뒤의 같은 절(구분자·다른 참조 전까지) 일부."""
    before = text[:start]
    after = text[end:]
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(before)]
    refs = [m.end() for m in _REF_RE.finditer(before)]
    before = before[max(breaks + refs + [0]):][-_DIRECTION_WINDOW:]
    stop = len(after)
    for pattern in (_CLAUSE_BREAK, _REF_RE):
        m = pattern.search(after)
        if m:
            stop = min(stop, m.start())
    return before + " " + after[:stop][:_DIRECTION_WINDOW]


def _check_and_render(text: str, field: str, facts: dict, grounding) -> str:
    tickers, codes = grounding
    if _ASSERTIVE_RE.search(text):
        raise ValueError(f"{field}: assertive forecast wording")
    for m in _REF_RE.finditer(text):
        ref = m.group(1)
        fact = facts.get(ref)
        if fact is None:
            raise ValueError(f"{field}: unknown fact reference {ref}")
        if fact["kind"] in _SIGNED_KINDS and fact["value"] != 0:
            window = _direction_window(text, m.start(), m.end())
            wrong = _UP_WORDS if fact["value"] < 0 else _DOWN_WORDS
            word = next((w for w in wrong if w in window), None)
            if word:
                raise ValueError(f"{field}: direction '{word}' contradicts {ref}={fact['value']:g}")
    plain = _REF_RE.sub(" ", text)
    if "{" in plain or "}" in plain:
        raise ValueError(f"{field}: malformed fact reference")
    for token in _TICKER_RE.findall(plain):
        base = token.split(".", 1)[0]
        if token not in tickers and base not in tickers and base not in GENERIC_TOKENS:
            raise ValueError(f"{field}: unknown ticker {token}")
    for code in _KR_CODE_RE.findall(plain):
        if code not in codes:
            raise ValueError(f"{field}: unknown code {code}")
    residual = _KR_CODE_RE.sub(" ", _TICKER_RE.sub(" ", plain))
    literal = _DIGIT_RE.search(residual)
    if literal:
        raise ValueError(f"{field}: literal number {literal.group(0)} (use a fact reference)")
    return _REF_RE.sub(lambda m: f"[{facts[m.group(1)]['text']}]", text)


def validate_output(raw: dict, payload: dict) -> dict:
    """LLM 출력을 스키마 + 사실표 참조 검증 후 결정적으로 렌더링한다. 하나라도 어긋나면 ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("output must be object")
    unknown = set(raw) - set(ALLOWED_KEYS)
    if unknown:
        raise ValueError("unknown keys: " + ", ".join(sorted(unknown)))
    missing = [k for k in ALLOWED_KEYS if k not in raw]
    if missing:
        raise ValueError("missing keys: " + ", ".join(missing))
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be non-empty string")
    for key in NOTE_KEYS:
        items = raw.get(key)
        if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
            raise ValueError(f"{key} must be list of non-empty strings")
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or _safe_float(confidence) is None or not 0 <= confidence <= 100:
        raise ValueError(f"confidence must be number 0..100: {confidence!r}")

    facts = fact_table(payload or {})
    grounding = _grounding(payload or {})
    rendered_summary = _check_and_render(summary, "summary", facts, grounding)
    rendered = {key: [_check_and_render(item, key, facts, grounding) for item in raw[key]]
                for key in NOTE_KEYS}
    return {
        "summary": _compact(rendered_summary, 140),
        "position_notes": [_compact(x, 120) for x in rendered["position_notes"][:2]],
        "decision_notes": [_compact(x, 120) for x in rendered["decision_notes"][:2]],
        "risk_checks": [_compact(x, 120) for x in rendered["risk_checks"][:2]],
        "confidence": int(confidence),
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


def fallback_section(status: str) -> list[str]:
    """LLM 근거를 게시하지 못했을 때의 표시. 비활성(disabled)이면 빈 리스트(섹션 숨김)."""
    if not status or status == "disabled":
        return []
    return [
        "🧠 LLM 판단근거",
        f"- 미게시: LLM 근거 검증 실패/미응답 ({_compact(status, 70)})",
        "- 정량 근거 fallback — 위 성과·보유·결정 근거 수치만 판단 근거로 사용",
    ]


def run(payload: dict, *, runner=llm_cli.run_cli) -> tuple[dict | None, str]:
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
        return validate_output(_extract_json_object(getattr(result, "stdout", "") or ""), payload), "ok"
    except Exception as exc:
        return None, f"guard rejected: {_compact(exc, 80)}"
