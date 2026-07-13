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
    shared_section = shared_memory.build_context_section(
        {"screen": surface, "query": "stock-report AI 콘솔 컨텍스트 프롬프트", "limit": 4}
    )
    if shared_section:
        lines += ["", shared_section]
    lines += ["", "[화면별 초점]", *[f"- {x}" for x in pack["focus"]]]
    return "\n".join(lines)


def answer(question: str, surface: str = "market") -> dict:
    question = str(question or "").strip()
    surface = str(surface or "market").strip().lower()
    if not question:
        return {"ok": False, "error": "질문을 입력해 주세요."}

    history = storage.list_conversation(limit=12)
    storage.add_conversation("user", question, surface)
    pack = context.context_pack(surface)
    response = _compose_answer(question, pack, history=history)
    storage.add_conversation("assistant", response, surface)
    try:
        shared_memory.append_chat_exchange(question, response, surface)
    except Exception:
        pass
    return {
        "ok": True,
        "answer": response,
        "surface": surface,
        "context": {
            "event_count": len(pack["sources"]["events"]),
            "memory_count": len(pack["memory"]),
            "shared_memory_count": (pack.get("shared_memory") or {}).get("recordCount", 0),
            "source_counts": pack["sources"]["source_counts"],
            "symbol_counts": pack["sources"]["symbol_counts"],
        },
        "conversation": storage.list_conversation(limit=20),
    }


def _compose_answer(question: str, pack: dict, history: list[dict] | None = None) -> str:
    if _is_trading_logic_question(question) or _is_trading_followup(question, history):
        return _compose_trading_logic_answer(question, pack)
    asset = _extract_asset_symbol(question)
    if asset:
        return _compose_asset_opinion_answer(question, pack, history, asset)
    if not _is_market_context_question(question, pack):
        return _compose_general_chat_answer(question, pack, history)

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
    "nvda": ("NVDA", "엔비디아"),
    "mu": ("MU", "마이크론"),
}


def _extract_asset_symbol(question: str) -> tuple[str, str] | None:
    q = str(question or "").strip()
    ql = q.lower()
    intent_words = (
        "어때", "어떰", "어떠", "top", "탑", "티어", "매수", "진입", "목표",
        "가냐", "가능", "전망", "보유", "팔", "살", "+", "롱", "숏",
    )
    if not any(word in ql for word in intent_words):
        return None
    for raw in re.findall(r"\$?[A-Za-z][A-Za-z0-9.-]{1,12}", q):
        token = raw.lstrip("$").lower()
        if token in _ASSET_ALIASES:
            return _ASSET_ALIASES[token]
        if raw.isupper() and 2 <= len(raw) <= 6 and token not in {"top"}:
            return (raw.upper(), raw.upper())
    return None


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
    surface = str((pack or {}).get("surface") or "").lower()
    market_words = (
        "시장", "증시", "종목", "주식", "포트폴리오", "보유", "비중", "수익률", "리스크",
        "뉴스", "이슈", "금리", "유가", "달러", "환율", "vix", "qqq", "spy", "반도체",
        "ai", "레버리지", "mdd", "벤치", "오른", "내린", "상승", "하락", "반등", "조정",
        "매수", "매도", "진입", "청산", "추천", "성과", "왜 올", "왜 떨",
    )
    if any(word in q for word in market_words):
        return True
    return surface in {"portfolio", "paper", "ticker", "lab"} and len(q) <= 80


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
    return _fallback_general_chat(question, pack)


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
    shared_section = shared_memory.build_context_section(
        {
            "screen": pack.get("surface") or "market",
            "query": question,
            "provider": "codex-cli",
            "limit": 6,
        }
    )
    return "\n".join([
        "너는 stock-report 안의 대화형 에이전트다.",
        "사용자는 한국어로 편하게 말한다. 너도 한국어로 자연스럽게 답한다.",
        "투자 데이터가 필요한 질문이면 주어진 컨텍스트를 참고하되, 일반 질문이면 억지로 시장 리포트로 바꾸지 않는다.",
        "공유 메모리는 참고 맥락일 뿐이며 현재 사용자 질문과 화면 컨텍스트가 우선한다.",
        "실제 매매 지시는 피하고, 판단 근거와 확인할 점을 짧고 명확하게 말한다.",
        "모르면 모른다고 말하고, 필요한 정보가 무엇인지 묻는다.",
        "",
        "[최근 대화]",
        *(hist or ["- 없음"]),
        "",
        "[사용 가능한 투자 컨텍스트]",
        *(ctx or ["- 없음"]),
        "",
        shared_section or "[컨텍스트 메모리]\n- 없음",
        "",
        f"[사용자 질문]\n{question}",
        "",
        "답변:",
    ])


def _fallback_general_chat(question: str, pack: dict) -> str:
    q = str(question or "").strip()
    ql = q.lower()
    if any(word in ql for word in ("안녕", "ㅎㅇ", "hello", "hi")):
        return "안녕. 이제 투자 질문뿐 아니라 일반 질문도 대화처럼 받을게요. 필요하면 시장 데이터, 모의투자, 단기 로직 쪽으로 바로 이어서 볼 수 있습니다."
    if "뭐" in ql and any(word in ql for word in ("할 수", "가능", "기능")):
        return (
            "저는 지금 이 프로젝트 안에서 시장 맥락 설명, 모의투자/단기투자 로직 평가, 포트폴리오 시나리오 정리, "
            "코드 변경 방향 설명을 할 수 있습니다. 일반 질문도 답하되, 실시간 정보가 필요한 내용은 현재 수집된 데이터 기준으로 답합니다."
        )
    if "왜" in ql and "반복" in ql:
        return "이전 답변기가 질문 의도보다 화면 맥락을 먼저 봐서 시장 리포트를 반복했습니다. 이제 범위 밖 질문은 일반 챗봇 답변으로, 투자 질문은 데이터 기반 답변으로 나누도록 바꿨습니다."
    return (
        f"질문은 이해했습니다: **{q}**\n\n"
        "모델 응답을 바로 받지는 못했지만, 질문을 시장 데이터·포트폴리오·모의투자·단기 트레이딩 중 어디에 연결할지 말해주면 "
        "그 맥락으로 바로 이어서 답하겠습니다."
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
