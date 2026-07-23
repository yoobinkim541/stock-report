#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_risk_report.py — cross-asset market risk report.

첨부 예시의 Flash Layer / Deep-Dive Layer 형식을 자동 생성한다.
라이브 데이터 수집은 best-effort이고, 판정/렌더링은 순수 함수로 테스트한다.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

KST = timezone(timedelta(hours=9))
REPORTS_DIR = os.path.expanduser("~/reports")

ASSET_NAMES = {
    "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "RSP": "RSP",
    "^VIX": "VIX", "^MOVE": "MOVE", "DX-Y.NYB": "DXY", "TLT": "TLT",
    "HYG": "HYG", "LQD": "LQD", "CL=F": "WTI", "GC=F": "Gold",
    "BTC-USD": "BTC", "ES=F": "ES", "NQ=F": "NQ", "RTY=F": "RTY",
}
LIVE_TICKERS = tuple(ASSET_NAMES)


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if value != value:
            return None
        return value
    except Exception:
        return None


def _pct(value: float | None) -> str:
    v = _num(value)
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt(value: float | None) -> str:
    v = _num(value)
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _asset(inputs: dict, ticker: str) -> dict:
    return (inputs.get("assets") or {}).get(ticker) or {}


def _ratio_change(inputs: dict, left: str, right: str, horizon: str) -> float | None:
    a = _num(_asset(inputs, left).get(horizon))
    b = _num(_asset(inputs, right).get(horizon))
    if a is None or b is None:
        return None
    return ((1 + a / 100.0) / (1 + b / 100.0) - 1) * 100.0


def classify_regime(inputs: dict) -> dict:
    assets = inputs.get("assets") or {}
    vix = _num((assets.get("^VIX") or {}).get("last"))
    vix_d1 = _num((assets.get("^VIX") or {}).get("d1"))
    wti = _num((assets.get("CL=F") or {}).get("last"))
    wti_d5 = _num((assets.get("CL=F") or {}).get("d5"))
    tlt_d20 = _num((assets.get("TLT") or {}).get("d20"))
    move_d20 = _num((assets.get("^MOVE") or {}).get("d20"))
    rty_d1 = _num((assets.get("RTY=F") or {}).get("d1"))
    hyg_lqd_5d = _ratio_change(inputs, "HYG", "LQD", "d5")
    rsp_spy_20d = _ratio_change(inputs, "RSP", "SPY", "d20")

    score = 0
    drivers: list[str] = []
    if vix is not None and vix >= 30:
        score += 3; drivers.append(f"VIX {_fmt(vix)} 30 상회")
    elif vix is not None and vix >= 25:
        score += 2; drivers.append(f"VIX {_fmt(vix)} 고위험권")
    if vix_d1 is not None and vix_d1 >= 10:
        score += 1; drivers.append(f"VIX 1D {_pct(vix_d1)}")
    if wti is not None and wti >= 100:
        score += 2; drivers.append(f"WTI {_fmt(wti)} 고유가")
    if wti_d5 is not None and wti_d5 >= 8:
        score += 2; drivers.append(f"WTI 5D {_pct(wti_d5)} 급등")
    if tlt_d20 is not None and tlt_d20 < -2:
        score += 1; drivers.append(f"TLT 20D {_pct(tlt_d20)}")
    if move_d20 is not None and move_d20 > 20:
        score += 1; drivers.append(f"MOVE 20D {_pct(move_d20)}")
    if rty_d1 is not None and rty_d1 < -1.5:
        score += 1; drivers.append(f"RTY 선물 1D {_pct(rty_d1)}")
    if hyg_lqd_5d is not None and hyg_lqd_5d < -0.5:
        score += 2; drivers.append(f"HYG/LQD 5D {_pct(hyg_lqd_5d)}")
    if rsp_spy_20d is not None and rsp_spy_20d < -0.5:
        score += 1; drivers.append(f"RSP/SPY 20D {_pct(rsp_spy_20d)}")

    energy = (wti is not None and wti >= 95) or (wti_d5 is not None and wti_d5 >= 8)
    if score >= 7:
        severity = "높음"
    elif score >= 4:
        severity = "중간"
    elif score >= 2:
        severity = "주의"
    else:
        severity = "낮음"

    if energy and severity in {"중간", "높음"}:
        label = f"에너지 쇼크형 이벤트 리스크오프({severity} 강도)"
    elif severity in {"중간", "높음"}:
        label = f"크로스에셋 리스크오프({severity} 강도)"
    elif severity == "주의":
        label = "혼조/경계 레짐"
    else:
        label = "정상 관찰 레짐"
    return {"score": score, "severity": severity, "label": label, "drivers": drivers[:8]}


def _bucket_rows(inputs: dict) -> list[tuple[str, str, str]]:
    a = inputs.get("assets") or {}
    rsp_spy = _ratio_change(inputs, "RSP", "SPY", "d20")
    iwm_spy = _ratio_change(inputs, "IWM", "SPY", "d20")
    hyg_lqd = _ratio_change(inputs, "HYG", "LQD", "d5")
    rows = [
        ("Equity Cash vs Overnight",
         f"SPY {_pct((a.get('SPY') or {}).get('d1'))}, QQQ {_pct((a.get('QQQ') or {}).get('d1'))} vs ES {_pct((a.get('ES=F') or {}).get('d1'))}, NQ {_pct((a.get('NQ=F') or {}).get('d1'))}",
         "현물 종가와 오버나이트 선물이 엇갈리면 전일 지수보다 체감 위험을 높게 봅니다."),
        ("Breadth / Leadership",
         f"RSP/SPY 20D {_pct(rsp_spy)}, IWM/SPY 20D {_pct(iwm_spy)}",
         "동일가중·중소형 상대강도는 반등의 폭과 내부 체력을 보여줍니다."),
        ("Vol",
         f"VIX {_fmt((a.get('^VIX') or {}).get('last'))} ({_pct((a.get('^VIX') or {}).get('d1'))} 1D)",
         "25~30 구간은 이벤트 헤지 수요, 30 이상은 포지션 스트레스 경계선입니다."),
        ("Rates",
         f"TLT 20D {_pct((a.get('TLT') or {}).get('d20'))}, MOVE 20D {_pct((a.get('^MOVE') or {}).get('d20'))}",
         "채권이 주식 약세를 받아주지 못하면 할인율 리스크가 남습니다."),
        ("Credit", f"HYG/LQD 5D {_pct(hyg_lqd)}",
         "크레딧이 꺾이면 이벤트 장세가 시스템 스트레스로 번지는 신호입니다."),
        ("USD / Commodities",
         f"DXY 20D {_pct((a.get('DX-Y.NYB') or {}).get('d20'))}, WTI 5D {_pct((a.get('CL=F') or {}).get('d5'))}",
         "달러와 유가가 함께 오르면 글로벌 금융여건과 마진 부담이 동시에 커집니다."),
        ("Alt Risk", f"BTC 20D {_pct((a.get('BTC-USD') or {}).get('d20'))}",
         "크립토는 위험선호의 민감한 층이 식는지 보는 보조 프록시입니다."),
    ]
    return rows


def _asset_table(inputs: dict, tickers: list[str]) -> list[str]:
    lines = ["| 자산 | Last | 1D | 5D | 20D | 60D | as of |", "|---|---:|---:|---:|---:|---:|---|"]
    for t in tickers:
        item = _asset(inputs, t)
        label = ASSET_NAMES.get(t, t)
        lines.append(
            f"| {label} | {_fmt(item.get('last'))} | {_pct(item.get('d1'))} | {_pct(item.get('d5'))} | "
            f"{_pct(item.get('d20'))} | {_pct(item.get('d60'))} | {item.get('as_of', '—')} |"
        )
    return lines


def _world_memory_rows(inputs: dict) -> list[str]:
    rows = inputs.get("world_memory") or []
    lines = ["| Active State | 현재 의미 | 시장 함의 |", "|---|---|---|"]
    if not rows:
        lines.append("| World Memory | 최신 승격 메모리 없음 | 뉴스/가격 데이터 중심으로 판정 |")
        return lines
    for row in rows[:5]:
        lines.append(f"| {row.get('title', '메모리')} | {row.get('meaning', row.get('body', '—'))} | {row.get('implication', '—')} |")
    return lines


def _checkpoint_rows(inputs: dict) -> list[str]:
    a = inputs.get("assets") or {}
    checks = [
        ("CL=F / WTI", _fmt((a.get("CL=F") or {}).get("last")), "100~110달러 위 고착 시 에너지-물가 경로 악화"),
        ("^VIX", _fmt((a.get("^VIX") or {}).get("last")), "30 돌파 여부가 포지션 스트레스 경계"),
        ("TLT", _fmt((a.get("TLT") or {}).get("last")), "채권이 방패 역할을 회복하는지 확인"),
        ("HYG/LQD", _pct(_ratio_change(inputs, "HYG", "LQD", "d5")), "신용 전이 여부를 보는 실용적 신호"),
        ("RSP/SPY", _pct(_ratio_change(inputs, "RSP", "SPY", "d20")), "시장 폭 회복 여부"),
        ("IWM/SPY", _pct(_ratio_change(inputs, "IWM", "SPY", "d20")), "경기민감·금리민감 체력"),
    ]
    lines = ["| 체크포인트 | 현재 값/상태 | 왜 중요한가 |", "|---|---:|---|"]
    lines.extend(f"| {name} | {value} | {why} |" for name, value, why in checks)
    return lines


def build_market_risk_report(inputs: dict) -> str:
    regime = classify_regime(inputs)
    as_of = inputs.get("as_of") or datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines: list[str] = [
        "# 시장 위험 보고서",
        f"작성 시각(as of): {as_of}",
        "기준 시장: 미국 주식 중심 + 글로벌 크로스에셋 전이 경로",
        "데이터 성격: yfinance 일봉/최근 가용 시세 + 프로젝트 World Memory/뉴스 다이제스트",
        "",
        "## 데이터 컷오프",
        "- 자산별 최신 시점이 완전히 같지 않은 혼합 시차 데이터입니다.",
        "- 지수/ETF/선물/원자재/크립토는 각 소스의 최신 가용 시점 기준입니다.",
        "- 같은 줄의 수치를 단순 동시 비교하지 말고 방향성과 전이 경로 중심으로 해석합니다.",
        "",
        "## Flash Layer",
        "",
        "### 1. 한 줄 레짐 판정",
        f"현재 단기 레짐은 **{regime['label']}**입니다.",
        "",
        "핵심 근거: " + (" · ".join(regime["drivers"]) if regime["drivers"] else "특이 위험 신호 제한적"),
        "",
        "### 2. 버킷별 핵심 신호",
        "",
        "| Bucket | Signal | 해석 |",
        "|---|---|---|",
    ]
    for bucket, signal, meaning in _bucket_rows(inputs):
        lines.append(f"| {bucket} | {signal} | {meaning} |")

    lines.extend(["", "### 3. 월드메모리 오버레이", "", *_world_memory_rows(inputs), ""])

    digest = inputs.get("news_digest") or []
    lines.extend(["### 4. 지금 시장을 움직이는 핵심 드라이버", ""])
    if digest:
        for item in digest[:5]:
            lines.append(f"- {item}")
    else:
        for item in regime["drivers"][:5]:
            lines.append(f"- {item}")
    if not digest and not regime["drivers"]:
        lines.append("- 최신 뉴스/월드메모리 드라이버가 제한적입니다.")

    lines.extend([
        "",
        "## Deep-Dive Layer",
        "",
        "### 1. 왜 지금은 지수보다 위험이 더 높거나 낮은가",
        "현물 지수, 오버나이트 선물, 변동성, 금리, 크레딧, 원자재가 같은 방향으로 움직이는지 분해합니다. "
        "특히 선물 약세와 VIX 상승이 현물 종가보다 빠르게 나타나면 전일 지수 수익률은 체감 위험을 과소평가할 수 있습니다.",
        "",
        "### 2. 교차자산 스냅샷",
        "",
        * _asset_table(inputs, ["SPY", "QQQ", "IWM", "RSP", "^VIX", "^MOVE", "DX-Y.NYB", "TLT", "HYG", "LQD", "CL=F", "GC=F", "BTC-USD"]),
        "",
        "### 3. 오버나이트 선물/24시간 프록시",
        "",
        * _asset_table(inputs, ["ES=F", "NQ=F", "RTY=F", "CL=F", "BTC-USD"]),
        "",
        "### 4. 변동성·옵션 프록시",
        f"- VIX: {_fmt(_asset(inputs, '^VIX').get('last'))} ({_pct(_asset(inputs, '^VIX').get('d1'))} 1D)",
        f"- MOVE 20D: {_pct(_asset(inputs, '^MOVE').get('d20'))}",
        "- 옵션 체인 품질이 불안정할 수 있으므로 기본 결론은 VIX, MOVE, 선물, HYG/LQD, WTI 조합으로 판단합니다.",
        "",
        "### 5. 뉴스 내러티브와 리스크 전이",
        "- 에너지/공급망 충격 → 인플레 기대 → 장기금리/할인율 재가격",
        "- 달러 강세 + 원자재 상승 → 글로벌 금융여건 긴축 → 중소형/투기성 자산 약세",
        "- 내부 폭 악화 → 메가캡 의존도 상승 → 지수 완충력 약화",
        "",
        "### 6. 시나리오와 트리거",
        "**Baseline:** 고변동성 경계 국면 지속. VIX 25~30, WTI 고수준, HYG/LQD 보합이면 이벤트형 경계로 봅니다.",
        "",
        "**Upside:** WTI가 빠르게 식고 VIX가 24 아래로 내려오며 RSP/SPY·IWM/SPY가 같이 개선될 때입니다.",
        "",
        "**Downside:** WTI 110 이상, VIX 30 이상, HYG/LQD 음전, RTY/NQ 약세가 현물장 하락으로 확정될 때입니다.",
        "",
        "### 7. 이번 주 체크포인트",
        "",
        *_checkpoint_rows(inputs),
        "",
        "### 8. 한계와 주의",
        "- yfinance는 자산별 최신 시점과 결측 처리 방식이 다를 수 있습니다.",
        "- 본 문서는 확정적 예언이 아니라 현재 데이터 조합이 가리키는 우세 시나리오입니다.",
        "- 투자 자문이 아니라 정보 제공 목적입니다.",
        "",
        "## 결론",
        f"현재 우세 판단은 **{regime['label']}**입니다. 확인선은 WTI, VIX, 장기채(TLT), HYG/LQD, 시장 폭(RSP/SPY·IWM/SPY)입니다.",
        "",
    ])
    return "\n".join(lines)


def build_mobile_summary(inputs: dict) -> str:
    regime = classify_regime(inputs)
    a = inputs.get("assets") or {}
    lines = [
        f"⚠️ 시장 위험 보고서 · {inputs.get('as_of', datetime.now(KST).strftime('%Y-%m-%d %H:%M KST'))}",
        f"레짐: {regime['label']}",
        "",
        "핵심 근거:",
    ]
    for item in regime["drivers"][:5] or ["특이 위험 신호 제한적"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "핵심 체크:",
        f"- WTI {_fmt((a.get('CL=F') or {}).get('last'))} / VIX {_fmt((a.get('^VIX') or {}).get('last'))}",
        f"- HYG/LQD 5D {_pct(_ratio_change(inputs, 'HYG', 'LQD', 'd5'))}",
        f"- RSP/SPY 20D {_pct(_ratio_change(inputs, 'RSP', 'SPY', 'd20'))}",
        "",
        "전체 위험 보고서는 첨부 문서 참고",
    ])
    return "\n".join(lines)[:1800]


def _history_to_asset(ticker: str) -> dict:
    try:
        hist = yf.Ticker(ticker).history(period="90d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty or "Close" not in hist:
            return {"error": "no history"}
        close = hist["Close"].dropna()
        if close.empty:
            return {"error": "no close"}
        last = float(close.iloc[-1])

        def ret(days: int) -> float | None:
            if len(close) <= days:
                return None
            base = float(close.iloc[-1 - days])
            if not base:
                return None
            return (last / base - 1) * 100.0

        idx = close.index[-1]
        as_of = str(getattr(idx, "date", lambda: idx)())
        return {"last": last, "d1": ret(1), "d5": ret(5), "d20": ret(20), "d60": ret(60), "as_of": as_of}
    except Exception as exc:
        return {"error": str(exc)}


def _load_world_memory(limit: int = 4) -> list[dict]:
    try:
        from agent_console import context
        rows = context.world_memory_rows(limit=limit)
        out = []
        for row in rows[:limit]:
            out.append({
                "title": row.get("title") or row.get("kind") or "World Memory",
                "meaning": row.get("body") or row.get("summary") or row.get("source") or "—",
                "implication": row.get("impact") or "시장 전이 경로 확인",
            })
        return out
    except Exception:
        return []


def _load_news_digest(limit: int = 5) -> list[str]:
    try:
        from source_collector import load_recent_events
        events = load_recent_events(hours=24)
        return [str(e.get("title") or e.get("summary") or e)[:140] for e in events[:limit]]
    except Exception:
        return []


def collect_market_risk_inputs() -> dict:
    return {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "assets": {ticker: _history_to_asset(ticker) for ticker in LIVE_TICKERS},
        "world_memory": _load_world_memory(),
        "news_digest": _load_news_digest(),
    }


def write_report(*, date: str | None = None, reports_dir: str = REPORTS_DIR) -> tuple[str, str]:
    inputs = collect_market_risk_inputs()
    date_key = date or datetime.now(KST).strftime("%Y-%m-%d")
    out_dir = Path(reports_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"market-risk-report-{date_key}.md"
    summary_path = out_dir / f"market-risk-summary-{date_key}.txt"
    data_path = out_dir / f"market-risk-data-{date_key}.json"
    report_path.write_text(build_market_risk_report(inputs), encoding="utf-8")
    summary_path.write_text(build_mobile_summary(inputs), encoding="utf-8")
    data_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(report_path), str(summary_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the daily cross-asset market risk report.")
    parser.add_argument("--date")
    parser.add_argument("--reports-dir", default=REPORTS_DIR)
    args = parser.parse_args(argv)
    report, summary = write_report(date=args.date, reports_dir=args.reports_dir)
    print(f"market risk report: {report}")
    print(f"market risk summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
