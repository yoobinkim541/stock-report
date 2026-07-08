"""providers/etf_compare.py — ETF 비교·점수 데이터층 (TR/PR·피어 지표·1~100 점수).

- TR(배당재투자) = yf.download(auto_adjust=False) 의 Adj Close · PR(가격) = raw Close.
  (현 차트 경로의 조정종가 = TR 근사 — QYLD 3y TR +46.9% vs PR +1.9% 실측)
- "추종지수 대비" 벤치마크 = 그룹 대표 ETF TR 프록시 (etf_meta.bench — TR 지수
  원천 ^XNDX 등이 yfinance 에서 불안정. 표시 시 '프록시' 정직 병기)
- 점수 = 동종그룹 내 백분위 가중합(전략별 가중치) — 표시·참고용, 매매신호 아님.
- 수익률/MDD/추적차 필드 단위 = **percent** (tr_1y=12.3 → +12.3%).
  expense_ratio 만 etf_summary 관례 유지 = decimal (0.0068 → 0.68%).

순수부(지표·백분위·점수)는 무네트워크 — tests/test_etf_compare.py 가 합성 데이터로
검증. 네트워크부(peer_report)는 그룹 단위 12h 디스크 캐시(etf_data 패턴)·graceful.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import etf_meta
from ml.adaptive.reward import max_drawdown
from providers.etf_data import etf_summary, normalize_ticker

CACHE_DIR = Path.home() / "reports" / "ml-cache"
CACHE_TTL_S = 12 * 3600

# 전략별 점수 가중치 (합 100) — index 는 추적효율, 인컴 전략은 분배 지속성으로 대체
SCORE_WEIGHTS: dict[str, dict[str, float]] = {
    "index":        {"비용": 25, "성과": 25, "추적": 20, "리스크": 15, "유동성": 15},
    "covered_call": {"비용": 20, "성과": 30, "인컴": 25, "리스크": 15, "유동성": 10},
    "dividend":     {"비용": 25, "성과": 30, "인컴": 20, "리스크": 15, "유동성": 10},
}


# ── 순수 계산부 (무네트워크) ──────────────────────────────────────────────────

def _extract_tr_pr(df, ticker: str):
    """yf.download 결과(단일/MultiIndex 프레임) → (tr, pr) 종가 시리즈. 실패 None.

    auto_adjust=False 기준: Adj Close=TR(분배 재투자 근사)·Close=PR(가격만).
    """
    if df is None or getattr(df, "empty", True):
        return None
    cols = df.columns
    try:
        if getattr(cols, "nlevels", 1) > 1:                     # MultiIndex (배치 다운로드)
            lv0 = set(cols.get_level_values(0))
            if ticker in lv0:                                    # group_by="ticker"
                sub = df[ticker]
            else:                                                # group_by="column" (필드 우선)
                sub = df.xs(ticker, axis=1, level=1)
            tr, pr = sub.get("Adj Close"), sub.get("Close")
        else:
            tr, pr = df.get("Adj Close"), df.get("Close")
        if tr is None or pr is None:
            return None
        tr, pr = tr.dropna(), pr.dropna()
        if len(tr) < 2 or len(pr) < 2:
            return None
        return tr, pr
    except Exception:
        return None


def window_return(s, days: int):
    """마지막 봉 기준 최근 days 구간 수익률(%) — 커버리지 <60% 면 None(짧은 이력 정직)."""
    import pandas as pd
    s = s.dropna()
    if s is None or len(s) < 2:
        return None
    anchor = s.index[-1] - pd.Timedelta(days=int(days))
    win = s[s.index >= anchor]
    if len(win) < 2:
        return None
    covered = (win.index[-1] - win.index[0]).days
    if covered < 0.6 * days:
        return None
    base = float(win.iloc[0])
    if base <= 0:
        return None
    return (float(win.iloc[-1]) / base - 1.0) * 100.0


def ann_return(total_ret_pct: float, years: float):
    """누적 수익률(%) → 연율화(%) (기하)."""
    if total_ret_pct is None or years <= 0:
        return None
    base = 1.0 + total_ret_pct / 100.0
    if base <= 0:
        return None
    return (base ** (1.0 / years) - 1.0) * 100.0


def mdd_pct(tr, days: int = 365 * 3):
    """TR 시리즈 최근 days 창의 MDD(%) + 실제 창 길이(년). 데이터 부족 시 가용 전체."""
    import pandas as pd
    tr = tr.dropna()
    if len(tr) < 2:
        return None, None
    win = tr[tr.index >= tr.index[-1] - pd.Timedelta(days=days)]
    if len(win) < 2:
        win = tr
    window_y = (win.index[-1] - win.index[0]).days / 365.25
    return float(max_drawdown(list(win.values)) * 100.0), round(window_y, 1)


def compute_metrics(prices: dict, group: dict, extras: dict) -> list[dict]:
    """그룹 멤버별 지표 행 (순수 — prices/extras 주입).

    prices: {ticker: yf.download(auto_adjust=False) 프레임}, extras: {ticker: etf_summary dict}.
    tracking_diff = 자기 TR 연율 − 벤치 TR 연율 (%p, 3y 우선·1y 폴백 — 양쪽 동일 basis).
    """
    rows = []
    for t in group.get("etfs", []):
        pair = _extract_tr_pr(prices.get(t), t)
        ex = extras.get(t) or {}
        dv = ex.get("dividends") or {}
        row = {"ticker": t,
               "expense_ratio": ex.get("expense_ratio"),
               "aum": ex.get("total_assets"),
               "div_yield_pct": dv.get("yield_pct"),
               "div_count_12m": dv.get("count_12m"),
               "tr_1y": None, "tr_3y_ann": None, "pr_1y": None, "pr_3y_ann": None,
               "mdd": None, "mdd_window_y": None, "history_years": None,
               "avg_dollar_vol": None, "tracking_diff": None}
        if pair is not None:
            tr, pr = pair
            row["tr_1y"] = window_return(tr, 365)
            row["pr_1y"] = window_return(pr, 365)
            tr3, pr3 = window_return(tr, 365 * 3), window_return(pr, 365 * 3)
            row["tr_3y_ann"] = ann_return(tr3, 3.0) if tr3 is not None else None
            row["pr_3y_ann"] = ann_return(pr3, 3.0) if pr3 is not None else None
            row["mdd"], row["mdd_window_y"] = mdd_pct(tr)
            row["history_years"] = round((tr.index[-1] - tr.index[0]).days / 365.25, 1)
            df = prices.get(t)
            try:                                                 # 60일 평균 거래대금 (달러/원)
                cols = df.columns
                sub = df[t] if getattr(cols, "nlevels", 1) > 1 and t in set(
                    cols.get_level_values(0)) else df
                vol, cl = sub.get("Volume"), sub.get("Close")
                if vol is not None and cl is not None:
                    dv60 = (cl * vol).dropna().tail(60)
                    if len(dv60):
                        row["avg_dollar_vol"] = float(dv60.mean())
            except Exception:
                pass
        rows.append(row)
    # 추적차 후처리 — 벤치 TR 연율과 동일 basis(3y 우선) 비교. 벤치가 그룹 밖이어도
    # (커버드콜 그룹의 기초지수 프록시) prices 에 주입되면 계산 — 벤치 자신은 자연히 0.
    bench = group.get("bench")
    b_pair = _extract_tr_pr(prices.get(bench), bench)
    b3 = b1 = None
    if b_pair is not None:
        btr = b_pair[0]
        b3raw = window_return(btr, 365 * 3)
        b3 = ann_return(b3raw, 3.0) if b3raw is not None else None
        b1 = window_return(btr, 365)
    for r in rows:
        if r["tr_3y_ann"] is not None and b3 is not None:
            r["tracking_diff"] = r["tr_3y_ann"] - b3
        elif r["tr_1y"] is not None and b1 is not None:
            r["tracking_diff"] = r["tr_1y"] - b1
    return rows


def percentile_rank(values: list, v, higher_better: bool = True):
    """v 의 그룹 내 백분위 (0~1, 자기 포함) — (worse + 0.5·tied)/n. n==1 → 0.5."""
    if v is None:
        return None
    vals = [x for x in values if x is not None]
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return 0.5
    worse = sum(1 for x in vals if (x < v) == higher_better and x != v)
    tied = sum(1 for x in vals if x == v)
    return (worse + 0.5 * tied) / n


def _income_component(row: dict, rows: list[dict]):
    """인컴 = 0.7·분배율 백분위 + 0.3·규칙성 (월배당 1.0 · 분기 0.7 · 연 0.4)."""
    yp = percentile_rank([r.get("div_yield_pct") for r in rows],
                         row.get("div_yield_pct"), higher_better=True)
    if yp is None:
        return None
    cnt = row.get("div_count_12m") or 0
    reg = 1.0 if cnt >= 11 else 0.7 if cnt >= 3 else 0.4 if cnt >= 1 else 0.0
    return 0.7 * yp + 0.3 * reg


def etf_score(row: dict, rows: list[dict], strategy: str = "index") -> dict:
    """동종그룹 내 ETF 점수 1~100 (순수 — 표시·참고용, 매매신호 아님).

    컴포넌트 = 그룹 내 백분위×100 (비용·MDD 낮을수록↑). 결측 컴포넌트는 가중치
    재정규화, 가용 가중치 <50 → score=None(데이터 부족). 소그룹(n<3)은 50 쪽으로
    shrink + n<4 low_confidence. 이력 <2.5y 는 1y basis 폴백·<0.8y 는 성과/리스크 드롭.
    """
    weights = SCORE_WEIGHTS.get(strategy, SCORE_WEIGHTS["index"])
    hy = row.get("history_years") or 0
    basis = "3y" if (hy >= 2.5 and row.get("tr_3y_ann") is not None) else "1y"
    perf_field = "tr_3y_ann" if basis == "3y" else "tr_1y"
    comps: dict[str, float | None] = {}
    if "비용" in weights:
        comps["비용"] = percentile_rank([r.get("expense_ratio") for r in rows],
                                        row.get("expense_ratio"), higher_better=False)
    if "성과" in weights:
        comps["성과"] = (None if hy < 0.8 else
                        percentile_rank([r.get(perf_field) for r in rows],
                                        row.get(perf_field), higher_better=True))
    if "추적" in weights:
        comps["추적"] = percentile_rank([r.get("tracking_diff") for r in rows],
                                        row.get("tracking_diff"), higher_better=True)
    if "인컴" in weights:
        comps["인컴"] = _income_component(row, rows)
    if "리스크" in weights:
        comps["리스크"] = (None if hy < 0.8 else
                          percentile_rank([r.get("mdd") for r in rows],
                                          row.get("mdd"), higher_better=False))
    if "유동성" in weights:
        ap = percentile_rank([r.get("aum") for r in rows], row.get("aum"))
        vp = percentile_rank([r.get("avg_dollar_vol") for r in rows],
                             row.get("avg_dollar_vol"))
        comps["유동성"] = (0.6 * ap + 0.4 * vp if ap is not None and vp is not None
                          else ap if ap is not None else vp)
    avail = sum(w for k, w in weights.items() if comps.get(k) is not None)
    n = len(rows)
    out = {"components": {k: (round(c * 100) if c is not None else None)
                          for k, c in comps.items()},
           "weights": dict(weights), "n_peers": n,
           "low_confidence": n < 4, "basis": basis, "strategy": strategy}
    if avail < 50:
        out["score"] = None                                     # 데이터 부족 — 정직 생략
        return out
    raw = sum(w * comps[k] for k, w in weights.items() if comps.get(k) is not None) \
        / avail * 100.0
    shrink = min(1.0, max(0.0, (n - 1) / 3.0))                  # 소그룹 → 50 쪽 수축
    out["score"] = int(round(min(100.0, max(1.0, 50.0 + (raw - 50.0) * shrink))))
    return out


# ── 네트워크부 (graceful — 실패 시 None/{}·12h 디스크 캐시) ─────────────────────

def fetch_group_prices(tickers: list[str], years: int = 5) -> dict:
    """그룹 배치 다운로드(auto_adjust=False) → {ticker: DataFrame}. 실패 티커 개별 재시도."""
    import yfinance as yf
    out: dict = {}
    try:
        df = yf.download(" ".join(tickers), period=f"{years}y", auto_adjust=False,
                         progress=False, group_by="ticker", threads=True)
        for t in tickers:
            try:
                sub = df[t].dropna(how="all") if len(tickers) > 1 else df
                if sub is not None and not sub.empty:
                    out[t] = sub
            except Exception:
                pass
    except Exception:
        pass
    for t in tickers:                                           # 배치 누락분 개별 재시도
        if t not in out:
            try:
                s = yf.download(t, period=f"{years}y", auto_adjust=False, progress=False)
                if s is not None and not s.empty:
                    out[t] = s
            except Exception:
                pass
    return out


def tr_pr_series(ticker: str, years: int = 5):
    """단일 ETF TR/PR 시리즈 — {"tr": Series, "pr": Series, "asof": str} | None."""
    t = normalize_ticker(ticker)
    try:
        import yfinance as yf
        df = yf.download(t, period=f"{years}y", auto_adjust=False, progress=False)
    except Exception:
        return None
    pair = _extract_tr_pr(df, t)
    if pair is None:
        return None
    tr, pr = pair
    return {"tr": tr, "pr": pr,
            "asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def _peer_cache_path(group_key: str) -> Path:
    return CACHE_DIR / f"etf_peer_{group_key}.json"


def peer_report(ticker: str) -> dict:
    """동종그룹 지표+점수 — {"group", "rows", "asof"} | {} (그룹 없음/실패).

    rows 는 그룹 단위 12h 디스크 캐시 공유(어느 멤버로 봐도 재사용). 점수는 캐시된
    rows 에서 매 호출 순수 재계산(공식 변경이 캐시 무효화 없이 반영).
    """
    key = etf_meta.group_of(ticker)
    if not key:
        return {}
    group = etf_meta.ETF_GROUPS[key]
    rows, asof = None, None
    p = _peer_cache_path(key)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
            data = json.loads(p.read_text())
            rows, asof = data.get("rows"), data.get("asof")
    except Exception:
        rows = None
    if not rows:
        try:
            bench = group.get("bench")
            dl = group["etfs"] + ([bench] if bench not in group["etfs"] else [])
            prices = fetch_group_prices(dl)
            extras = {}
            for t in group["etfs"]:
                try:
                    extras[t] = etf_summary(t)                  # 자체 12h 캐시
                except Exception:
                    extras[t] = {}
            rows = compute_metrics(prices, group, extras)
            asof = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if any(r.get("tr_1y") is not None for r in rows):   # 유효 데이터만 캐시
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps({"rows": rows, "asof": asof},
                                          ensure_ascii=False))
                tmp.replace(p)
        except Exception:
            return {}
    if not rows:
        return {}
    strategy = group.get("strategy", "index")
    for r in rows:
        r["score_detail"] = etf_score(r, rows, strategy)
        r["score"] = r["score_detail"].get("score")
    return {"group": {"key": key, "name": group["name"], "strategy": strategy,
                      "bench": group["bench"]},
            "rows": rows, "asof": asof}
