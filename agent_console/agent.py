from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from . import context, storage

_KST = timezone(timedelta(hours=9))

_SURFACE_TITLES = {
    "market": "현재 시장 상황 인식",
    "portfolio": "포트폴리오 로직 점검",
    "ticker": "종목 맥락 판단",
    "paper": "모의투자 로직 점검",
    "lab": "전략 가설 점검",
}


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
