"""providers/market_valuation.py — S&P500 지수 레벨 밸류에이션 (하이브리드).

PER 헤드라인 = **multpl.com** (S&P 보고이익 GAAP 기준·1871~ 월별 → 역사 백분위 맥락)
+ fPER·EPS 성장·PEG = 상위 100 시총가중 상향 집계 (multpl 에 forward 계열이 없음).
두 PER 은 이익 정의가 달라(보고 vs yfinance trailing) 수치가 다름 — 출처 병기.

지수 자체(^GSPC)와 SPY 는 forward PE 를 안 주므로, 시총 상위 구성종목의
trailing/forward PE 를 **시총가중 조화평균**으로 집계한다:
  지수 PER = Σ시총 / Σ(시총/PER) = Σ시총 / Σ이익   (조화평균 — 지수 PE 의 정의와 일치)
  EPS 성장률 = Σ이익_fwd / Σ이익_ttm − 1 · PEG = PER ÷ 성장률(%) (교과서식)

상위 100 종목(지수 시총의 ~70%) 커버리지 — 정직 라벨 병기. 12h 디스크 캐시.
순수 집계(aggregate_index_valuation)는 무네트워크 테스트.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path.home() / "reports" / "ml-cache" / "sp500_valuation.json"
CACHE_TTL_S = 12 * 3600
TOP_N = 100


def aggregate_index_valuation(rows: list[dict]) -> dict:
    """[{cap, per, fper}] → 지수 PER·fPER·EPS 성장·PEG (순수 — 시총가중 조화평균).

    per/fper ≤0·결측 행은 해당 합계에서 제외(커버리지 별도 보고). 반환 {} = 재료 부족.
    """
    cap_t = e_ttm = 0.0                 # trailing 커버 시총·이익
    cap_f = e_fwd = 0.0                 # forward 커버 시총·이익
    total_cap = sum(float(r.get("cap") or 0) for r in rows)
    for r in rows:
        cap = float(r.get("cap") or 0)
        if cap <= 0:
            continue
        per, fper = r.get("per"), r.get("fper")
        if per and per > 0:
            cap_t += cap
            e_ttm += cap / float(per)
        if fper and fper > 0:
            cap_f += cap
            e_fwd += cap / float(fper)
    if e_ttm <= 0 or e_fwd <= 0 or total_cap <= 0:
        return {}
    per_idx = cap_t / e_ttm
    fper_idx = cap_f / e_fwd
    # 성장률은 양쪽 모두 커버된 시총 기준이 정확하지만, 커버리지 차이가 작아
    # (대형주 대부분 양쪽 존재) 단순 이익합 비율로 근사 — 커버리지 병기로 정직 보정
    growth = (e_fwd / max(e_ttm, 1e-9) * (cap_t / max(cap_f, 1e-9)) - 1) * 100
    return {
        "per": round(per_idx, 1), "fper": round(fper_idx, 1),
        "eps_growth_pct": round(growth, 1),
        "peg": round(per_idx / growth, 2) if growth > 0 else None,
        "n": len(rows),
        "cov_trailing_pct": round(cap_t / total_cap * 100, 1),
        "cov_forward_pct": round(cap_f / total_cap * 100, 1),
    }




# ── multpl.com — 보고이익 기준 PER + 역사 백분위 (순수 파서 + graceful fetch) ──
import re as _re

_MULTPL_URL = "https://www.multpl.com/s-p-500-pe-ratio"


def parse_multpl_current(html: str):
    """div#current 블록의 현재 PER (순수). 실패 None."""
    m = _re.search(r'id="current".*?</b>\s*([\d.]+)', html, _re.S)
    try:
        return float(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def parse_multpl_table(html: str) -> list:
    """월별 테이블 → [(date_str, value)] 최신순 (순수)."""
    out = []
    # 값 셀 앞에 <abbr>†</abbr>(추정치)·&#x2002;(공백 엔티티) 등이 끼어듦 — 모두 건너뜀
    for m in _re.finditer(r"<td>([A-Z][a-z]{2} \d+, \d{4})</td>\s*<td>\s*"
                          r"(?:(?:<[^>]+>[^<]*</[^>]+>|&#x[0-9a-fA-F]+;)\s*)*([\d.]+)",
                          html):
        try:
            out.append((m.group(1), float(m.group(2))))
        except ValueError:
            continue
    return out


def hist_percentile(values: list, current: float):
    """현재값의 역사 백분위 (0~100 · 값 이하 비율). 순수."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals or current is None:
        return None
    return round(sum(1 for v in vals if v <= current) / len(vals) * 100, 1)


def fetch_multpl_pe() -> dict:
    """multpl 현재 PER + 역사 백분위(전체·최근 20년). 실패 {} (graceful)."""
    try:
        import requests
        ua = {"User-Agent": "Mozilla/5.0"}
        cur = parse_multpl_current(
            requests.get(_MULTPL_URL, headers=ua, timeout=15).text)
        if cur is None:
            return {}
        rows = parse_multpl_table(
            requests.get(_MULTPL_URL + "/table/by-month", headers=ua, timeout=15).text)
        vals = [v for _, v in rows]
        return {"per_reported": cur,
                "per_pctile_all": hist_percentile(vals, cur),
                "per_pctile_20y": hist_percentile(vals[:240], cur),
                "hist_n": len(vals)}
    except Exception:
        return {}


def _fetch_rows(top_n: int = TOP_N) -> list[dict]:
    import yfinance as yf
    from sp500_meta import MARKET_CAP
    tks = [t for t, _ in sorted(MARKET_CAP.items(), key=lambda x: -x[1])[:top_n]]

    def one(t):
        try:
            i = yf.Ticker(t).info or {}
            return {"cap": MARKET_CAP.get(t, 0),
                    "per": i.get("trailingPE"), "fper": i.get("forwardPE")}
        except Exception:
            return {"cap": MARKET_CAP.get(t, 0), "per": None, "fper": None}

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, tks))


def sp500_valuation(top_n: int = TOP_N) -> dict:
    """지수 밸류 집계 — 12h 디스크 캐시. 실패/재료 부족 {} (graceful)."""
    try:
        if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
            cached = json.loads(CACHE.read_text())
            if "per_reported" not in cached:            # 구 캐시 self-heal — multpl 만 보강
                cached.update(fetch_multpl_pe())
                if "per_reported" in cached:
                    CACHE.write_text(json.dumps(cached, ensure_ascii=False))
            return cached
    except Exception:
        pass
    try:
        out = aggregate_index_valuation(_fetch_rows(top_n))
        out.update(fetch_multpl_pe())
        if out:
            out["asof"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, ensure_ascii=False))
            tmp.replace(CACHE)
        return out
    except Exception:
        return {}
