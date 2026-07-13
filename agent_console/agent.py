from __future__ import annotations

from collections import Counter

from . import context, storage


def build_context_prompt(surface: str = "market") -> str:
    pack = context.context_pack(surface)
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
    lines += ["", "[화면별 초점]", *[f"- {x}" for x in pack["focus"]]]
    return "\n".join(lines)


def answer(question: str, surface: str = "market") -> dict:
    question = str(question or "").strip()
    surface = str(surface or "market").strip().lower()
    if not question:
        return {"ok": False, "error": "질문을 입력해 주세요."}

    storage.add_conversation("user", question, surface)
    pack = context.context_pack(surface)
    response = _compose_answer(question, pack)
    storage.add_conversation("assistant", response, surface)
    return {
        "ok": True,
        "answer": response,
        "surface": surface,
        "context": {
            "event_count": len(pack["sources"]["events"]),
            "memory_count": len(pack["memory"]),
            "source_counts": pack["sources"]["source_counts"],
            "symbol_counts": pack["sources"]["symbol_counts"],
        },
        "conversation": storage.list_conversation(limit=20),
    }


def _compose_answer(question: str, pack: dict) -> str:
    events = pack["sources"]["events"]
    memory = pack["memory"]
    reports = pack["reports"]
    ml_rows = pack["ml_activity"]
    source_counts = pack["sources"]["source_counts"]
    symbol_counts = pack["sources"]["symbol_counts"]
    surface = pack["surface"]

    lines = [f"### {surface} 컨텍스트 답변", ""]
    lines.append(_headline(question, events, memory))
    lines.append("")
    lines.append("**지금 보이는 흐름**")
    if events:
        for item in events[:5]:
            title = item.get("title") or item.get("summary") or "(제목 없음)"
            lines.append(f"- {item.get('source', 'source')} · {title}")
    else:
        lines.append("- 최근 수집 이벤트가 아직 부족합니다. 먼저 memory ingest를 실행하는 게 좋습니다.")

    lines.append("")
    lines.append("**누적 기억에서 이어지는 단서**")
    if memory:
        for item in memory[:5]:
            lines.append(f"- {item.get('kind')} · {item.get('title')}")
    else:
        lines.append("- 아직 World Memory가 비어 있습니다. 최근 리포트/뉴스/모의 원장을 먼저 적재하세요.")

    lines.append("")
    lines.append("**정량/검증 레이어**")
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
    lines.append("**다음에 물어보면 좋은 질문**")
    lines.extend(_next_questions(surface))
    return "\n".join(lines)


def _headline(question: str, events: list[dict], memory: list[dict]) -> str:
    q = question.lower()
    if "어디서" in q or "시작" in q or "왜" in q:
        return "최근 이벤트와 누적 기억을 시간순으로 묶어 원인-전개-현재 상태를 추적하는 질문으로 이해했습니다."
    if "포트폴리오" in q or "비중" in q:
        return "포트폴리오 관점에서는 수익률보다 손실한도, 상관, 현금/레버리지 사용 조건을 먼저 봐야 합니다."
    if "실패" in q or "성공" in q:
        return "성공/실패 평가는 추천 당시 맥락과 20/60거래일 outcome을 같이 봐야 합니다."
    return f"질문: {question}"


def _next_questions(surface: str) -> list[str]:
    base = {
        "market": [
            "- 이 변화가 금리/달러/VIX 중 어디에서 먼저 시작됐나?",
            "- 같은 뉴스가 보유종목과 모의투자에 어떤 경로로 영향을 주나?",
        ],
        "portfolio": [
            "- 이 비중 조합의 최대손실 예산은 얼마인가?",
            "- 레버리지/현금/방어자산을 어떤 조건에서 바꿀 것인가?",
        ],
        "paper": [
            "- 최근 모의투자 손익은 모델 점수 때문인가, 거래비용/회전율 때문인가?",
            "- 성공한 편입과 실패한 편입의 공통 feature는 무엇인가?",
        ],
        "lab": [
            "- 이 전략은 어떤 시장 국면에서만 켜야 하나?",
            "- 실패하면 어떤 지표가 먼저 나빠질 것으로 예상하나?",
        ],
    }
    return base.get(surface, base["market"])

