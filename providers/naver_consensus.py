"""providers/naver_consensus.py — KR 애널리스트 컨센서스 (Naver 모바일 JSON API).

yfinance 의 .KS forward/target 은 실제와 크게 어긋나 신뢰불가 → Naver 는 국내 증권사
컨센서스를 그대로 노출: ① integration API 의 목표주가 평균·투자의견,
② finance/annual API 의 **차기연도 컨센서스 추정**(isConsensus=Y 열 — EPS·ROE·매출 등).

트레일링(DART) 전용이던 KR 가치평가에 진짜 포워드 축을 공급한다 — 고ROE 성장주
(예: SK하이닉스 2026E EPS 컨센서스 fwd PER ~6.6)가 잔여이익 영구모델의 "고평가"
편향으로 오도되던 한계 해소.

JSON API(UTF-8) — Naver HTML EUC-KR 함정 해당 없음. 실패 시 None/{} (graceful).
12h 디스크 캐시: ~/reports/ml-cache/naver_consensus/{code}.json
"""
from __future__ import annotations

import logging
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
        out.update(parse_integration(_fetch_json(f"{_BASE}/{code}/integration")))
    except Exception as e:
        logger.debug("naver integration 실패 %s: %s", code, e)
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
