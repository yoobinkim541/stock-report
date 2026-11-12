#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""가격으로 재현되는 미검증 규칙을 현재 모의와 같은 비용·게이트로 확인한다.

교체 후보가 되려면 한국 현물 매수 규칙이어야 하고, 전기간과 2019-2026 모두
should_adopt 를 통과해야 한다. 미국 ETF·옵션 대용·단기 샘플은 숫자만 남긴다.
이 스크립트는 라이브 가중을 고치지 않는다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kr_policy_backtest as kb  # noqa: E402
from ml.adaptive.reward import should_adopt  # noqa: E402
from ml.x_strategies import current_weights, picks_for, simulate_with_cash  # noqa: E402
from ml.x_untested import (  # noqa: E402
    connors_weights, meta_decisions, paa_weights, pairs_weights, simulate_signed,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("x_untested")

OUT_PATH = os.path.expanduser("~/reports/ml-cache/x_untested_backtest.json")
HOLDOUT = "2019-01-01"
US_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "XOM", "KO")


def _ever_top(marcap: pd.DataFrame, k: int) -> list[str]:
    names: set[str] = set()
    for t in kb.month_ends(marcap.index):
        row = marcap.loc[t].dropna()
        if len(row):
            names.update(row.nlargest(min(k, len(row))).index)
    return sorted(names)


def _universe_mask(marcap: pd.DataFrame, k: int) -> pd.DataFrame:
    months = kb.month_ends(marcap.index)
    mask = np.zeros(marcap.shape, dtype=bool)
    pos = {c: i for i, c in enumerate(marcap.columns)}
    dates = marcap.index
    for i, t in enumerate(months[:-1]):
        row = marcap.loc[t].dropna()
        if row.empty:
            continue
        cols = [pos[c] for c in row.nlargest(min(k, len(row))).index]
        left = int(dates.searchsorted(t, side="right"))
        right = int(dates.searchsorted(months[i + 1], side="right"))
        mask[left:right, cols] = True
    return pd.DataFrame(mask, index=marcap.index, columns=marcap.columns)


def _perf_pair(series: pd.Series, bench: pd.Series, champion: pd.Series | None,
               start: str | None = None) -> dict | None:
    s = series.dropna()
    if start is not None:
        s = s.loc[start:]
    if champion is not None:
        s = s.loc[s.index.intersection(champion.dropna().index)]
    b = bench.reindex(s.index).dropna()
    s = s.reindex(b.index).fillna(0.0)
    if len(s) < 2:
        return None
    ps, pb = kb.perf(s), kb.perf(b)
    out = {
        "total": ps["total"], "cagr": ps["cagr"], "mdd": ps["mdd"],
        "sharpe": ps["sharpe"], "n": ps["n"],
        "excess": round(ps["total"] - pb["total"], 4),
        "bench_total": pb["total"], "bench_mdd": pb["mdd"], "gate": False,
    }
    if champion is None:
        return out
    c = champion.reindex(s.index).fillna(0.0)
    pc = kb.perf(c)
    out["champion_excess"] = round(pc["total"] - pb["total"], 4)
    out["champion_mdd"] = pc["mdd"]
    out["gate"] = bool(should_adopt(
        {"excess": ps["total"] - pb["total"], "mdd": ps["mdd"], "n": ps["n"]},
        {"excess": pc["total"] - pb["total"], "mdd": pc["mdd"], "n": pc["n"]},
        pb["mdd"], min_samples=kb.MIN_SAMPLES if hasattr(kb, "MIN_SAMPLES") else 252))
    return out


def _rule(tested: bool, note: str, **extra) -> dict:
    return {"tested": tested, "replace_live": False, "note": note, **extra}


def _close_frame(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        levels = [df.columns.get_level_values(i) for i in range(df.columns.nlevels)]
        if "Close" in levels[0]:
            return df["Close"]
        if "Close" in levels[-1]:
            return df.xs("Close", axis=1, level=-1)
    return df


def run_connors(panels: dict, bench: pd.Series) -> tuple[dict, pd.Series | None]:
    names = _ever_top(panels["marcap"], kb.UNIVERSE_N)
    px = panels["adj"].reindex(columns=names)
    ret = panels["ret"].reindex(columns=names)
    marcap = panels["marcap"].reindex(columns=names)
    log.info("Connors 유니버스 %d종목", len(names))
    eligible = _universe_mask(marcap, kb.UNIVERSE_N)
    weights = connors_weights(px, eligible, k=kb.TOP_K, entry=5.0)
    net = simulate_signed(ret, weights, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS)
    gross = simulate_signed(ret, weights, buy_bps=0, sell_bps=0)
    if net.empty:
        return _rule(True, "신호는 만들었으나 체결 구간이 비었다."), None
    turn = float(weights.diff().abs().sum(axis=1).mean() * 252)
    return _rule(
        True,
        "한국 현물. RSI(2)<5, 200일선 위 진입, 5일선 위 청산, 최대 5종목. "
        "비용은 모의와 같은 매수 2bp·매도 20bp.",
        full_vs_bench=_perf_pair(net, bench, None),
        holdout_vs_bench=_perf_pair(net, bench, None, HOLDOUT),
        gross_full=_perf_pair(gross, bench, None),
        turnover_annual=round(turn, 2),
        _series=net,
    ), net


def run_pairs(panels: dict, bench: pd.Series) -> dict:
    names = _ever_top(panels["marcap"], kb.UNIVERSE_N)
    px = panels["adj"].reindex(columns=names)
    ret = panels["ret"].reindex(columns=names)
    marcap = panels["marcap"].reindex(columns=names)
    rebal = kb.month_ends(px.index)
    out = {}
    for key, long_only, note in (
        ("gatev_pairs", False, "달러중립 페어. 한국 모의는 공매도가 없어 라이브 교체 대상이 아니다."),
        ("gatev_long_cheap", True, "싼 다리만 매수하는 변형. 라이브에 넣을 수 있는 형태다."),
    ):
        weights = pairs_weights(px, marcap, rebal, n_names=40, n_pairs=5, long_only=long_only)
        net = simulate_signed(ret, weights, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS)
        out[key] = _rule(
            True, note,
            full_vs_bench=_perf_pair(net, bench, None),
            holdout_vs_bench=_perf_pair(net, bench, None, HOLDOUT),
            _series=net,
        )
        log.info("%s 완료 n=%s", key, 0 if net.empty else len(net))
    return out

def run_meta(panels: dict, bench: pd.Series) -> tuple[dict, pd.Series | None]:
    weights = current_weights()
    picks = {}
    months = []
    for t in kb.month_ends(panels["ret"].index):
        base = kb.features_asof(panels, t, universe_n=kb.UNIVERSE_N)
        if base is None:
            continue
        try:
            picks[t] = picks_for("current_price_mix", base, {}, kb.TOP_K, weights)
        except Exception:
            picks[t] = []
        months.append(t)
        if len(months) % 12 == 0:
            log.info("현재 믹스 %d개월 (%s)", len(months), t.date())
    champion = simulate_with_cash(panels["ret"], picks, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS)
    if len(months) < 37 or champion.empty:
        return _rule(False, f"메타라벨 학습에 필요한 36개월보다 짧다. 월수 {len(months)}."), champion
    rows, labels = [], []
    usable = months[:-1]
    for i, t in enumerate(usable):
        nxt = months[i + 1]
        hist = champion.loc[:t]
        fut = champion.loc[(champion.index > t) & (champion.index <= nxt)]
        if len(hist) < 126 or fut.empty:
            rows.append([np.nan, np.nan, np.nan, np.nan])
            labels.append(np.nan)
            continue
        trail = hist.iloc[-126:]
        btrail = bench.reindex(trail.index).fillna(0.0)
        rows.append([
            float((1.0 + trail).prod() - 1.0),
            float(trail.std()),
            float((1.0 + btrail).prod() - 1.0),
            float((1.0 + hist.iloc[-21:]).prod() - 1.0),
        ])
        labels.append(1.0 if float((1.0 + fut).prod() - 1.0) > 0 else 0.0)
    take = meta_decisions(np.array(rows, dtype=float), np.array(labels, dtype=float), min_train=36)
    filtered = dict(picks)
    for i, t in enumerate(usable):
        if not take[i]:
            filtered[t] = []
    series = simulate_with_cash(panels["ret"], filtered, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS)
    skipped = int((~take).sum())
    return _rule(
        True,
        "현재 가격 믹스를 1차 신호로 두고, 과거 36개월만으로 학습한 로지스틱이 "
        "다음 달 수익이 양수일 확률이 0.5 미만이면 현금. Lopez de Prado 메타라벨.",
        months=len(usable), skipped=skipped,
        full_vs_bench=_perf_pair(series, bench, champion),
        holdout_vs_bench=_perf_pair(series, bench, champion, HOLDOUT),
        _series=series,
    ), champion


def _download_close(tickers: list[str]) -> pd.DataFrame | None:
    import yfinance as yf
    df = yf.download(tickers, period="max", auto_adjust=True, progress=False, threads=False)
    if df is None or len(df) == 0:
        return None
    close = _close_frame(df).dropna(how="all")
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.sort_index()


def run_paa() -> dict:
    risky, prot = ["SPY", "EFA", "EEM"], ["TLT", "IEF", "GLD"]
    close = _download_close(risky + prot)
    if close is None:
        return _rule(False, "yfinance 에서 ETF 가격을 받지 못했다.")
    have = [c for c in risky + prot if c in close.columns and close[c].notna().sum() > 300]
    risky = [c for c in risky if c in have]
    prot = [c for c in prot if c in have]
    if len(risky) < 2 or not prot:
        return _rule(False, f"ETF 시계열이 부족하다. 받은 종목: {have}")
    months = kb.month_ends(close.index)
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for i, t in enumerate(months[:-1]):
        hist = close.loc[:t].dropna(how="all")
        if len(hist) < 253:
            continue
        mom = hist.iloc[-1] / hist.iloc[-253] - 1.0
        alloc = paa_weights(mom, risky, prot)
        nxt = months[i + 1]
        days = close.index[(close.index >= t) & (close.index < nxt)]
        for name, weight in alloc.items():
            weights.loc[days, name] = weight
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    series = simulate_signed(ret, weights, buy_bps=1, sell_bps=1)
    spy = close["SPY"].pct_change(fill_method=None) if "SPY" in close.columns else pd.Series(dtype=float)
    return _rule(
        True,
        "미국 ETF 자산배분이다. 한국 종목 선정 로직을 이 규칙으로 바꾸지 않는다. 비용 1bp.",
        loaded=have,
        span=[str(close.index.min().date()), str(close.index.max().date())],
        full_vs_spy=_perf_pair(series, spy, None),
        holdout_vs_spy=_perf_pair(series, spy, None, HOLDOUT),
    )


def run_tail() -> dict:
    close = _download_close(["SPY", "TAIL"])
    if close is None or "TAIL" not in getattr(close, "columns", []):
        return _rule(False, "TAIL ETF 가격을 받지 못했다. 옵션 체결 이력도 로컬에 없다.")
    tail = close["TAIL"].dropna()
    spy = close["SPY"].reindex(tail.index)
    both = pd.DataFrame({"SPY": spy, "TAIL": tail}).dropna()
    if len(both) < 252:
        return _rule(False, f"TAIL 표본이 {len(both)}일이다. 옵션 전략의 대용이 아니다.")
    weights = pd.DataFrame(0.0, index=both.index, columns=["SPY", "TAIL"])
    months = kb.month_ends(both.index)
    for i, t in enumerate(months[:-1]):
        nxt = months[i + 1]
        days = both.index[(both.index >= t) & (both.index < nxt)]
        weights.loc[days, "SPY"] = 0.9
        weights.loc[days, "TAIL"] = 0.1
    ret = both.pct_change(fill_method=None)
    series = simulate_signed(ret, weights, buy_bps=1, sell_bps=1)
    return _rule(
        True,
        "Universa 풋이 아니라 TAIL ETF 90/10 대용이다. 한국 옵션 이력이 없어 원규칙은 검증되지 않았다.",
        proxy=True,
        span=[str(both.index.min().date()), str(both.index.max().date())],
        full_vs_spy=_perf_pair(series, both["SPY"].pct_change(fill_method=None), None),
    )


def _surprise(frame: pd.DataFrame) -> pd.Series:
    for name in frame.columns:
        if "surprise" in str(name).lower():
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(dtype=float)


def run_pead() -> dict:
    import yfinance as yf
    kr = yf.Ticker("005930.KS").get_earnings_dates(limit=8)
    kr_n = 0 if kr is None else len(kr)
    events = []
    errors = []
    for ticker in US_TICKERS:
        try:
            frame = yf.Ticker(ticker).get_earnings_dates(limit=60)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
            continue
        if frame is None or frame.empty:
            continue
        surprise = _surprise(frame)
        for stamp, value in surprise.items():
            if pd.notna(value) and float(value) > 0:
                events.append((pd.Timestamp(stamp).tz_localize(None), ticker))
    if not events:
        return _rule(False, f"서프라이즈 이력이 없다. 삼성전자 실적일 {kr_n}건. {errors[:2]}")
    close = _download_close(list(US_TICKERS) + ["SPY"])
    if close is None or "SPY" not in close.columns:
        return _rule(False, "미국 가격을 받지 못해 PEAD 수익을 계산하지 못했다.")
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for stamp, ticker in events:
        if ticker not in close.columns:
            continue
        loc = int(close.index.searchsorted(stamp, side="right"))
        if loc >= len(close.index):
            continue
        weights.iloc[loc:loc + 21, weights.columns.get_loc(ticker)] += 1.0
    active = weights.sum(axis=1)
    weights = weights.div(active.replace(0.0, np.nan), axis=0).fillna(0.0)
    ret = close.pct_change(fill_method=None)
    series = simulate_signed(ret, weights, buy_bps=1, sell_bps=1)
    return _rule(
        True,
        "미국 8종목, 긍정 서프라이즈 후 21거래일. 현재 초대형주만 있어 생존편향이 있다. "
        "한국 표본은 pead_kr 이다. 어느 쪽도 모의 교체 근거가 아니다.",
        kr_earnings_rows=kr_n, us_events=len(events), errors=errors,
        full_vs_spy=_perf_pair(series, close["SPY"].pct_change(fill_method=None), None),
    )

KR_PEAD = (
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS",
    "000270.KS", "068270.KS", "005490.KS", "035420.KS", "051910.KS",
    "006400.KS", "035720.KS", "028260.KS", "105560.KS", "055550.KS",
    "012330.KS", "066570.KS", "003550.KS", "096770.KS", "034730.KS",
)


def run_pead_kr() -> dict:
    """현재 대형주 20개의 yfinance 서프라이즈. 상폐·과거 유니버스는 포함하지 않는다."""
    import yfinance as yf
    events = []
    errors = []
    spans = []
    for ticker in KR_PEAD:
        try:
            frame = yf.Ticker(ticker).get_earnings_dates(limit=40)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
            continue
        if frame is None or frame.empty:
            continue
        surprise = _surprise(frame)
        stamps = []
        for stamp, value in surprise.items():
            if pd.notna(value) and float(value) > 0:
                when = pd.Timestamp(stamp).tz_localize(None)
                events.append((when, ticker))
                stamps.append(when)
        if stamps:
            spans.append((min(stamps), max(stamps)))
    if not events:
        return _rule(False, f"한국 대형주 서프라이즈를 받지 못했다. {errors[:2]}")
    close = _download_close(list(KR_PEAD) + ["069500.KS"])
    if close is None or "069500.KS" not in close.columns:
        return _rule(False, "한국 가격 또는 KODEX 200 을 받지 못했다.", events=len(events))
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for stamp, ticker in events:
        if ticker not in close.columns:
            continue
        loc = int(close.index.searchsorted(stamp, side="right"))
        if loc >= len(close.index):
            continue
        weights.iloc[loc:loc + 21, weights.columns.get_loc(ticker)] += 1.0
    active = weights.sum(axis=1)
    weights = weights.div(active.replace(0.0, np.nan), axis=0).fillna(0.0)
    ret = close.pct_change(fill_method=None)
    series = simulate_signed(ret, weights, buy_bps=kb.BUY_BPS, sell_bps=kb.SELL_BPS)
    return _rule(
        True,
        "현재 시총 대형주 20종목만의 표본이다. 상장폐지 종목이 없어 생존편향이 있고, "
        "한국 전종목 패널이 아니므로 모의 교체 근거가 아니다. 긍정 서프라이즈 다음 21거래일.",
        events=len(events), errors=errors[:4],
        names_with_events=len(spans),
        full_vs_kodex=_perf_pair(series, close["069500.KS"].pct_change(fill_method=None), None),
    )


def run_fundamentals() -> dict:
    import yfinance as yf
    tried = {}
    for ticker in ("005930.KS", "AAPL"):
        try:
            stmt = yf.Ticker(ticker).quarterly_income_stmt
            dates = [] if stmt is None or stmt.empty else [str(pd.Timestamp(c).date()) for c in stmt.columns]
            tried[ticker] = {"rows": 0 if stmt is None else int(stmt.shape[0]), "dates": dates}
        except Exception as exc:
            tried[ticker] = {"error": str(exc)}
    spans = [d for item in tried.values() for d in item.get("dates", [])]
    return _rule(
        False,
        "손익 스냅샷만 있고 공시일 기준 25년 패널이 없다. QMJ·CAN SLIM 의 이익 규칙은 백테스트하지 못했다. "
        "가격만 쓰는 CAN SLIM 부분집합은 앞선 카탈로그의 minervini 결과로 이미 기각됐다.",
        statements=tried,
        latest_dates=spans[:8],
    )


def run_intraday() -> dict:
    bar_dir = os.path.expanduser("~/reports/ml-data/intraday_bars")
    local = os.path.isdir(bar_dir)
    import yfinance as yf
    try:
        raw = yf.download("005930.KS", period="730d", interval="1h",
                          auto_adjust=True, progress=False, threads=False)
    except Exception as exc:
        return _rule(False, f"로컬 분봉 없음. yfinance 1시간봉 실패: {exc}", local_bars=local)
    close = _close_frame(raw)
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0] if close.shape[1] else pd.Series(dtype=float)
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < 100:
        return _rule(False, f"로컬 분봉 없음. 받은 1시간봉이 {len(close)}개다.", local_bars=local)
    fast = close.ewm(span=9, adjust=False).mean()
    slow = close.ewm(span=21, adjust=False).mean()
    held = (fast > slow).astype(float).shift(1)
    ret = close.pct_change(fill_method=None).fillna(0.0)
    strat = held * ret
    buy = close.pct_change(fill_method=None).fillna(0.0)
    span_days = int((close.index.max() - close.index.min()).days)
    return _rule(
        True,
        "로컬 분봉 저장소가 없다. 삼성전자 1시간봉 EMA 9/21 한 종목 샘플이며 모의 교체 근거가 아니다.",
        local_bars=local, bars=int(len(close)), span_days=span_days,
        strategy_total=round(float((1.0 + strat).prod() - 1.0), 4),
        buyhold_total=round(float((1.0 + buy).prod() - 1.0), 4),
    )


def run_blocked_local() -> dict:
    news = os.path.expanduser("~/reports/ml-data/news_llm_labels.jsonl")
    options = os.path.expanduser("~/reports/ml-cache/options_snapshots.jsonl")
    return {
        "llm_sentiment": _rule(
            False,
            "뉴스 라벨 파일이 없다. 라벨을 만들어 맞히는 검증은 하지 않는다. "
            f"경로 존재: {os.path.exists(news)}",
        ),
        "tail_hedge_options": _rule(
            False,
            "options_snapshot.py 가 과거 옵션 조회는 불가능하다고 적고 있다. "
            f"스냅샷 파일 존재: {os.path.exists(options)}. 원규칙은 검증되지 않았다.",
        ),
    }


def _attach_champion(rule: dict, bench: pd.Series, champion: pd.Series | None) -> dict:
    series = rule.pop("_series", None)
    if series is None or champion is None or series.empty:
        rule["gate_full"] = False
        rule["gate_holdout"] = False
        return rule
    rule["full"] = _perf_pair(series, bench, champion)
    rule["holdout_2019"] = _perf_pair(series, bench, champion, HOLDOUT)
    rule["gate_full"] = bool(rule["full"] and rule["full"].get("gate"))
    rule["gate_holdout"] = bool(rule["holdout_2019"] and rule["holdout_2019"].get("gate"))
    rule["replace_live"] = bool(rule["gate_full"] and rule["gate_holdout"])
    return rule


def _print_table(payload: dict) -> None:
    print("\n규칙 | 검증 | 교체 | 전기간 초과 | 2019+ 초과 | MDD | 비고")
    for name, rule in payload["rules"].items():
        full = rule.get("full") or rule.get("full_vs_bench") or rule.get("full_vs_spy") or rule.get("full_vs_kodex") or {}
        hold = rule.get("holdout_2019") or rule.get("holdout_vs_bench") or rule.get("holdout_vs_spy") or {}
        print(
            f"{name} | {rule.get('tested')} | {rule.get('replace_live')} | "
            f"{full.get('excess', full.get('strategy_total', '-'))} | "
            f"{hold.get('excess', '-')} | {full.get('mdd', '-')} | {rule.get('note', '')[:80]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2001)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--out", default=OUT_PATH)
    args = parser.parse_args(argv)
    rules: dict = run_blocked_local()
    panels = kb.build_panels(args.start, args.end, market="KOSPI")
    if not panels:
        payload = {"asof": datetime.now().isoformat(timespec="seconds"), "rules": rules,
                   "error": "marcap 패널을 만들지 못했다."}
        _dump(args.out, payload)
        return 1
    rebal = kb.month_ends(panels["ret"].index)
    bench = kb.cap_benchmark(panels, rebal)
    connors, _connors_series = run_connors(panels, bench)
    rules["connors_rsi2"] = connors
    rules.update(run_pairs(panels, bench))
    meta, champion = run_meta(panels, bench)
    rules["meta_label"] = meta
    for key in ("connors_rsi2", "gatev_long_cheap"):
        if key in rules:
            rules[key] = _attach_champion(rules[key], bench, champion)
    rules["gatev_pairs"].pop("_series", None)
    rules["meta_label"].pop("_series", None)
    rules["meta_label"]["replace_live"] = bool(
        rules["meta_label"].get("full_vs_bench", {}) and
        rules["meta_label"]["full_vs_bench"].get("gate") and
        rules["meta_label"].get("holdout_vs_bench", {}) and
        rules["meta_label"]["holdout_vs_bench"].get("gate")
    )
    rules["gatev_pairs"]["replace_live"] = False
    payload = {
        "asof": datetime.now().isoformat(timespec="seconds"),
        "period": [args.start, args.end],
        "costs_bps": {"buy": kb.BUY_BPS, "sell": kb.SELL_BPS},
        "rules": rules,
        "live_changed": False,
    }
    _dump(args.out, payload)
    if not args.skip_network:
        import socket
        socket.setdefaulttimeout(40)
        for name, fn in (
            ("paa_multiasset", run_paa),
            ("tail_proxy", run_tail),
            ("pead", run_pead),
            ("pead_kr", run_pead_kr),
            ("qmj_can_slim", run_fundamentals),
            ("intraday", run_intraday),
        ):
            try:
                rules[name] = fn()
            except Exception as exc:
                rules[name] = _rule(False, f"실행 실패: {exc}")
            log.info("%s tested=%s", name, rules[name].get("tested"))
    payload = {
        "asof": datetime.now().isoformat(timespec="seconds"),
        "period": [args.start, args.end],
        "costs_bps": {"buy": kb.BUY_BPS, "sell": kb.SELL_BPS},
        "rules": rules,
        "live_changed": False,
    }
    _dump(args.out, payload)
    _print_table(payload)
    return 0


def _dump(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    log.info("저장 %s", path)


if __name__ == "__main__":
    sys.exit(main())
