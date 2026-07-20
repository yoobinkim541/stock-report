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

_THEME_CANDIDATES = {
    "semis": [
        ("AMD", "경쟁사", "GPU 시장 직접 경쟁"),
        ("TSM", "공급망", "파운드리 파트너/공급망"),
        ("AVGO", "같은 테마", "AI·네트워크 반도체 테마"),
        ("ASML", "같은 테마", "노광 장비로 업황 동행"),
        ("MU", "같은 테마", "메모리 업황 동행"),
        ("INTC", "경쟁사", "x86·반도체 경쟁"),
        ("QCOM", "같은 테마", "모바일/AI 반도체 테마"),
        ("AMAT", "공급망", "장비·CAPEX 수혜"),
        ("LRCX", "공급망", "메모리 장비 테마"),
        ("KLAC", "공급망", "장비·검사 테마"),
        ("SMCI", "같은 테마", "AI 서버 인프라 테마"),
        ("MRVL", "같은 테마", "AI 네트워킹/가속기 테마"),
        ("NXPI", "같은 테마", "엣지·자동차 반도체 테마"),
    ],
    "bigtech": [
        ("MSFT", "경쟁사", "클라우드·엔터프라이즈 경쟁"),
        ("GOOGL", "경쟁사", "검색·클라우드 경쟁"),
        ("META", "경쟁사", "광고·AI 플랫폼 경쟁"),
        ("ORCL", "같은 테마", "클라우드/DB 인프라 테마"),
        ("CRM", "같은 테마", "SaaS·기업 소프트웨어 테마"),
        ("NOW", "같은 테마", "워크플로우 SaaS 테마"),
        ("SNOW", "같은 테마", "데이터 클라우드 테마"),
        ("ADBE", "같은 테마", "크리에이티브·AI 소프트웨어 테마"),
        ("AAPL", "대체재", "대형 플랫폼·소비자 생태계 비교"),
    ],
    "consumer": [
        ("AMZN", "경쟁사", "이커머스·클라우드 경쟁"),
        ("WMT", "경쟁사", "유통·소비자 경쟁"),
        ("COST", "같은 테마", "리테일·구독형 소비 테마"),
        ("MCD", "같은 테마", "소비재·브랜드 테마"),
        ("SBUX", "같은 테마", "프리미엄 소비 테마"),
        ("NKE", "같은 테마", "브랜드 소비재 테마"),
        ("PG", "같은 테마", "방어 소비재 테마"),
        ("KO", "같은 테마", "방어 소비재 테마"),
        ("PEP", "같은 테마", "방어 소비재 테마"),
    ],
    "finance": [
        ("JPM", "경쟁사", "대형 은행 경쟁"),
        ("BAC", "경쟁사", "대형 은행 경쟁"),
        ("WFC", "경쟁사", "대형 은행 경쟁"),
        ("GS", "경쟁사", "IB·트레이딩 경쟁"),
        ("MS", "경쟁사", "자산관리·IB 경쟁"),
        ("V", "같은 테마", "결제 네트워크 테마"),
        ("MA", "같은 테마", "결제 네트워크 테마"),
        ("AXP", "같은 테마", "카드·결제 테마"),
        ("C", "경쟁사", "글로벌 은행 경쟁"),
    ],
    "health": [
        ("LLY", "경쟁사", "비만·당뇨 약물 경쟁"),
        ("JNJ", "같은 테마", "대형 헬스케어 테마"),
        ("UNH", "같은 테마", "보험·헬스케어 테마"),
        ("MRK", "같은 테마", "제약 테마"),
        ("ABBV", "같은 테마", "제약 테마"),
        ("PFE", "같은 테마", "제약 테마"),
        ("ABT", "같은 테마", "의료기기 테마"),
        ("TMO", "같은 테마", "연구·진단 테마"),
        ("DHR", "같은 테마", "생명과학 툴 테마"),
    ],
    "industrial": [
        ("BA", "경쟁사", "항공·방산 경쟁"),
        ("LMT", "경쟁사", "방산 경쟁"),
        ("RTX", "경쟁사", "방산·항공우주 경쟁"),
        ("CAT", "같은 테마", "산업재·설비 테마"),
        ("GE", "같은 테마", "산업·항공 테마"),
        ("HON", "같은 테마", "산업 자동화 테마"),
        ("UPS", "같은 테마", "물류·운송 테마"),
    ],
    "ev": [
        ("TSLA", "경쟁사", "전기차 경쟁"),
        ("RIVN", "경쟁사", "전기차 경쟁"),
        ("F", "경쟁사", "전통차·EV 경쟁"),
        ("GM", "경쟁사", "전통차·EV 경쟁"),
        ("UBER", "같은 테마", "모빌리티 테마"),
    ],
    "crypto": [
        ("COIN", "경쟁사", "암호화폐 거래/브로커리지 경쟁"),
        ("MSTR", "같은 테마", "비트코인 민감도 동행"),
        ("MARA", "같은 테마", "비트코인 채굴 테마"),
        ("RIOT", "같은 테마", "비트코인 채굴 테마"),
        ("PYPL", "같은 테마", "디지털 결제 테마"),
    ],
}

_THEME_HINTS = {
    "semis": ("NVDA", "AMD", "반도체", "GPU", "chip", "semi", "semiconductor", "HBM", "AI 서버"),
    "bigtech": ("MSFT", "GOOG", "GOOGL", "META", "AAPL", "ORCL", "클라우드", "SaaS", "AI", "소프트웨어"),
    "consumer": ("AMZN", "WMT", "COST", "소비", "리테일", "유통", "ecommerce", "이커머스"),
    "finance": ("JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "은행", "결제", "카드", "금융"),
    "health": ("LLY", "JNJ", "UNH", "MRK", "ABBV", "PFE", "ABT", "TMO", "DHR", "헬스", "제약", "바이오"),
    "industrial": ("BA", "LMT", "RTX", "CAT", "GE", "HON", "UPS", "산업", "방산", "항공", "물류"),
    "ev": ("TSLA", "RIVN", "F", "GM", "UBER", "전기차", "EV", "모빌리티", "자동차"),
    "crypto": ("COIN", "MSTR", "BTC", "crypto", "암호", "코인", "비트코인", "결제"),
}


def _cand_pool() -> set[str]:
    import ticker_names
    return set(ticker_names.universe())


def _theme_for(ticker: str, name: str = "", context: str = "") -> str | None:
    blob = " ".join([str(ticker or ""), str(name or ""), str(context or "")]).upper()
    for key, hints in _THEME_HINTS.items():
        if any(str(h).upper() in blob for h in hints):
            return key
    return None


def _fallback_related(ticker: str, name: str = "", context: str = "") -> list[dict]:
    import ticker_names
    out: list[dict] = []
    seen: set[str] = set()
    me = ticker_names.normalize_input(ticker) or (ticker or "").upper()
    pool = _cand_pool()

    # ETF 는 같은 그룹 피어를 먼저 사용
    try:
        from etf_meta import peers_of
        for peer in peers_of(me):
            tk = ticker_names.normalize_input(peer) or peer
            if tk and tk not in seen and tk != me:
                out.append({"ticker": tk, "relation": "같은 테마", "reason": "동종 ETF 비교 대상"})
                seen.add(tk)
    except Exception:
        pass

    theme = _theme_for(me, name, context)
    if theme:
        for tk, rel, reason in _THEME_CANDIDATES.get(theme, []):
            ntk = ticker_names.normalize_input(tk)
            if not ntk or ntk == me or ntk in seen or ntk not in pool:
                continue
            out.append({"ticker": ntk, "relation": rel, "reason": reason})
            seen.add(ntk)
            if len(out) >= 5:
                return out[:5]

    # 최후의 일반 후보: 보유/인기 대형주 몇 개
    generic = ["QQQ", "SPY", "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "TSM", "AMD", "JPM", "LLY"]
    for tk in generic:
        ntk = ticker_names.normalize_input(tk)
        if not ntk or ntk == me or ntk in seen or ntk not in pool:
            continue
        out.append({"ticker": ntk, "relation": "같은 테마", "reason": "동반 점검이 잦은 대형 핵심 종목"})
        seen.add(ntk)
        if len(out) >= 5:
            break
    return out[:5]


def _analysis_max_tokens() -> int:
    try:
        return max(256, int(os.getenv("DASH_LLM_RELATED_MAX_TOKENS", "1024")))
    except ValueError:
        return 1024


def _analysis_retry_max_tokens() -> int:
    try:
        return max(128, int(os.getenv("DASH_LLM_RELATED_RETRY_MAX_TOKENS", "384")))
    except ValueError:
        return 384


def _prepare_related_hermes_home(max_tokens: int):
    base_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    base_config = os.path.join(base_home, "config.yaml")
    if not os.path.isfile(base_config):
        return None
    try:
        import yaml
        with open(base_config, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        if not isinstance(cfg, dict):
            return None
        model_cfg = cfg.get("model")
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        model_cfg["max_tokens"] = int(max_tokens)
        cfg["model"] = model_cfg
        overlay = os.path.join(os.path.expanduser("~/.cache"), "stock-report",
                               f"hermes-overlay-{int(max_tokens)}-related")
        os.makedirs(overlay, exist_ok=True)
        tmp_path = os.path.join(overlay, ".config.yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, os.path.join(overlay, "config.yaml"))
        for name in (".env", "auth.json"):
            src = os.path.join(base_home, name)
            dst = os.path.join(overlay, name)
            if os.path.exists(src) and not os.path.lexists(dst):
                os.symlink(src, dst)
        return overlay
    except Exception:
        return None


def _run_related_llm(cmd, runner, timeout, max_tokens):
    env = None
    overlay_home = _prepare_related_hermes_home(max_tokens)
    if overlay_home:
        env = dict(os.environ)
        env["HERMES_HOME"] = overlay_home
    return runner(cmd, capture_output=True, text=True, timeout=timeout, env=env)


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

    상태: "ok" | "cached" | "fallback" | "disabled" | "call failed: …" | "empty".
    """
    if os.getenv("DASH_LLM_RELATED_ENABLED", "1").lower() in ("0", "false", "no"):
        return None, "disabled"
    cp = _cache_path(ticker)
    if not force and file_cache.is_fresh(cp, CACHE_TTL_H):
        cached = file_cache.read_json(cp)
        if isinstance(cached, list) and cached:
            return cached, "cached"
    prompt = build_prompt(ticker, name or ticker, context)
    provider = _env("DASH_LLM_RELATED_PROVIDER",
                    _env("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex"))
    model = _env("DASH_LLM_RELATED_MODEL",
                 _env("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini"))
    cmd = ["hermes", "chat", "-q", prompt,
           "--provider", provider,
           "--model", model,
           "-Q"]
    try:
        timeout = max(20, int(os.getenv("DASH_LLM_RELATED_TIMEOUT", "60")))
    except ValueError:
        timeout = 60
    max_tokens = _analysis_max_tokens()
    first_error = ""
    try:
        res = _run_related_llm(cmd, runner, timeout, max_tokens)
    except Exception as exc:
        res = None
        first_error = f"call failed: {str(exc)[:80]}"
    else:
        first_error = ""
    if res is not None and getattr(res, "returncode", 1) != 0:
        stderr_text = str(getattr(res, "stderr", "") or "")
        retry_tokens = _analysis_retry_max_tokens()
        if "402" in stderr_text and retry_tokens < max_tokens:
            try:
                res = _run_related_llm(cmd, runner, timeout, retry_tokens)
            except Exception as exc:
                first_error = f"call failed: {str(exc)[:80]}"
            else:
                first_error = ""
    if res is not None and getattr(res, "returncode", 1) == 0:
        items = parse_related(getattr(res, "stdout", "") or "", ticker)
        if items:
            try:
                file_cache.harden_dir(CACHE_DIR)
                _tmp = cp.with_suffix(".tmp")
                _tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                os.replace(_tmp, cp)
            except Exception:
                pass
            return items, "ok"

    fallback = _fallback_related(ticker, name, context)
    if fallback:
        try:
            file_cache.harden_dir(CACHE_DIR)
            _tmp = cp.with_suffix(".tmp")
            _tmp.write_text(json.dumps(fallback, ensure_ascii=False), encoding="utf-8")
            os.replace(_tmp, cp)
        except Exception:
            pass
        if first_error:
            return fallback, f"fallback ({first_error})"
        return fallback, "fallback"

    if first_error:
        return None, first_error
    if res is not None and getattr(res, "returncode", 1) != 0:
        return None, f"call failed: {str(getattr(res, 'stderr', ''))[:80] or 'non-zero exit'}"
    return None, "empty"
