"""providers/naver_consensus.py — KR 애널리스트 컨센서스 + ETF 핵심지표 (Naver 모바일 JSON API).

yfinance 의 .KS forward/target 은 실제와 크게 어긋나 신뢰불가 → Naver 는 국내 증권사
컨센서스를 그대로 노출: ① integration API 의 목표주가 평균·투자의견,
② finance/annual API 의 **차기연도 컨센서스 추정**(isConsensus=Y 열 — EPS·ROE·매출 등).

트레일링(DART) 전용이던 KR 가치평가에 진짜 포워드 축을 공급한다 — 고ROE 성장주
(예: SK하이닉스 2026E EPS 컨센서스 fwd PER ~6.6)가 잔여이익 영구모델의 "고평가"
편향으로 오도되던 한계 해소.

같은 integration 응답의 etfKeyIndicator 로 국내 ETF 총보수(expense_ratio)도 함께
채운다 — KIS 실계좌 시세 API(providers.kis_quote.get_etf_snapshot)엔 총보수 필드가
없어(라이브 확인) 네이버로 보강(감사 후속). 추가 네트워크 호출 없이 기존 integration
호출에 얹는다.

JSON API(UTF-8) — Naver HTML EUC-KR 함정 해당 없음. 실패 시 None/{} (graceful).
12h 디스크 캐시: ~/reports/ml-cache/naver_consensus/{code}.json
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = "https://m.stock.naver.com/api/stock"
_UA = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path.home() / "reports" / "ml-cache" / "naver_consensus"
CACHE_TTL_H = 12.0


def _code(ticker: str) -> str | None:
    """'000660.KS'/'000660' → 6자리 코드. KR 형식이 아니면 None."""
    base = str(ticker or "").upper().split(".")[0]
    return base if (base.isdigit() and len(base) == 6) else None


def _num(v):
    """Naver 수치 문자열('3,547,917'·'97.49'·'-12,517') → float. 결측 None."""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── 순수 파서 (fixture 테스트 가능) ────────────────────────────────────────────

def parse_integration(d: dict) -> dict:
    """integration 응답 → {target_mean, recomm_mean, asof}. 결측 {}."""
    ci = (d or {}).get("consensusInfo") or {}
    out = {"target_mean": _num(ci.get("priceTargetMean")),
           "recomm_mean": _num(ci.get("recommMean")),
           "asof": ci.get("createDate")}
    return out if out["target_mean"] is not None else {}


def _parse_korean_won(s) -> float | None:
    """'25조 4,325억' 같은 네이버 조/억 표기 → 원 단위 float. 순수 숫자 문자열도 허용.

    KODEX200 라이브 확인: "25조 4,325억" = 25*1e12 + 4325*1e8 = 25,432,500,000,000
    (KIS etf_ntas_ttam 억원 단위 대조값과 정확히 일치).
    """
    if s is None:
        return None
    text = str(s).replace(",", "").strip()
    if not text:
        return None
    total = 0.0
    matched = False
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*조", text)
    if m:
        total += float(m.group(1)) * 1e12
        matched = True
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*억", text)
    if m:
        total += float(m.group(1)) * 1e8
        matched = True
    if matched:
        return total
    try:
        return float(text)
    except ValueError:
        return None


def parse_etf_indicator(d: dict) -> dict:
    """integration 응답의 etfKeyIndicator → {expense_ratio, issuer, total_assets,
    nav, premium_pct}. 결측/비ETF {}.

    totalFee 는 퍼센트 단위(0.15=0.15%) → 분수(0.0015)로 정규화(providers.etf_data.
    norm_expense_ratio 와 동일 관례). deviationRate/-Sign 조합이 괴리율(부호 포함).
    """
    ek = (d or {}).get("etfKeyIndicator") or {}
    if not ek:
        return {}
    out: dict = {}
    fee = _num(ek.get("totalFee"))
    if fee is not None and fee > 0:
        out["expense_ratio"] = fee / 100.0
    issuer = re.sub(r"\s*\(ETF\)\s*$", "", str(ek.get("issuerName") or "").strip())
    if issuer:
        out["issuer"] = issuer
    nav = _num(ek.get("nav"))
    if nav is not None:
        out["nav"] = nav
    deviation = _num(ek.get("deviationRate"))
    if deviation is not None:
        sign = -1 if str(ek.get("deviationSign") or "").strip() == "-" else 1
        out["premium_pct"] = round(deviation * sign, 4)
    total_assets = _parse_korean_won(ek.get("totalNav"))
    if total_assets is not None:
        out["total_assets"] = total_assets
    return out


def parse_annual(d: dict) -> dict:
    """finance/annual 응답 → {actual: {...최근 확정연도}, fwd: {...컨센서스 연도}}.

    trTitleList 의 isConsensus=Y 열이 차기연도 컨센서스. 행: EPS·ROE·PER·BPS·
    주당배당금 등. 결측 {}.
    """
    fi = (d or {}).get("financeInfo") or {}
    titles = fi.get("trTitleList") or []
    rows = fi.get("rowList") or []
    actual_keys = [t["key"] for t in titles if t.get("isConsensus") == "N" and t.get("key")]
    fwd_keys = [t["key"] for t in titles if t.get("isConsensus") == "Y" and t.get("key")]
    if not rows or not (actual_keys or fwd_keys):
        return {}
    a_key = max(actual_keys) if actual_keys else None    # 최근 확정연도 (YYYYMM 정렬)
    f_key = min(fwd_keys) if fwd_keys else None          # 가장 가까운 컨센서스 연도

    _row_map = {"EPS": "eps", "ROE": "roe", "PER": "per", "BPS": "bps",
                "주당배당금": "dps", "당기순이익": "net_income", "매출액": "revenue"}

    def _pick(key):
        got = {}
        if not key:
            return got
        for r in rows:
            name = _row_map.get(str(r.get("title") or "").strip())
            if not name:
                continue
            got[name] = _num(((r.get("columns") or {}).get(key) or {}).get("value"))
        return got

    out = {}
    if a_key:
        out["actual"] = {"year": a_key[:4], **_pick(a_key)}
    if f_key:
        out["fwd"] = {"year": f_key[:4], **_pick(f_key)}
    return out


# ── fetch + 캐시 ───────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 8):
    import requests
    r = requests.get(url, headers=_UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def summary(ticker: str) -> dict:
    """통합 컨센서스 {target_mean, recomm_mean, asof, actual{...}, fwd{...}, source}.

    12h 디스크 캐시 · 실패/비KR {} (graceful — 소비자는 결측 재정규화).
    """
    code = _code(ticker)
    if not code:
        return {}
    from lib import file_cache
    p = CACHE_DIR / f"{code}.json"
    if file_cache.is_fresh(p, CACHE_TTL_H):
        hit = file_cache.read_json(p)
        if isinstance(hit, dict):
            return hit
    out: dict = {}
    try:
        integration = _fetch_json(f"{_BASE}/{code}/integration")
    except Exception as e:
        logger.debug("naver integration 실패 %s: %s", code, e)
        integration = None
    if integration is not None:
        out.update(parse_integration(integration))
        etf = parse_etf_indicator(integration)
        if etf:
            out["etf"] = etf
    try:
        out.update(parse_annual(_fetch_json(f"{_BASE}/{code}/finance/annual")))
    except Exception as e:
        logger.debug("naver annual 실패 %s: %s", code, e)
    if out:
        out["source"] = "naver"
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            file_cache.write_json_atomic(p, out)
        except Exception:
            pass
    return out
