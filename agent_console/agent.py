from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import tempfile

from . import context, shared_memory, storage

_KST = timezone(timedelta(hours=9))

_SURFACE_TITLES = {
    "market": "현재 시장 상황 인식",
    "portfolio": "포트폴리오 로직 점검",
    "ticker": "종목 맥락 판단",
    "paper": "모의투자 로직 점검",
    "lab": "전략 가설 점검",
}


def build_context_prompt(surface: str = "market") -> str:
    pack = _safe_context_pack(surface)
    top_events = pack["sources"]["events"][:8]
    memory = pack["memory"][:8]
    lines = [
        "너는 stock-report AI 운영 콘솔의 로컬 에이전트다.",
        "역할: 시장 맥락, 모의투자, 추천 사후검증, 포트폴리오 실험을 연결해서 설명한다.",
        "원칙: 실제 매매 지시가 아니라 근거, 리스크, 검증 상태, 다음 확인 질문을 제공한다.",
        f"현재 화면: {pack['surface']}",
        "",
        "[최근 수집 이벤트]",
    ]
    for item in top_events:
        lines.append(f"- {item.get('source')}: {item.get('title')}")
    lines += ["", "[누적 World Memory]"]
    for item in memory:
        lines.append(f"- {item.get('observed_at')} · {item.get('kind')} · {item.get('title')}")
    try:
        shared_section = shared_memory.build_context_section(
            {"screen": surface, "query": "stock-report AI 콘솔 컨텍스트 프롬프트", "limit": 4}
        )
    except Exception:
        shared_section = ""
    if shared_section:
        lines += ["", shared_section]
    lines += ["", "[화면별 초점]", *[f"- {x}" for x in pack["focus"]]]
    return "\n".join(lines)


def answer(question: str, surface: str = "market") -> dict:
    question = str(question or "").strip()
    surface = str(surface or "market").strip().lower()
    if not question:
        return {"ok": False, "error": "질문을 입력해 주세요."}

    history = _safe_list_conversation(limit=12, surface=surface)
    _safe_add_conversation("user", question, surface)
    pack = _safe_context_pack(surface)
    try:
        response = _compose_answer(question, pack, history=history)
    except Exception as exc:
        response = _compose_error_fallback_answer(question, pack, exc)
    _safe_add_conversation("assistant", response, surface)
    try:
        shared_memory.append_chat_exchange(question, response, surface)
    except Exception:
        pass
    sources = pack.get("sources") or {}
    return {
        "ok": True,
        "answer": response,
        "surface": surface,
        "context": {
            "event_count": len(sources.get("events") or []),
            "memory_count": len(pack.get("memory") or []),
            "shared_memory_count": (pack.get("shared_memory") or {}).get("recordCount", 0),
            "source_counts": sources.get("source_counts") or [],
            "symbol_counts": sources.get("symbol_counts") or [],
            "context_error": pack.get("context_error"),
        },
        "conversation": _safe_list_conversation(limit=20, surface=surface),
    }


def _safe_context_pack(surface: str) -> dict:
    try:
        return context.context_pack(surface)
    except Exception as exc:
        return _fallback_context_pack(surface, str(exc))


def _fallback_context_pack(surface: str, error: str = "") -> dict:
    surface = str(surface or "market").strip().lower()
    try:
        focus = context.focus_for_surface(surface)
    except Exception:
        focus = []
    return {
        "ok": False,
        "surface": surface,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": "stock-report",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "reports": [],
        "ml_activity": [],
        "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": [error] if error else []},
        "paper": {"kr": None, "us": None, "combined": None, "errors": [error] if error else []},
        "models": {"items": []},
        "memory": [],
        "focus": focus,
        "shared_memory": {"ok": False, "error": error, "records": []} if error else {"ok": True, "records": []},
        "context_error": error,
    }


def _safe_list_conversation(limit: int, surface: str) -> list[dict]:
    try:
        return storage.list_conversation(limit=limit, context_surface=surface)
    except Exception:
        return []


def _safe_add_conversation(role: str, message: str, surface: str) -> int | None:
    try:
        return storage.add_conversation(role, message, surface)
    except Exception:
        return None


def _compose_error_fallback_answer(question: str, pack: dict, exc: Exception) -> str:
    base = _fallback_general_chat(question, pack, history=[])
    return "\n\n".join([
        base,
        f"참고: 답변 조립 중 일부 내부 컨텍스트 오류가 있었습니다. 핵심 질문은 계속 처리하되, 세부 수치가 비어 있을 수 있습니다. (`{type(exc).__name__}`)",
    ])


def _compose_answer(question: str, pack: dict, history: list[dict] | None = None) -> str:
    resolved_question = _resolve_followup_question(question, history)
    if _is_trading_logic_question(question) or _is_trading_followup(question, history):
        return _compose_trading_logic_answer(question, pack)
    if _is_portfolio_preference_question(question, pack, history):
        return _compose_portfolio_preference_answer(question, pack, history)
    if _is_portfolio_risk_question(resolved_question, pack):
        return _compose_portfolio_risk_answer(resolved_question, pack, history)
    if _is_domestic_etf_question(resolved_question, history):
        return _compose_domestic_etf_answer(question, resolved_question, pack, history)
    asset = _extract_asset_symbol(resolved_question)
    if asset:
        return _compose_asset_opinion_answer(resolved_question, pack, history, asset)
    if not _is_market_context_question(resolved_question, pack):
        return _compose_general_chat_answer(resolved_question, pack, history)

    events = pack["sources"]["events"]
    memory = pack["memory"]
    reports = pack["reports"]
    ml_rows = pack["ml_activity"]
    source_counts = pack["sources"]["source_counts"]
    symbol_counts = pack["sources"]["symbol_counts"]
    surface = pack["surface"]
    read = _market_read(question, pack)

    lines = [f"### {read['title']}", ""]
    lines.append(f"`{_format_kst(pack.get('generated_at'))}` · **{read['status']}**")
    lines.append("")
    lines.append(f"> {read['summary']}")
    lines.append("")
    lines.append(read["narrative"])

    lines.append("")
    lines.append("#### 시장 신호 점수")
    lines.append("")
    lines.append("| 신호 | 점수 | 해석 |")
    lines.append("|---|---:|---|")
    for item in read["signals"]:
        lines.append(f"| {item['name']} | {item['score']} | {item['text']} |")

    lines.append("")
    lines.append("#### 지금 볼 우선순위")
    lines.append("")
    for idx, item in enumerate(read["priorities"], start=1):
        lines.append(f"{idx}. **{item['title']}**")
        lines.append(f"   {item['body']}")

    lines.append("")
    lines.append("#### 근거로 잡은 변화")
    if events:
        for item in events[:4]:
            title = item.get("title") or item.get("summary") or "(제목 없음)"
            lines.append(f"- {item.get('source', 'source')} · {title}")
    else:
        lines.append("- 최근 수집 이벤트가 부족합니다. 먼저 메모리 적재를 실행하는 게 좋습니다.")
    if memory:
        memory_line = " · ".join((item.get("title") or "")[:34] for item in memory[:3] if item.get("title"))
        if memory_line:
            lines.append(f"- World Memory · {memory_line}")

    lines.append("")
    lines.append("#### 검증 상태")
    if ml_rows:
        file_counts = Counter(row.get("_file", "unknown") for row in ml_rows)
        lines.append("- 최근 ML/모의 원장: " + ", ".join(f"{name} {cnt}건" for name, cnt in file_counts.most_common(5)))
    else:
        lines.append("- 최근 ML activity를 찾지 못했습니다.")
    if reports:
        lines.append(f"- 최신 리포트: {reports[0].get('name')} ({reports[0].get('mtime')})")
    if source_counts:
        lines.append("- 수집 소스: " + ", ".join(f"{src} {cnt}" for src, cnt in source_counts[:5]))
    if symbol_counts:
        lines.append("- 자주 언급된 심볼: " + ", ".join(f"{sym} {cnt}" for sym, cnt in symbol_counts[:8]))

    lines.append("")
    lines.append("#### Codex에게 바로 물어볼 질문")
    lines.extend(_next_questions(surface, read))
    return "\n".join(lines)


def _is_portfolio_risk_question(question: str, pack: dict | None = None) -> bool:
    q = str(question or "").lower()
    surface = str((pack or {}).get("surface") or "").lower()
    risk_words = (
        "현재 비중", "비중", "줄여", "줄일", "축소", "리스크", "위험",
        "최대 손실", "손실한도", "손실 한도", "max loss", "시나리오",
        "현금", "레버리지", "리밸런싱", "리밸런스",
    )
    return surface == "portfolio" and any(word in q for word in risk_words)


def _compose_portfolio_risk_answer(question: str, pack: dict, history: list[dict] | None = None) -> str:
    q = str(question or "")
    portfolio = pack.get("portfolio") or {}
    holdings = portfolio.get("holdings") or []
    paper = pack.get("paper") or {}
    limit_pct = _extract_loss_limit_pct(q) or _extract_loss_limit_pct(_last_user_question(history)) or 1.0
    is_scenario = any(word in q.lower() for word in ("시나리오", "손실한도", "손실 한도", "최대 손실", "max loss"))

    if not holdings:
        return "\n".join([
            "### 포트폴리오 리스크 점검",
            "",
            "> 현재 보유 비중 스냅샷을 읽지 못했습니다. `portfolio_snapshot.json` 또는 보유 데이터 동기화가 먼저 필요합니다.",
            "",
            "그래도 방향은 이렇습니다: 손실한도 기준 답변은 시장 뉴스보다 **포지션 크기, 종목별 손절폭, 상관관계, 현금 비중**을 먼저 봐야 합니다.",
        ])

    risk = _portfolio_risk_snapshot(holdings, paper)
    if is_scenario:
        return _portfolio_loss_scenario_answer(question, pack, risk, limit_pct)
    return _portfolio_reduce_risk_answer(question, pack, risk, limit_pct)


def _extract_loss_limit_pct(text: str) -> float | None:
    source = str(text or "").lower()
    match = re.search(r"(?:최대\s*)?(?:손실\s*한도|손실한도|max\s*loss|loss)\D{0,8}(\d+(?:\.\d+)?)\s*%", source)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:안|이내|손실|한도)", source)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 0 < value <= 50 else None


def _portfolio_risk_snapshot(holdings: list[dict], paper: dict) -> dict:
    rows = []
    for raw in holdings:
        ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        rows.append({
            "ticker": ticker,
            "name": str(raw.get("name") or ticker).strip(),
            "weight": _num(raw.get("weight"), 0.0),
            "ret": _num(raw.get("ret"), 0.0),
            "value": _num(raw.get("value"), 0.0),
        })
    rows.sort(key=lambda row: row["weight"], reverse=True)
    top_weight = rows[0]["weight"] if rows else 0.0
    top3_weight = sum(row["weight"] for row in rows[:3])
    negative = [row for row in rows if row["ret"] < 0]
    drawdown_accounts = []
    for key in ("kr", "us"):
        account = paper.get(key) if isinstance(paper, dict) else None
        if isinstance(account, dict):
            drawdown_accounts.append({
                "label": "KR 모의" if key == "kr" else "US 모의",
                "mdd": _num(account.get("strat_mdd"), None),
                "cum_ret": _num(account.get("cum_ret"), None),
                "cash": _num(account.get("cash"), None),
                "currency": account.get("currency") or "",
            })
    concentration = "높음" if top_weight >= 25 or top3_weight >= 65 else "보통" if top_weight >= 15 else "낮음"
    reduce_first = _rank_reduce_candidates(rows)
    return {
        "holdings": rows,
        "top_weight": top_weight,
        "top3_weight": top3_weight,
        "negative": negative,
        "drawdown_accounts": drawdown_accounts,
        "concentration": concentration,
        "reduce_first": reduce_first,
    }


def _rank_reduce_candidates(rows: list[dict]) -> list[dict]:
    candidates = []
    for row in rows:
        score = 0.0
        reasons = []
        if row["weight"] >= 25:
            score += 4
            reasons.append("단일 비중 과대")
        elif row["weight"] >= 15:
            score += 2
            reasons.append("상위 비중")
        if row["ret"] < -8:
            score += 3
            reasons.append("손실 확대")
        elif row["ret"] < 0:
            score += 1
            reasons.append("약세")
        if row["ticker"] in {"TQQQ", "SOXL", "SQQQ", "SOXS", "QLD", "SSO"}:
            score += 3
            reasons.append("레버리지/고변동")
        if row["ticker"] in {"NVDA", "MU", "SMH", "SOXX", "TSM", "AVGO", "AMD", "INTC", "QCOM"}:
            score += 1.5
            reasons.append("반도체/AI 베타")
        candidates.append({**row, "score": score, "reasons": reasons or ["비중 점검"]})
    candidates.sort(key=lambda row: (row["score"], row["weight"]), reverse=True)
    return candidates[:5]


def _portfolio_reduce_risk_answer(question: str, pack: dict, risk: dict, limit_pct: float) -> str:
    holdings = risk["holdings"]
    reduce_first = risk["reduce_first"]
    lines = ["### 먼저 줄일 리스크", ""]
    lines.append(f"`{_format_kst(pack.get('generated_at'))}` · **현재 비중 기준**")
    lines.append("")
    lines.append(
        f"> 시장 국면보다 먼저 볼 것은 **집중도와 한 번 틀렸을 때 계좌 손실이 {limit_pct:.1f}% 안에서 멈추는지**입니다."
    )
    lines.append("")
    lines.append("#### 현재 구조")
    lines.append(f"- 보유 종목 수: {len(holdings)}개")
    lines.append(f"- 1위 비중: {_fmt_pct(risk['top_weight'])}")
    lines.append(f"- 상위 3개 합산: {_fmt_pct(risk['top3_weight'])}")
    lines.append(f"- 집중도 판정: **{risk['concentration']}**")
    if risk["negative"]:
        neg = ", ".join(f"{row['ticker']} {_fmt_pct(row['ret'])}" for row in risk["negative"][:4])
        lines.append(f"- 손실 구간 보유: {neg}")

    lines.append("")
    lines.append("#### 우선 줄일 후보")
    lines.append("")
    lines.append("| 우선 | 종목 | 비중 | 손익 | 줄이는 이유 |")
    lines.append("|---:|---|---:|---:|---|")
    for idx, row in enumerate(reduce_first[:4], start=1):
        lines.append(
            f"| {idx} | {row['ticker']} | {_fmt_pct(row['weight'])} | {_fmt_pct(row['ret'])} | {', '.join(row['reasons'][:3])} |"
        )

    lines.append("")
    lines.append("#### 실행 순서")
    lines.extend([
        "1. **단일 25% 초과 포지션부터 상한을 낮춥니다.** 좋은 종목이어도 포트 전체를 흔드는 비중이면 먼저 깎습니다.",
        "2. **손실 중인 고베타/레버리지부터 줄입니다.** 반등 기대보다 손실한도 유지가 우선입니다.",
        "3. **상위 3개 합산이 60%를 넘으면 현금 또는 저상관 자산으로 옮깁니다.** 새 매수보다 포트 흔들림을 줄이는 단계입니다.",
    ])
    lines.append("")
    lines.append("#### 바로 쓸 기준")
    lines.append(
        f"각 포지션은 `비중 × 허용 하락폭 ≤ {limit_pct:.1f}%`로 잡겠습니다. "
        "예를 들어 20% 비중이면 해당 종목의 허용 하락폭은 약 5%입니다. 그보다 크게 흔들릴 종목은 비중을 낮춰야 합니다."
    )
    return "\n".join(lines)


def _portfolio_loss_scenario_answer(question: str, pack: dict, risk: dict, limit_pct: float) -> str:
    holdings = risk["holdings"]
    top = risk["reduce_first"][0] if risk["reduce_first"] else None
    current_high_beta = sum(
        row["weight"]
        for row in holdings
        if row["ticker"] in {"TQQQ", "SOXL", "QLD", "SSO", "NVDA", "MU", "SMH", "SOXX", "AMD", "TSM", "AVGO"}
    )
    lines = ["### 최대 손실한도 시나리오", ""]
    lines.append(f"`{_format_kst(pack.get('generated_at'))}` · **계좌 손실한도 {limit_pct:.1f}% 기준**")
    lines.append("")
    lines.append(
        "> 이 답변에서는 시장 뉴스보다 **한 번의 틀린 판단이 계좌 전체에 몇 % 손실을 만들 수 있는지**를 기준으로 봅니다."
    )
    lines.append("")
    lines.append("#### 현재 위험 예산")
    lines.append(f"- 상위 1개 비중: {_fmt_pct(risk['top_weight'])}")
    lines.append(f"- 상위 3개 비중: {_fmt_pct(risk['top3_weight'])}")
    lines.append(f"- AI/반도체/레버리지 성격 비중 추정: {_fmt_pct(current_high_beta)}")
    if top:
        lines.append(f"- 가장 먼저 점검할 포지션: **{top['ticker']}** ({_fmt_pct(top['weight'])}, {', '.join(top['reasons'][:2])})")
    lines.append("")
    lines.append("#### 시나리오")
    lines.append("")
    lines.append("| 모드 | 조건 | 공격/고베타 | 현금/방어 | 행동 |")
    lines.append("|---|---|---:|---:|---|")
    lines.append("| 방어 | HYG/LQD 약세, 달러/유가 상승, 상위 보유 손절선 접근 | 40% 이하 | 30% 이상 | 레버리지 중지, 손실 포지션 먼저 축소 |")
    lines.append("| 기본 | 주식 반등은 있으나 크레딧 확인 전 | 50~60% | 20~30% | 신규 매수는 분할, 상위 3개 60% 이하 유지 |")
    lines.append("| 공격 | QQQ/반도체 반등 + 크레딧 안정 + 보유 상위 종목 거래대금 동반 | 65~75% | 10~20% | 손실한도 내에서만 레버리지/테마 ETF 추가 |")
    lines.append("")
    lines.append("#### 포지션 크기 공식")
    lines.append(
        f"- 종목별 최대 비중 = `{limit_pct:.1f}% / 예상 손절폭%`입니다. "
        "손절폭 5%면 최대 20%, 손절폭 10%면 최대 10%입니다."
    )
    lines.append("- 레버리지 ETF는 같은 손절폭이라도 실질 변동성이 크므로 계산 비중의 1/2만 씁니다.")
    lines.append("- 이미 손실 중인 포지션은 신규 진입 후보보다 위험 예산을 먼저 차지한 것으로 봅니다.")
    lines.append("")
    lines.append("#### 지금 결론")
    lines.append(
        "지금은 공격 신호를 새로 찾기보다 **상위 비중과 고베타 묶음을 먼저 낮춰 손실한도 여유를 만드는 단계**입니다. "
        "그 다음에 크레딧과 반도체 수급이 같이 좋아질 때만 공격 비중을 다시 켜는 쪽이 맞습니다."
    )
    return "\n".join(lines)


def _is_portfolio_preference_question(question: str, pack: dict | None = None,
                                      history: list[dict] | None = None) -> bool:
    q = str(question or "").lower()
    surface = str((pack or {}).get("surface") or "").lower()
    if surface != "portfolio":
        return False
    preference_words = (
        "들고 가", "들고가", "가져가", "유지", "계속", "보유", "홀딩", "hold", "keep",
        "팔기 싫", "팔고 싶지", "안 팔", "남기고", "남겨", "가지고",
    )
    if not any(word in q for word in preference_words):
        return False
    if _resolve_portfolio_symbol(question, (pack or {}).get("portfolio", {}).get("holdings") or []):
        return True
    previous = _last_user_question(history).lower()
    return any(word in previous for word in ("비중", "리스크", "손실", "줄여", "시나리오"))


def _resolve_portfolio_symbol(question: str, holdings: list[dict]) -> str | None:
    q = str(question or "")
    ql = q.lower()
    tickers = {str(row.get("ticker") or row.get("symbol") or "").upper() for row in holdings}
    tickers.discard("")

    for raw in re.findall(r"\$?[A-Za-z][A-Za-z0-9.-]{1,12}", q):
        token = raw.lstrip("$").upper()
        if token in tickers:
            return token

    for row in holdings:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        name = str(row.get("name") or "").strip()
        if ticker and ticker.lower() in ql:
            return ticker
        if name and name.lower() in ql:
            return ticker

    try:
        import ticker_names
        chunks = re.findall(r"[A-Za-z0-9.-]{2,}|[가-힣A-Za-z0-9&+.-]+", q)
        for chunk in chunks:
            term = re.sub(r"(은|는|이|가|을|를|도|만|으로|에는|에서|까지|부터)$", "", chunk.strip())
            if not term:
                continue
            resolved = ticker_names.resolve(term, allow_net=False)
            if resolved and resolved.upper() in tickers:
                return resolved.upper()
    except Exception:
        pass
    return None


def _compose_portfolio_preference_answer(question: str, pack: dict,
                                         history: list[dict] | None = None) -> str:
    portfolio = pack.get("portfolio") or {}
    holdings = portfolio.get("holdings") or []
    ticker = _resolve_portfolio_symbol(question, holdings)
    limit_pct = _extract_loss_limit_pct(question) or _extract_loss_limit_pct(_last_user_question(history)) or 1.0
    if not holdings or not ticker:
        return "\n".join([
            "### 보유 희망 조건 반영",
            "",
            "> 이건 시장 해설 질문이 아니라 `특정 종목은 유지하고 싶다`는 조건 추가로 이해했습니다.",
            "",
            "다만 현재 보유 스냅샷에서 해당 종목을 특정하지 못했습니다. 종목명이나 티커를 한 번만 더 명확히 주면, 그 종목을 보호 포지션으로 두고 나머지에서 줄일 후보를 다시 짜겠습니다.",
        ])

    risk = _portfolio_risk_snapshot(holdings, pack.get("paper") or {})
    protected = next((row for row in risk["holdings"] if row["ticker"] == ticker), None)
    other_candidates = _rank_reduce_candidates([row for row in risk["holdings"] if row["ticker"] != ticker])
    name = protected["name"] if protected else ticker
    weight = protected["weight"] if protected else 0.0
    ret = protected["ret"] if protected else 0.0
    allowable_drop = (limit_pct / weight * 100) if weight > 0 else None
    action = "유지 가능" if weight <= 20 else "유지하되 추가 확대 금지"
    if weight >= 25:
        action = "핵심 보유는 가능하지만 일부 상한 조정 필요"

    lines = [f"### {name}({ticker}) 유지 조건부 리밸런싱", ""]
    lines.append(f"`{_format_kst(pack.get('generated_at'))}` · **보유 희망 조건 반영**")
    lines.append("")
    lines.append(
        f"> 문맥상 이건 새 시장 분석이 아니라 **{ticker}는 들고 가고 싶으니, 다른 곳에서 리스크를 줄이자**는 요청으로 이해했습니다."
    )
    lines.append("")
    lines.append("#### 결론")
    lines.append(
        f"- {ticker}는 지금 감축 1순위로 보지 않고 **보호 포지션**으로 둡니다."
    )
    lines.append(f"- 현재 비중은 {_fmt_pct(weight)}, 손익은 {_fmt_pct(ret)}입니다.")
    lines.append(f"- 판단: **{action}**. 보유와 추가매수는 분리해서 봐야 합니다.")
    if allowable_drop is not None:
        lines.append(
            f"- 계좌 손실한도 {limit_pct:.1f}% 기준으로, {ticker} 단독 허용 하락폭은 약 {allowable_drop:.1f}%입니다. "
            "이보다 넓게 들고 가고 싶다면 다른 고베타 비중을 줄여야 합니다."
        )

    lines.append("")
    lines.append("#### 대신 줄일 후보")
    if other_candidates:
        lines.append("")
        lines.append("| 우선 | 종목 | 비중 | 손익 | 이유 |")
        lines.append("|---:|---|---:|---:|---|")
        for idx, row in enumerate(other_candidates[:4], start=1):
            lines.append(
                f"| {idx} | {row['ticker']} | {_fmt_pct(row['weight'])} | {_fmt_pct(row['ret'])} | {', '.join(row['reasons'][:3])} |"
            )
    else:
        lines.append("- 현재 보유 스냅샷에서는 대체 감축 후보가 충분히 보이지 않습니다.")

    lines.append("")
    lines.append("#### 운용 규칙")
    lines.extend([
        f"1. **{ticker}는 매도 후보에서 빼고, 비중 상한만 둡니다.** 손실한도 초과 전까지는 보유 논리를 유지합니다.",
        "2. **손실 중인 레버리지/고베타를 먼저 줄입니다.** 원하는 종목을 들고 가려면 다른 쪽에서 변동성을 줄여야 합니다.",
        "3. **추가매수는 별도 조건입니다.** 유지 판단이 곧 물타기나 비중 확대 신호는 아닙니다.",
    ])
    return "\n".join(lines)


_ASSET_ALIASES = {
    "btc": ("BTC-USD", "비트코인"),
    "bitcoin": ("BTC-USD", "비트코인"),
    "eth": ("ETH-USD", "이더리움"),
    "ethereum": ("ETH-USD", "이더리움"),
    "sol": ("SOL-USD", "솔라나"),
    "solana": ("SOL-USD", "솔라나"),
    "xrp": ("XRP-USD", "리플"),
    "doge": ("DOGE-USD", "도지코인"),
    "qqq": ("QQQ", "QQQ"),
    "spy": ("SPY", "SPY"),
    "tqqq": ("TQQQ", "TQQQ"),
    "qld": ("QLD", "QLD"),
    "soxl": ("SOXL", "SOXL"),
    "orcl": ("ORCL", "오라클"),
    "nvda": ("NVDA", "엔비디아"),
    "mu": ("MU", "마이크론"),
}


def _resolve_followup_question(question: str, history: list[dict] | None = None) -> str:
    q = str(question or "").strip()
    if not q or not _looks_like_followup_correction(q):
        return q
    previous = _last_user_question(history)
    if not previous:
        return q
    return f"{previous} / 정정: {q}"


def _last_user_question(history: list[dict] | None = None) -> str:
    for row in reversed(history or []):
        if row.get("role") != "user":
            continue
        text = str(row.get("message") or row.get("content") or "").strip()
        if text:
            return text
    return ""


def _looks_like_followup_correction(question: str) -> bool:
    q = str(question or "").strip().lower()
    if not q or len(q) > 60:
        return False
    correction_words = ("아니", "아니아니", "그거 말고", "말고", "아니고", "정정", "그 뜻", "그게 아니라")
    context_words = ("국내", "etf", "코스피", "코스닥", "한국", "미국", "종목", "브랜드", "상장")
    return any(word in q for word in correction_words) and any(word in q for word in context_words)


def _extract_asset_symbol(question: str) -> tuple[str, str] | None:
    q = str(question or "").strip()
    ql = q.lower()
    if _is_domestic_etf_question(q):
        return None
    intent_words = (
        "어때", "어떰", "어떠", "top", "탑", "티어", "매수", "진입", "목표",
        "가냐", "가능", "전망", "보유", "팔", "살", "+", "롱", "숏",
    )
    if not any(word in ql for word in intent_words):
        return None
    raw_tokens = re.findall(r"\$?[A-Za-z][A-Za-z0-9.-]{1,12}", q)
    for raw in raw_tokens:
        token = raw.lstrip("$").lower()
        if token in _ASSET_ALIASES:
            return _ASSET_ALIASES[token]
    try:
        import ticker_names
        for chunk in re.findall(r"[A-Za-z0-9.-]{2,}|[가-힣A-Za-z0-9&+.-]+", q):
            term = re.sub(r"(은|는|이|가|을|를|도|만|으로|에는|에서|까지|부터)$", "", chunk.strip())
            if not term:
                continue
            resolved = ticker_names.resolve(term, allow_net=False)
            if resolved:
                return (resolved, ticker_names.display_name(resolved, allow_net=False) or resolved)
    except Exception:
        pass
    for raw in raw_tokens:
        token = raw.lstrip("$").lower()
        if raw.isupper() and 2 <= len(raw) <= 6 and token not in {"top"}:
            return (raw.upper(), raw.upper())
    return None


def _is_domestic_etf_question(question: str, history: list[dict] | None = None) -> bool:
    q = str(question or "").lower()
    has_etf = "etf" in q or "상장지수" in q
    domestic = any(word in q for word in ("국내", "한국", "코스피", "코스닥", "krx", "kospi", "kosdaq"))
    brands = ("kodex", "tiger", "ace", "sol", "rise", "kbstar", "hanaro", "koact", "히어로즈")
    ai_theme = "ai" in q or "인공지능" in q or "반도체" in q or "top" in q or "탑" in q
    if has_etf and (domestic or any(brand in q for brand in brands)):
        return True
    if domestic and ai_theme and _last_user_question(history):
        return True
    return False


def _compose_domestic_etf_answer(question: str, resolved_question: str, pack: dict,
                                 history: list[dict] | None) -> str:
    llm_prompt = (
        "사용자의 최신 발화는 직전 질문에 대한 정정입니다. "
        f"직전 맥락까지 합친 질문: '{resolved_question}'. "
        "여기서 SOL은 크립토 심볼이 아니라 국내 상장 ETF 브랜드/상품명일 가능성이 높습니다. "
        "정확한 종목코드가 없으면 확정 가격·수익률은 만들지 말고, 국내 AI 테마 ETF 평가 프레임으로 답하세요. "
        "이전 질문을 기억했다는 점을 반영해 한국어로 자연스럽게 답하세요."
    )
    llm = _try_llm_chat(llm_prompt, pack, history)
    if llm:
        return llm

    previous = _last_user_question(history)
    lines = ["### 국내 ETF 기준으로 다시 볼게요", ""]
    if previous:
        lines.append(f"> 방금 말은 직전 질문 **“{previous}”**에서 `SOL`을 코인이 아니라 **국내 상장 ETF** 쪽으로 정정한 걸로 이해했습니다.")
    else:
        lines.append("> `SOL`을 코인이 아니라 국내 ETF 브랜드/상품명 쪽으로 보겠습니다.")
    lines.append("")
    lines.append("#### 먼저 정정")
    lines.append("- 여기서 `SOL`은 **국내 ETF 브랜드명/상품명**으로 보겠습니다.")
    lines.append("- `AI top 2+`는 상품명이 정확히 필요합니다. 종목코드가 없으면 가격·괴리율·구성종목 비중은 단정하면 안 됩니다.")
    lines.append("")
    lines.append("#### 평가 기준")
    lines.extend([
        "- **구성 상위 2종목 집중도**: 이름처럼 top 2 비중이 크면 상승 탄력은 좋지만, 종목 리스크도 같이 커집니다.",
        "- **AI 노출의 질**: 단순 테마명보다 반도체, 전력, 데이터센터, 소프트웨어 중 어디에 실제로 베팅하는지 봐야 합니다.",
        "- **거래대금/스프레드**: 국내 테마 ETF는 유동성이 얇으면 진입가보다 청산가가 더 중요해집니다.",
        "- **총보수와 괴리율**: 장기 보유면 총보수, 단기 매매면 괴리율과 호가 간격을 먼저 봅니다.",
    ])
    lines.append("")
    lines.append("#### 내 판단")
    lines.append(
        "국내 AI 집중 ETF라면 **개별 AI/반도체주를 직접 고르기 부담스러울 때 쓰는 위성 포지션**으로는 괜찮습니다. "
        "다만 top 2 집중형이면 분산 ETF라기보다 `테마 압축 베팅`에 가까워서, 포트 핵심 비중보다는 손실한도를 정한 보조 비중이 맞습니다."
    )
    lines.append("")
    lines.append("종목코드나 정확한 ETF명을 주면 구성종목, 거래대금, 보수, 최근 수익률 기준으로 바로 다시 평가할게요.")
    return "\n".join(lines)


def _compose_asset_opinion_answer(question: str, pack: dict, history: list[dict] | None,
                                  asset: tuple[str, str]) -> str:
    symbol, name = asset
    llm_prompt = (
        f"자산 질문입니다. 사용자가 '{question}'라고 물었습니다. "
        f"대상은 {name}({symbol})로 해석하세요. 현재 제공된 컨텍스트에 직접 데이터가 부족하면 부족하다고 말하고, "
        "조건부 판단, 확인할 가격/상대강도/리스크를 한국어로 짧게 답하세요."
    )
    llm = _try_llm_chat(llm_prompt, pack, history)
    if llm:
        return llm

    ql = str(question or "").lower()
    top2 = "top" in ql or "탑" in ql or "2+" in ql
    if symbol == "SOL-USD":
        take = (
            "짧게 보면 **SOL은 '상위권 재평가 후보'는 맞지만, top 2+를 바로 베팅할 근거는 아직 확인이 필요**합니다."
            if top2 else
            "**SOL은 강한 베타 자산이라 리스크온 구간에서는 좋지만, 단독 확신 매수보다 조건부 접근이 맞습니다.**"
        )
        checks = [
            "SOL/BTC와 SOL/ETH 상대강도가 고점을 다시 높이는지",
            "네트워크 수수료·활성주소·DEX/DeFi 거래량이 가격보다 먼저 개선되는지",
            "BTC가 위험선호를 유지하고 ETH 대비 내러티브가 실제 자금 유입으로 이어지는지",
        ]
        risks = [
            "가동 중단/성능 이슈 재발",
            "락업·대형 물량·밸리데이터 집중 리스크",
            "크립토 전체가 risk-off로 꺾일 때 SOL이 더 크게 빠지는 베타 리스크",
        ]
    else:
        take = f"**{name}({symbol})은 지금 로컬 컨텍스트만으로 확정 판단하기보다 조건부로 보는 게 맞습니다.**"
        checks = [
            f"{symbol} 자체 추세가 시장/동종 자산 대비 강한지",
            "거래량을 동반한 돌파인지, 단순 반등인지",
            "손절 기준을 먼저 정해도 기대수익/위험비가 남는지",
        ]
        risks = ["직접 뉴스·가격 데이터 부족", "시장 전체 risk-off 전환", "거래량 없는 단기 반등"]

    lines = [f"### {name}({symbol}) 의견", ""]
    lines.append(f"> {take}")
    lines.append("")
    lines.append("#### 지금 볼 조건")
    lines.extend(f"- {item}" for item in checks)
    lines.append("")
    lines.append("#### 조심할 점")
    lines.extend(f"- {item}" for item in risks)
    lines.append("")
    lines.append(
        "결론적으로, 이 질문이 '포트폴리오 top 2 비중 후보냐'는 뜻이면 **소액/조건부 후보**에 가깝고, "
        "'시총 top 2 이상 가능하냐'는 뜻이면 **가능성은 있지만 아직 검증해야 할 내러티브**로 보겠습니다."
    )
    return "\n".join(lines)


def _is_market_context_question(question: str, pack: dict | None = None) -> bool:
    q = str(question or "").lower()
    market_words = (
        "시장", "증시", "종목", "주식", "포트폴리오", "보유", "비중", "수익률", "리스크",
        "뉴스", "이슈", "금리", "유가", "달러", "환율", "vix", "qqq", "spy", "반도체",
        "ai", "레버리지", "mdd", "벤치", "오른", "내린", "상승", "하락", "반등", "조정",
        "매수", "매도", "진입", "청산", "추천", "성과", "왜 올", "왜 떨", "etf",
        "국내", "코스피", "코스닥", "kospi", "kosdaq", "kodex", "tiger", "ace", "sol",
    )
    if any(word in q for word in market_words):
        return True
    return False


def _is_trading_followup(question: str, history: list[dict] | None) -> bool:
    q = str(question or "").strip().lower()
    if len(q) > 36:
        return False
    follow_words = ("그럼", "그래서", "어떻게", "바꿔", "수정", "왜", "기준", "다음", "더")
    if not any(word in q for word in follow_words):
        return False
    for row in reversed(history or []):
        if row.get("role") != "assistant":
            continue
        text = str(row.get("message") or "")
        return "모의·단기투자 로직 평가" in text or "단기투자" in text
    return False


def _compose_general_chat_answer(question: str, pack: dict, history: list[dict] | None = None) -> str:
    llm = _try_llm_chat(question, pack, history)
    if llm:
        return llm
    return _fallback_general_chat(question, pack, history)


def _try_llm_chat(question: str, pack: dict, history: list[dict] | None = None,
                  runner=subprocess.run) -> str | None:
    if os.getenv("AGENT_CONSOLE_LLM_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return None
    prompt = _build_general_chat_prompt(question, pack, history)
    return _try_codex_chat(prompt, runner=runner) or _try_hermes_chat(prompt, runner=runner)


def _try_codex_chat(prompt: str, runner=subprocess.run) -> str | None:
    if os.getenv("AGENT_CONSOLE_CODEX_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return None
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="agent-codex-", suffix=".txt", delete=False) as tmp:
            out_path = tmp.name
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--cd",
            os.getenv("AGENT_CONSOLE_CODEX_CWD", "/tmp"),
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            out_path,
        ]
        model = os.getenv("AGENT_CONSOLE_CODEX_MODEL")
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        timeout = int(os.getenv("AGENT_CONSOLE_CODEX_TIMEOUT", os.getenv("AGENT_CONSOLE_LLM_TIMEOUT", "75")) or "75")
        result = runner(cmd, capture_output=True, text=True, timeout=max(10, min(timeout, 240)))
        if getattr(result, "returncode", 1) != 0:
            return None
        text = Path(out_path).read_text(encoding="utf-8", errors="replace").strip() if out_path else ""
        if not text:
            text = (getattr(result, "stdout", "") or "").strip()
        return text[:6000] if text else None
    except Exception:
        return None
    finally:
        if out_path:
            try:
                Path(out_path).unlink(missing_ok=True)
            except Exception:
                pass


def _try_hermes_chat(prompt: str, runner=subprocess.run) -> str | None:
    if os.getenv("AGENT_CONSOLE_HERMES_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return None
    cmd = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "--provider",
        os.getenv("AGENT_CONSOLE_LLM_PROVIDER", os.getenv("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex")),
        "--model",
        os.getenv("AGENT_CONSOLE_LLM_MODEL", os.getenv("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini")),
        "-Q",
    ]
    timeout = int(os.getenv("AGENT_CONSOLE_LLM_TIMEOUT", "60") or "60")
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=max(10, min(timeout, 180)))
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    text = (getattr(result, "stdout", "") or "").strip()
    if not text:
        return None
    return text[:6000]


def _build_general_chat_prompt(question: str, pack: dict, history: list[dict] | None = None) -> str:
    events = (pack.get("sources") or {}).get("events") or []
    memory = pack.get("memory") or []
    hist = []
    for row in (history or [])[-6:]:
        role = "사용자" if row.get("role") == "user" else "에이전트"
        msg = str(row.get("message") or "").replace("\n", " ")[:500]
        if msg:
            hist.append(f"- {role}: {msg}")
    ctx = []
    for item in events[:4]:
        title = item.get("title") or item.get("summary")
        if title:
            ctx.append(f"- {item.get('source', 'source')}: {title}")
    for item in memory[:3]:
        title = item.get("title")
        if title:
            ctx.append(f"- memory: {title}")
    portfolio_ctx = _compact_portfolio_context(pack)
    paper_ctx = _compact_paper_context(pack)
    try:
        shared_section = shared_memory.build_context_section(
            {
                "screen": pack.get("surface") or "market",
                "query": question,
                "provider": "codex-cli",
                "limit": 6,
            }
        )
    except Exception:
        shared_section = ""
    return "\n".join([
        "너는 stock-report 안의 대화형 에이전트다.",
        "사용자는 한국어로 편하게 말한다. 너도 한국어로 자연스럽게 답한다.",
        "투자 데이터가 필요한 질문이면 주어진 컨텍스트를 참고하되, 일반 질문이면 억지로 시장 리포트로 바꾸지 않는다.",
        "후속 발화가 정정, 조건 추가, 선호 표현이면 직전 질문을 다시 해석해서 답한다.",
        "포트폴리오 화면에서는 시장 총평보다 현재 보유, 비중, 손실한도, 사용자의 보유 선호를 우선한다.",
        "같은 템플릿을 반복하지 말고, 사용자의 최신 문장에 직접 답한다.",
        "공유 메모리는 참고 맥락일 뿐이며 현재 사용자 질문과 화면 컨텍스트가 우선한다.",
        "실제 매매 지시는 피하고, 판단 근거와 확인할 점을 짧고 명확하게 말한다.",
        "모르면 모른다고 말하고, 필요한 정보가 무엇인지 묻는다.",
        f"현재 화면: {pack.get('surface') or 'market'}",
        "",
        "[최근 대화]",
        *(hist or ["- 없음"]),
        "",
        "[사용 가능한 투자 컨텍스트]",
        *(ctx or ["- 없음"]),
        "",
        "[포트폴리오 스냅샷]",
        *(portfolio_ctx or ["- 없음"]),
        "",
        "[모의투자/단기 원장 요약]",
        *(paper_ctx or ["- 없음"]),
        "",
        shared_section or "[컨텍스트 메모리]\n- 없음",
        "",
        f"[사용자 질문]\n{question}",
        "",
        "답변:",
    ])


def _compact_portfolio_context(pack: dict) -> list[str]:
    portfolio = pack.get("portfolio") or {}
    holdings = portfolio.get("holdings") or []
    if not holdings:
        return []
    lines = []
    summary = portfolio.get("summary") or {}
    if summary:
        total = _num(summary.get("total_usd"), None)
        ret = _num(summary.get("return_pct"), None)
        n_holdings = summary.get("n_holdings") or len(holdings)
        parts = [f"보유 {n_holdings}개"]
        if total is not None:
            parts.append(f"총액 ${total:,.0f}")
        if ret is not None:
            parts.append(f"손익 {_fmt_pct(ret)}")
        lines.append("- " + " · ".join(parts))
    for row in holdings[:8]:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        if not ticker:
            continue
        name = str(row.get("name") or ticker)
        weight = _fmt_pct(row.get("weight"))
        ret = _fmt_pct(row.get("ret"))
        lines.append(f"- {name}({ticker}) 비중 {weight}, 손익 {ret}")
    return lines


def _compact_paper_context(pack: dict) -> list[str]:
    paper = pack.get("paper") or {}
    ml_rows = pack.get("ml_activity") or []
    lines = []
    for key, label in (("kr", "KR 모의"), ("us", "US 모의")):
        item = paper.get(key)
        if not isinstance(item, dict):
            continue
        parts = [label]
        if item.get("cum_ret") is not None:
            parts.append(f"누적 {_fmt_pct(item.get('cum_ret'))}")
        if item.get("strat_mdd") is not None:
            parts.append(f"MDD {_fmt_pct(item.get('strat_mdd'))}")
        if item.get("decisions") is not None:
            parts.append(f"결정 {len(item.get('decisions') or [])}건")
        lines.append("- " + " · ".join(parts))
    if ml_rows:
        counts = Counter(row.get("_file", "unknown") for row in ml_rows)
        lines.append("- ML 원장 " + ", ".join(f"{name} {cnt}건" for name, cnt in counts.most_common(4)))
    return lines


def _fallback_general_chat(question: str, pack: dict, history: list[dict] | None = None) -> str:
    q = str(question or "").strip()
    ql = q.lower()
    surface = str(pack.get("surface") or "market")
    if any(word in ql for word in ("안녕", "ㅎㅇ", "hello", "hi")):
        return "안녕. 이제 투자 질문뿐 아니라 일반 질문도 대화처럼 받을게요. 필요하면 시장 데이터, 모의투자, 단기 로직 쪽으로 바로 이어서 볼 수 있습니다."
    if "뭐" in ql and any(word in ql for word in ("할 수", "가능", "기능")):
        return (
            "저는 지금 이 프로젝트 안에서 시장 맥락 설명, 모의투자/단기투자 로직 평가, 포트폴리오 시나리오 정리, "
            "코드 변경 방향 설명을 할 수 있습니다. 일반 질문도 답하되, 실시간 정보가 필요한 내용은 현재 수집된 데이터 기준으로 답합니다."
        )
    if "왜" in ql and "반복" in ql:
        return "이전 답변기가 질문 의도보다 화면 맥락을 먼저 봐서 시장 리포트를 반복했습니다. 이제 범위 밖 질문은 일반 챗봇 답변으로, 투자 질문은 데이터 기반 답변으로 나누도록 바꿨습니다."
    if surface == "portfolio":
        holdings = ((pack.get("portfolio") or {}).get("holdings") or [])
        top = holdings[0] if holdings else None
        lines = ["### 포트폴리오 맥락으로 답할게요", ""]
        previous = _last_user_question(history)
        if previous:
            lines.append(f"> 직전 질문 **“{previous}”**에 이어진 말로 보고, 시장 총평보다 보유 비중 쪽을 먼저 보겠습니다.")
        else:
            lines.append("> 이 화면에서는 시장 총평보다 보유 비중, 손실한도, 사용자의 보유 선호를 먼저 보겠습니다.")
        if top:
            lines.append("")
            lines.append("#### 현재 먼저 보이는 점")
            lines.append(f"- 1위 비중은 {top.get('ticker')} {_fmt_pct(top.get('weight'))}입니다.")
            lines.append("- 특정 종목을 유지하고 싶다면 그 종목을 감축 후보에서 빼고, 나머지 고베타/손실 포지션에서 위험 예산을 맞추는 방식이 맞습니다.")
            lines.append("- 추가매수와 계속 보유는 다른 판단입니다. 보유는 가능해도 비중 확대는 손실한도 여유가 있을 때만 봅니다.")
        return "\n".join(lines)
    if surface == "paper":
        return (
            "### 모의투자 맥락으로 답할게요\n\n"
            "이 화면에서는 시장 해설보다 **결정 원장, 성숙 표본, 손익비, MDD, 거래비용**을 먼저 봐야 합니다. "
            "짧게 말하면 아직 답이 애매한 질문은 `돈을 벌었나`보다 `충분히 자주 검증됐나`로 먼저 판단하겠습니다."
        )
    if surface == "lab":
        return (
            "### 전략랩 맥락으로 답할게요\n\n"
            "이 말은 새 시장 리포트가 아니라 전략 조건 조정으로 보겠습니다. "
            "좋은 전략랩 답변은 `진입 조건`, `청산 조건`, `손실한도`, `검증 기간`, `실패 시 끌 조건`으로 쪼개야 합니다."
        )
    if surface == "ticker":
        return (
            "### 종목 맥락으로 답할게요\n\n"
            "종목 질문은 시장 총평보다 해당 종목의 **보유 여부, 최근 뉴스, 상대강도, 손절 기준**이 먼저입니다. "
            "티커가 화면에서 특정되지 않으면 종목명이나 티커를 기준으로 다시 연결하겠습니다."
        )
    return (
        f"질문은 이해했습니다: **{q}**\n\n"
        "지금은 로컬 모델 응답을 바로 받지 못했지만, 시장 템플릿으로 억지 전환하지 않고 질문 자체에 답하는 방향으로 처리하겠습니다. "
        "투자 판단이 필요한 질문이면 근거, 리스크, 확인 조건 순서로 이어가겠습니다."
    )


def _is_trading_logic_question(question: str) -> bool:
    q = str(question or "").lower()
    logic_words = ("평가", "어때", "어떤", "로직", "문제", "개선", "잘", "돈", "성과")
    trading_words = (
        "모의투자", "모의 투자", "페이퍼", "paper", "단기투자", "단기 투자",
        "단기트레이딩", "단기 트레이딩", "intraday", "슬리브", "가상체결",
    )
    return any(w in q for w in trading_words) and any(w in q for w in logic_words)


def _compose_trading_logic_answer(question: str, pack: dict) -> str:
    paper = pack.get("paper") or {}
    ml_rows = pack.get("ml_activity") or []
    reports = pack.get("reports") or []
    market_read = _market_read(question, pack)
    paper_eval = _paper_logic_eval(paper)
    intraday_eval = _intraday_logic_eval(ml_rows)
    verdict = _trading_verdict(paper_eval, intraday_eval)

    lines = ["### 모의·단기투자 로직 평가", ""]
    lines.append(f"`{_format_kst(pack.get('generated_at'))}` · **{verdict['label']}**")
    lines.append("")
    lines.append(f"> {verdict['summary']}")
    lines.append("")
    lines.append(
        "이 질문에서 시장 국면은 배경일 뿐이고, 핵심은 **로직이 돈을 벌 구조인지, 아직 검증 전인지, 어디서 깨질지**입니다."
    )

    lines.append("")
    lines.append("#### 로직별 판정")
    lines.append("")
    lines.append("| 영역 | 판정 | 근거 |")
    lines.append("|---|---|---|")
    for row in _logic_rows(paper_eval, intraday_eval, market_read):
        lines.append(f"| {row['area']} | {row['verdict']} | {row['reason']} |")

    lines.append("")
    lines.append("#### 내가 보는 장점")
    lines.extend([
        "- **성과 검증 구조는 맞습니다.** 결정 원장과 outcome 원장을 분리해 사후 적중률을 볼 수 있게 한 건 좋은 방향입니다.",
        "- **MDD·벤치마크·거래비용을 같이 보는 점도 맞습니다.** 단순 수익률만 보면 회전율 높은 전략이 과대평가됩니다.",
        "- **단기투자 축 설계는 이전보다 좋아졌습니다.** ORB, VWAP, 거래량, 호가 불균형, 뉴스 축을 두고 EMA/RSI/BB는 낮은 가중으로 둔 점은 합리적입니다.",
    ])

    lines.append("")
    lines.append("#### 지금 가장 약한 부분")
    lines.extend(_logic_weaknesses(paper_eval, intraday_eval, market_read))

    lines.append("")
    lines.append("#### 바로 바꿔야 할 기준")
    lines.extend([
        "1. **단기투자는 표본 100건 전까지 채택 금지**  현재처럼 표본이 적으면 승률이나 순손익은 거의 의미가 없습니다.",
        "2. **목표 지표를 수익률보다 R-multiple로 통일**  단기투자는 종목 가격대가 달라서 `+원/$`보다 `+R`, `max adverse excursion`, `time stop`이 더 중요합니다.",
        "3. **매수·매도 횟수 제한 대신 일손실 정지 유지**  횟수 제한은 엣지를 막을 수 있고, 손실한도는 계좌 생존을 지킵니다. 방향은 맞습니다.",
        "4. **시장 게이트를 보조 신호로 낮추기**  지금처럼 MIXED/지정학을 매번 말하는 건 과합니다. 단기 체결 판단은 가격·거래량·VWAP·손절폭이 1순위여야 합니다.",
        "5. **실패 원인 태깅을 자동화**  실패한 거래마다 `진입축 실패`, `청산 지연`, `뉴스 역방향`, `손절폭 과소`, `유동성 부족` 중 하나를 붙여야 학습이 됩니다.",
    ])

    lines.append("")
    lines.append("#### 결론")
    lines.append(
        "**모의투자 로직은 관찰 가능한 시스템으로는 괜찮지만 아직 채택 판정 전이고, 단기투자 로직은 설계는 좋아졌지만 "
        "표본이 너무 적어서 돈 버는 로직이라고 부르면 안 됩니다.** 지금 해야 할 일은 더 보수적으로 막는 게 아니라, "
        "손실한도 안에서 충분히 많이 체결시키고 실패 원인을 축별로 쌓는 것입니다."
    )

    lines.append("")
    lines.append("#### 현재 읽은 데이터")
    lines.append(f"- 모의투자: {_paper_data_line(paper_eval)}")
    lines.append(f"- 단기투자: {_intraday_data_line(intraday_eval)}")
    if reports:
        lines.append(f"- 최신 리포트: {reports[0].get('name')} ({reports[0].get('mtime')})")
    return "\n".join(lines)


def _paper_logic_eval(paper: dict) -> dict:
    accounts = []
    for key, label in (("kr", "KR 모의"), ("us", "US 모의")):
        item = paper.get(key)
        if not isinstance(item, dict):
            continue
        scorecard = item.get("scorecard") or {}
        cost = item.get("cost") or {}
        accounts.append({
            "key": key,
            "label": label,
            "nav": item.get("nav"),
            "cum_ret": item.get("cum_ret"),
            "strat_mdd": item.get("strat_mdd"),
            "bench_ret": item.get("bench_ret"),
            "bench_mdd": item.get("bench_mdd"),
            "cost_drag": cost.get("drag"),
            "turnover": cost.get("turnover"),
            "buy_hit": scorecard.get("buy_hit"),
            "n_buy": scorecard.get("n_buy") or 0,
            "sell_hit": scorecard.get("sell_hit"),
            "n_sell": scorecard.get("n_sell") or 0,
            "decision_n": len(item.get("decisions") or []),
            "sleeve": item.get("sleeve"),
        })
    matured = sum(a["n_buy"] + a["n_sell"] for a in accounts)
    decision_n = sum(a["decision_n"] for a in accounts)
    max_mdd_gap = max([
        _num(a["strat_mdd"], 0) - _num(a["bench_mdd"], 0)
        for a in accounts
        if a.get("strat_mdd") is not None and a.get("bench_mdd") is not None
    ] or [0])
    max_turnover = max([_num(a.get("turnover"), 0) for a in accounts] or [0])
    return {
        "accounts": accounts,
        "matured": matured,
        "decision_n": decision_n,
        "max_mdd_gap": max_mdd_gap,
        "max_turnover": max_turnover,
    }


def _intraday_logic_eval(ml_rows: list[dict]) -> dict:
    file_counts = Counter(row.get("_file", "unknown") for row in ml_rows)
    decisions = [r for r in ml_rows if "intraday" in str(r.get("_file", "")).lower()
                 and "decision" in str(r.get("_file", "")).lower()]
    outcomes = [r for r in ml_rows if "intraday" in str(r.get("_file", "")).lower()
                and "outcome" in str(r.get("_file", "")).lower()]
    success = [r for r in outcomes if bool(r.get("success") or r.get("correct"))]
    net = sum(_num(r.get("net_pnl"), 0) for r in outcomes)
    r_values = [_num(r.get("r") or r.get("net_r") or r.get("fwd_excess"), None) for r in outcomes]
    r_values = [v for v in r_values if v is not None]
    return {
        "file_counts": file_counts,
        "decision_n": len(decisions),
        "outcome_n": len(outcomes),
        "success_n": len(success),
        "hit": (len(success) / len(outcomes) * 100.0) if outcomes else None,
        "net": net,
        "avg_r": (sum(r_values) / len(r_values)) if r_values else None,
    }


def _trading_verdict(paper_eval: dict, intraday_eval: dict) -> dict:
    if intraday_eval["outcome_n"] < 30 and paper_eval["matured"] < 30:
        return {
            "label": "OBSERVE - 표본 부족",
            "summary": "구조는 좋아졌지만 아직 돈 버는 로직으로 판정할 만큼 성숙한 표본이 없습니다.",
        }
    if paper_eval["max_mdd_gap"] > 0.5:
        return {
            "label": "CAUTION - 낙폭 관리 미흡",
            "summary": "성과보다 먼저 MDD가 벤치보다 깊어지는 구간을 줄여야 합니다.",
        }
    return {
        "label": "WATCH - 검증 진행",
        "summary": "로직은 운영 가능한 형태지만, 채택은 순손익·MDD·비용·표본을 더 쌓은 뒤 판단해야 합니다.",
    }


def _logic_rows(paper_eval: dict, intraday_eval: dict, market_read: dict) -> list[dict]:
    paper_reason = f"결정 {paper_eval['decision_n']}건, 성숙 표본 {paper_eval['matured']}건"
    if paper_eval["max_mdd_gap"] > 0:
        paper_reason += f", MDD가 벤치보다 최대 {_fmt_pp(paper_eval['max_mdd_gap'])} 깊음"
    else:
        paper_reason += ", MDD 비교는 크게 나쁘지 않음"

    intraday_reason = f"outcome {intraday_eval['outcome_n']}건"
    if intraday_eval["hit"] is not None:
        intraday_reason += f", 승률 {_fmt_pct(intraday_eval['hit'])}"
    else:
        intraday_reason += ", 승률 미성숙"

    return [
        {"area": "중장기 모의투자", "verdict": "관찰 가능하지만 채택 전", "reason": paper_reason},
        {"area": "단기투자", "verdict": "설계는 합리적, 표본 부족", "reason": intraday_reason},
        {"area": "위험관리", "verdict": "방향 맞음", "reason": "횟수 제한보다 일손실 정지·포지션 리스크 예산으로 관리하는 구조가 맞음"},
        {"area": "시장 게이트", "verdict": "보조로 낮춰야 함", "reason": f"현재 {market_read['status']} / {market_read['top_theme']} 판단은 배경이지 단기 진입의 주신호가 아님"},
    ]


def _logic_weaknesses(paper_eval: dict, intraday_eval: dict, market_read: dict) -> list[str]:
    lines = []
    if intraday_eval["outcome_n"] < 100:
        lines.append(f"- **단기투자 표본이 너무 적습니다.** outcome {intraday_eval['outcome_n']}건이면 승률·평균손익이 쉽게 흔들립니다.")
    if paper_eval["matured"] < 50:
        lines.append(f"- **모의투자 성숙 표본도 부족합니다.** 성숙 표본 {paper_eval['matured']}건으로는 feature별 우위를 단정하기 어렵습니다.")
    if paper_eval["max_turnover"] >= 100:
        lines.append(f"- **회전율 비용을 계속 봐야 합니다.** 최대 회전율 {_fmt_pct(paper_eval['max_turnover'])}면 작은 엣지는 비용에 먹힐 수 있습니다.")
    if market_read["top_theme"] == "지정학":
        lines.append("- **시장 설명이 매매 판단을 덮고 있습니다.** 지정학은 리스크 예산 조절에는 유용하지만 단기 체결 트리거가 되면 안 됩니다.")
    if not lines:
        lines.append("- **가장 큰 약점은 아직 채택 기준이 빡빡하게 문서화되지 않은 점입니다.** 언제 shadow에서 실제 모의집행으로 올릴지 기준이 필요합니다.")
    return lines


def _paper_data_line(paper_eval: dict) -> str:
    if not paper_eval["accounts"]:
        return "계좌 요약 없음"
    parts = []
    for a in paper_eval["accounts"]:
        parts.append(
            f"{a['label']} 누적 {_fmt_pct(a.get('cum_ret'))}, MDD {_fmt_pct(a.get('strat_mdd'))}, "
            f"결정 {a['decision_n']}건"
        )
    return " · ".join(parts)


def _intraday_data_line(intraday_eval: dict) -> str:
    counts = intraday_eval["file_counts"]
    source = ", ".join(f"{name} {cnt}건" for name, cnt in counts.items()
                       if "intraday" in str(name).lower()) or "원장 없음"
    hit = "—" if intraday_eval["hit"] is None else _fmt_pct(intraday_eval["hit"])
    return f"{source} · outcome {intraday_eval['outcome_n']}건 · 승률 {hit}"


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _fmt_pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.1f}%" if float(value) < 0 else f"{float(value):.1f}%"
    except Exception:
        return "—"


def _fmt_pp(value) -> str:
    try:
        return f"{float(value):.1f}%p"
    except Exception:
        return "—"


def _market_read(question: str, pack: dict) -> dict:
    surface = pack.get("surface") or "market"
    events = pack.get("sources", {}).get("events") or []
    memory = pack.get("memory") or []
    text = _context_text(events, memory)
    q = str(question or "").lower()

    geo = _keyword_count(text, ["이란", "미군", "쿠웨이트", "호르무즈", "중동", "irgc", "공격", "보복", "전쟁", "폭발"])
    energy = _keyword_count(text, ["유가", "원유", "wti", "brent", "석유", "에너지"])
    credit = _keyword_count(text, ["회사채", "신용", "대출", "hyg", "lqd", "금리", "부채", "스프레드"])
    currency = _keyword_count(text, ["달러", "dxy", "위안", "엔", "환율", "강달러"])
    growth = _keyword_count(text, ["ai", "반도체", "제조업", "실적", "밸류에이션", "모델", "spacex", "anthropic"])
    policy = _keyword_count(text, ["트럼프", "연준", "fed", "정책", "관세", "규제", "iaea", "은행"])

    liquidity_score = _clamp(52 + currency * 3 - credit * 2 - geo + growth, high=86)
    policy_score = _clamp(48 + policy * 5 + credit * 2, high=86)
    geo_score = _clamp(44 + geo * 8 + energy * 3, high=88)
    credit_score = _clamp(48 + credit * 6 + currency * 2, high=88)

    if geo_score >= 78 and (liquidity_score <= 42 or credit_score >= 70):
        status = "RISK-OFF"
    elif growth >= 3 and geo_score < 62 and credit_score < 62:
        status = "RISK-ON"
    else:
        status = "MIXED"

    top_theme = _top_theme({
        "지정학": geo + energy,
        "크레딧/환율": credit + currency,
        "AI/성장": growth,
        "정책": policy,
    })
    title = _SURFACE_TITLES.get(surface, _SURFACE_TITLES["market"])
    if "로직" in q and surface in {"portfolio", "paper", "lab"}:
        title = f"{title} - 실행 조건"

    summary = _summary_for(surface, q, status, top_theme)
    narrative = _narrative_for(surface, q, status, top_theme, geo_score, liquidity_score, credit_score)
    signals = [
        {"name": "유동성", "score": liquidity_score, "text": _liquidity_text(liquidity_score, currency, credit)},
        {"name": "정책", "score": policy_score, "text": _policy_text(policy_score, policy, credit)},
        {"name": "지정학", "score": geo_score, "text": _geo_text(geo_score, geo, energy)},
    ]
    priorities = _priorities_for(surface, top_theme, status, geo_score, credit_score, growth)
    return {
        "title": title,
        "status": status,
        "summary": summary,
        "narrative": narrative,
        "signals": signals,
        "priorities": priorities,
        "top_theme": top_theme,
    }


def _context_text(events: list[dict], memory: list[dict]) -> str:
    chunks = []
    for row in events[:16]:
        chunks.append(str(row.get("title") or row.get("summary") or row.get("body") or ""))
    for row in memory[:12]:
        chunks.append(str(row.get("title") or row.get("body") or ""))
    return " ".join(chunks).lower()


def _keyword_count(text: str, keywords: list[str]) -> int:
    return sum(text.count(word.lower()) for word in keywords)


def _clamp(value: int | float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def _top_theme(scores: dict[str, int]) -> str:
    if not scores:
        return "혼합"
    name, value = max(scores.items(), key=lambda item: item[1])
    return name if value > 0 else "혼합"


def _summary_for(surface: str, question: str, status: str, top_theme: str) -> str:
    if "로직" in question and surface in {"portfolio", "paper", "lab"}:
        return ("로직은 방향이 맞습니다. 다만 지금은 종목 점수보다 시장의 1순위 위험이 어디에 있는지 먼저 정하고, "
                "그 위험이 완화될 때만 공격 비중이나 레버리지를 켜는 구조가 더 안전합니다.")
    if top_theme == "지정학":
        return ("현재 핵심은 중동 꼬리위험이 다시 가격에 들어오는지입니다. 성장주 반등보다 유가, 달러, 크레딧이 "
                "같이 안정되는지가 먼저 확인돼야 합니다.")
    if top_theme == "크레딧/환율":
        return ("시장은 위험이 해소된 장이라기보다 위험의 위치가 금리·달러·크레딧 쪽으로 이동한 장에 가깝습니다.")
    if top_theme == "AI/성장":
        return ("AI·성장 테마의 긍정 신호는 남아 있지만, 매수 판단은 밸류에이션과 크레딧 확인 뒤로 두는 편이 좋습니다.")
    if status == "RISK-ON":
        return "현재 데이터만 보면 위험 선호가 우세합니다. 그래도 모의투자에서는 손실한도와 회전율을 같이 봐야 합니다."
    return "현재 국면은 한쪽으로 단정하기 어렵습니다. 공격과 방어 신호가 섞인 혼합 장으로 보는 게 맞습니다."


def _narrative_for(surface: str, question: str, status: str, top_theme: str,
                   geo_score: int, liquidity_score: int, credit_score: int) -> str:
    base = (
        f"제가 보는 현재 국면은 **{status}**입니다. 표면적으로는 뉴스가 여러 갈래로 흩어져 있지만, "
        f"가장 먼저 볼 축은 **{top_theme}**입니다. "
    )
    if surface == "portfolio" or "로직" in question:
        return (
            base
            + "포트폴리오 로직은 지금처럼 이벤트가 많은 날에 종목별 점수를 바로 매수 신호로 쓰기보다, "
            "시장 위험 예산을 먼저 잠그고 그 안에서 공격 자산을 켜는 방식이 더 좋습니다. "
            f"지정학 점수 {geo_score}, 크레딧 압력 {credit_score}, 유동성 {liquidity_score} 조합이면 "
            "QQQ·반도체 반등만 보고 비중을 키우기보다는 HYG/LQD, 달러, 유가가 같이 진정되는지 확인해야 합니다."
        )
    if surface == "paper":
        return (
            base
            + "모의투자에서는 이 국면을 진입 횟수 제한으로 막기보다, 손실한도 안에서 신호가 틀렸을 때 얼마나 빨리 "
            "노출을 줄이는지가 핵심입니다. 즉, 거래를 막는 로직보다 실패 시 손실을 제한하는 로직이 더 중요합니다."
        )
    return (
        base
        + "따라서 오늘의 핵심 질문은 '좋은 뉴스가 있나'가 아니라 '위험자산이 올라갈 때 크레딧과 달러가 같이 허락하나'입니다. "
        "그 확인 없이 성장주 반등만 따라가면 반등의 질을 잘못 읽을 수 있습니다."
    )


def _liquidity_text(score: int, currency: int, credit: int) -> str:
    if score >= 65:
        return "달러·크레딧 부담이 낮아 위험자산에 우호적인 편입니다."
    if score <= 42:
        return "달러 또는 크레딧 압력이 남아 있어 공격 비중 확대는 확인이 필요합니다."
    if currency or credit:
        return "강달러·대출·크레딧 단서가 있어 유동성은 중립보다 약간 조심스럽습니다."
    return "유동성 단서는 중립입니다. 단독 매수 근거로 쓰기엔 부족합니다."


def _policy_text(score: int, policy: int, credit: int) -> str:
    if score >= 65:
        return "정책·은행·규제 변수가 가격 판단에 크게 개입하는 구간입니다."
    if policy or credit:
        return "정책/은행권 단서가 있어 금리 인하 기대를 단순화하면 안 됩니다."
    return "정책 변수는 아직 보조 신호입니다."


def _geo_text(score: int, geo: int, energy: int) -> str:
    if score >= 72:
        return "중동·에너지 꼬리위험이 1순위입니다. 휴전 감시가 아니라 재교전 확인 국면입니다."
    if geo or energy:
        return "지정학·유가 단서가 있어 방어 프리미엄을 일부 남겨야 합니다."
    return "지정학 위험은 전면 신호가 아닙니다."


def _priorities_for(surface: str, top_theme: str, status: str, geo_score: int,
                    credit_score: int, growth: int) -> list[dict]:
    priorities = []
    if geo_score >= 64 or top_theme == "지정학":
        priorities.append({
            "title": "중동 재교전",
            "body": "미국·이란 공식 발표, 호르무즈 통항, 보험료, Brent-WTI 스프레드, 방산주 반응을 먼저 봅니다.",
        })
    if credit_score >= 58:
        priorities.append({
            "title": "크레딧 확인",
            "body": "주식 반등이 나와도 HYG/LQD가 따라오지 않으면 반등 신뢰도는 낮게 둡니다.",
        })
    if growth:
        priorities.append({
            "title": "AI/반도체 밸류에이션",
            "body": "QQQ, SMH, SOXX, NVDA, MU는 수요 스토리보다 CAPEX·전력비·마진 둔화 여부가 더 중요합니다.",
        })
    if surface in {"portfolio", "paper", "lab"}:
        priorities.append({
            "title": "손실한도 기반 실행",
            "body": "매수·매도 횟수보다 한 번 틀렸을 때 계좌 손실이 어디서 멈추는지 먼저 고정해야 합니다.",
        })
    if not priorities:
        priorities.append({
            "title": "데이터 축적",
            "body": "수집 이벤트가 아직 약합니다. 새 뉴스와 모의 원장이 쌓인 뒤 판단 강도를 높이는 편이 낫습니다.",
        })
    return priorities[:3]


def _format_kst(value: str | None) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M KST")


def _next_questions(surface: str, read: dict | None = None) -> list[str]:
    top_theme = (read or {}).get("top_theme", "혼합")
    base = {
        "market": [
            f"- 지금 1순위인 {top_theme} 신호가 어떤 가격에서 확인돼?",
            "- 주식 반등이 진짜인지 HYG/LQD·달러·유가로 검증해줘",
        ],
        "portfolio": [
            "- 이 국면에서 공격 비중을 켜는 조건과 끄는 조건을 나눠줘",
            "- 최대손실 1% 안에서 QQQ/레버리지/현금 비중을 다시 짜줘",
        ],
        "paper": [
            "- 오늘 단기 트레이딩이 실패하면 어떤 feature가 먼저 틀린 거야?",
            "- 손실한도 안에서 매수·매도를 더 공격적으로 돌릴 조건을 정해줘",
        ],
        "lab": [
            "- 이 전략은 어떤 시장 국면에서만 켜야 해?",
            "- 실패하면 어떤 지표가 먼저 나빠질지 가설로 만들어줘",
        ],
    }
    return base.get(surface, base["market"])
