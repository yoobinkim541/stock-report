#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
holding_manager.py — portfolio_snapshot.json CRUD + DCA 비중 관리

텔레그램 /holding 명령어의 백엔드.
portfolio_snapshot.json과 dca_weights.json을 직접 수정한다.
"""

import json
import os
import tempfile
from datetime import datetime

import store  # SQLite 통합 저장소 (portfolio_snapshot 그림자 동기화 — round 3)
import safe_io  # 원자적 쓰기 + 교차 프로세스 쓰기 락

# portfolio_snapshot 경로 단일 소스 — portfolio_universe(STOCK_REPORT_PROJECT_DIR env 반영)
from portfolio_universe import PORTFOLIO_SNAPSHOT_PATH as PORTFOLIO_PATH

# ── ETF / 레버리지 티커 (목표 비중 분석 제외) ────────────────────────
_SKIP_TICKERS = {"SGOV", "QQQI", "QLD", "TQQQ", "BIL", "SHV", "SHY",
                 "QQQ", "SPY", "VTI", "EFA", "TLT", "IEF", "GLD",
                 "DBC", "DBMF", "UPRO", "TMF"}


# ══════════════════════════════════════════════════════════════════════
#  내부 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _load() -> dict:
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(snap: dict):
    """atomic write: temp → rename, 중간 충돌 시 원본 보호.

    교차 프로세스 쓰기 락(safe_io.file_write_lock)으로 kiwoom_sync_rest·
    portfolio_sync_server 와 동시 쓰기 시 lost update 를 방지한다.
    """
    snap["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        safe_io.atomic_write_json(PORTFOLIO_PATH, snap)
    # store 그림자 사본 (user_id 스코프 — 멀티유저 기반). 파일이 권위.
    store.shadow_doc("portfolio_snapshot", snap)


def _find_holding(snap: dict, ticker: str) -> tuple[str, int, dict | None]:
    """
    ticker를 overseas_general 또는 overseas_fractional 에서 탐색.
    Returns: (section_key, index, holding_dict)
    """
    ticker = ticker.upper()
    for section, key in [("overseas_general", "holdings_usd"),
                          ("overseas_fractional", "holdings")]:
        holdings = snap.get(section, {}).get(key, [])
        for i, h in enumerate(holdings):
            if h.get("ticker", "").upper() == ticker:
                return section, i, h
    return "", -1, None


def _all_holdings(snap: dict) -> list[dict]:
    """전 종목 통합 리스트 (표시용)."""
    result = []
    for h in snap.get("overseas_general", {}).get("holdings_usd", []):
        result.append({**h, "_account": "일반"})
    for h in snap.get("overseas_fractional", {}).get("holdings", []):
        result.append({**h, "_account": "소수점"})
    for h in snap.get("domestic", {}).get("holdings", []):
        result.append({**h, "_account": "국내"})
    return result


# ══════════════════════════════════════════════════════════════════════
#  공개 API
# ══════════════════════════════════════════════════════════════════════

def _rt_ret(h: dict):
    """실시간 신선시 (실시간 return%, True), 아니면 (스냅샷 return%, False). 예외 무발."""
    snap_ret = h.get("return_pct", 0) or 0
    try:
        from providers import realtime_quotes
        avg = float(h.get("avg_price_usd") or 0)
        if realtime_quotes.enabled() and avg > 0:
            cur = realtime_quotes.get_price(str(h.get("ticker", "")).split(".")[0])
            if cur and cur > 0:
                return (float(cur) - avg) / avg * 100.0, True
    except Exception:
        pass
    return snap_ret, False


def list_holdings() -> str:
    """현재 보유 종목 텍스트 출력. 해외는 실시간 시세 신선시 수익률 오버레이(⚡)·아니면 스냅샷."""
    snap = _load()
    if not snap:
        return "⚠️ portfolio_snapshot.json 로드 실패"

    today = snap.get("snapshot_date", "?")
    lines = [
        f"📋 보유 종목 현황  ({today})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    live_count = 0

    # 해외 일반
    gen = snap.get("overseas_general", {}).get("holdings_usd", [])
    if gen:
        lines.append("  [해외 일반계좌]")
        for h in gen:
            ret, live = _rt_ret(h)
            live_count += int(live)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h['ticker']:<6}  {h.get('shares', 0)}주  "
                f"@${h.get('avg_price_usd', 0):.2f}  "
                f"{sign}{abs(ret):.1f}%{'⚡' if live else ''}"
            )

    # 소수점
    frac = snap.get("overseas_fractional", {}).get("holdings", [])
    if frac:
        lines.append("  [소수점계좌]")
        for h in frac:
            ret, live = _rt_ret(h)
            live_count += int(live)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h['ticker']:<6}  {h.get('shares', 0):.4f}주  "
                f"{sign}{abs(ret):.1f}%{'⚡' if live else ''}"
            )

    # 국내 (KR 실시간은 별도 스트림 — 현재는 스냅샷 기준)
    dom = snap.get("domestic", {}).get("holdings", [])
    if dom:
        lines.append("  [국내계좌]")
        for h in dom:
            ret  = h.get("return_pct", 0)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h.get('ticker', h.get('name','?')):<12}  {h.get('shares', 0)}주  "
                f"{sign}{abs(ret):.1f}%"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    if live_count:
        lines.append(f"  ⚡ {live_count}종목 실시간 · 그 외 스냅샷({today}) · 전체 평가 /portfolio")
    else:
        lines.append(f"  📸 스냅샷({today}) 기준 — 실시간 평가는 /portfolio")
    return "\n".join(lines)


def buy_holding(ticker: str, shares: float, price_usd: float,
                fractional: bool = False) -> str:
    """
    매수 기록: 기존 포지션 있으면 평단가 재계산, 없으면 신규 추가.
    fractional=True 이면 소수점 계좌에 기록.
    """
    ticker = ticker.upper()
    snap   = _load()
    if not snap:
        return "❌ portfolio_snapshot.json 로드 실패"

    section = "overseas_fractional" if fractional else "overseas_general"
    key     = "holdings" if fractional else "holdings_usd"

    snap.setdefault(section, {}).setdefault(key, [])
    holdings = snap[section][key]

    # 기존 포지션 탐색
    existing = next((h for h in holdings if h.get("ticker", "").upper() == ticker), None)

    if existing:
        old_shares = float(existing.get("shares", 0))
        old_avg    = float(existing.get("avg_price_usd", existing.get("cost_usd", 0) / old_shares if old_shares > 0 else price_usd))
        new_shares = old_shares + shares
        new_avg    = (old_shares * old_avg + shares * price_usd) / new_shares if new_shares > 0 else price_usd

        existing["shares"]          = round(new_shares, 4)
        existing["avg_price_usd"]   = round(new_avg, 4)
        existing["cost_usd"]        = round(new_shares * new_avg, 4)
        existing.pop("current_price_usd", None)   # 삭제 → 다음 실행 시 갱신
        existing.pop("pnl_usd", None)
        existing.pop("return_pct", None)
        msg = (
            f"✅ 매수 기록\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  종목    {ticker}\n"
            f"  추가    {shares}주  @${price_usd:.2f}\n"
            f"  총 보유 {new_shares:.4f}주\n"
            f"  평단가  ${new_avg:.2f}  (재계산)"
        )
    else:
        new_entry = {
            "name":          ticker,
            "ticker":        ticker,
            "shares":        round(shares, 4),
            "avg_price_usd": round(price_usd, 4),
            "cost_usd":      round(shares * price_usd, 4),
        }
        holdings.append(new_entry)
        msg = (
            f"✅ 신규 포지션 추가\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  종목    {ticker}\n"
            f"  수량    {shares}주  @${price_usd:.2f}\n"
            f"  계좌    {'소수점' if fractional else '일반'}"
        )

    _save(snap)

    # 매수 후 전체 가격 갱신 (신규 종목 포함)
    refresh_msg = refresh_portfolio_prices()
    return msg + f"\n\n{refresh_msg}"


def sell_holding(ticker: str, shares: float | None = None) -> str:
    """
    매도 기록.
    shares=None 이면 전량 청산.
    두 계좌(일반 + 소수점) 모두 탐색.
    """
    ticker = ticker.upper()
    snap   = _load()
    if not snap:
        return "❌ portfolio_snapshot.json 로드 실패"

    sold_any = False
    msgs     = []

    for section, key in [("overseas_general", "holdings_usd"),
                          ("overseas_fractional", "holdings")]:
        holdings = snap.get(section, {}).get(key, [])
        for i, h in enumerate(holdings):
            if h.get("ticker", "").upper() != ticker:
                continue
            existing_shares = float(h.get("shares", 0))
            sell_qty = existing_shares if shares is None else min(shares, existing_shares)

            if sell_qty >= existing_shares:
                # 전량 청산
                snap[section][key].pop(i)
                msgs.append(f"  [{section.replace('overseas_', '')}] {ticker} 전량 청산 ({existing_shares:.4f}주)")
            else:
                h["shares"] = round(existing_shares - sell_qty, 4)
                h["cost_usd"] = round(h["shares"] * h.get("avg_price_usd", 0), 4)
                msgs.append(f"  [{section.replace('overseas_', '')}] {ticker} {sell_qty:.4f}주 매도  →  잔여 {h['shares']:.4f}주")
            sold_any = True
            break

    if not sold_any:
        return f"❌ {ticker} 포지션을 찾을 수 없습니다."

    _save(snap)

    # 전량 청산으로 포지션이 완전히 사라졌으면 은퇴 티커로 기록
    # → 일일 스모크 감사가 코드·설정에 남은 죽은 참조를 점검한다
    if _find_holding(snap, ticker)[2] is None:
        try:
            from portfolio_universe import record_retired_ticker
            record_retired_ticker(ticker)
            msgs.append(f"  🪦 {ticker} 은퇴 티커 등록 — 잔존 참조는 일일 감사가 점검")
        except Exception:
            pass

    # 매도 후 전체 가격 갱신
    refresh_msg = refresh_portfolio_prices()
    return "✅ 매도 기록\n━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(msgs) + f"\n\n{refresh_msg}"


# ══════════════════════════════════════════════════════════════════════
#  DCA 비중 관리
# ══════════════════════════════════════════════════════════════════════

def get_dca_weights() -> tuple[dict, dict]:
    """현재 DCA 비중 반환 (barbell_strategy에서 임포트)."""
    from barbell_strategy import load_dca_weights
    return load_dca_weights()


def set_dca_weights(updates: dict, mode: str = "normal") -> str:
    """
    DCA 비중 업데이트.
    updates: {"ORCL": 24, "NVDA": 20, "MSFT": 18, ...}  (퍼센트 또는 소수점)
    mode: "normal" | "bear"
    """
    from barbell_strategy import save_dca_weights, load_dca_weights, DCA_WEIGHTS_FILE
    import json

    w_normal, w_bear = load_dca_weights()

    # 값 정규화 (100 이상이면 % → 소수점)
    normalized = {}
    for k, v in updates.items():
        v = float(v)
        normalized[k.upper()] = v / 100 if v > 1 else v

    if mode == "bear":
        target = {**w_bear, **normalized}
    else:
        target = {**w_normal, **normalized}

    # 0 이하 항목 제거 (삭제 처리)
    target = {k: v for k, v in target.items() if v > 0}

    # 합계 정규화
    total = sum(target.values())
    if total <= 0:
        return "❌ 유효한 비중이 없습니다."
    target = {k: round(v / total, 4) for k, v in target.items()}

    if mode == "bear":
        save_dca_weights(w_normal, target)
    else:
        save_dca_weights(target, w_bear)

    lines = [f"✅ DCA 비중 업데이트 ({mode})", "━━━━━━━━━━━━━━━━━━━━━━━"]
    for ticker, w in sorted(target.items(), key=lambda x: -x[1]):
        amt = int(40_000 * w)
        lines.append(f"  {ticker:<6}  {w*100:.1f}%  ({amt:,}원/일)")
    return "\n".join(lines)


def refresh_portfolio_prices() -> str:
    """
    portfolio_snapshot.json 의 모든 보유 종목 현재가를 yfinance로 갱신.
    매수/매도 후 자동 호출되어 리포트가 항상 최신 상태를 반영.
    """
    snap = _load()
    if not snap:
        return "❌ 스냅샷 로드 실패"

    # 갱신할 티커 수집
    all_tickers: set[str] = set()
    for h in snap.get("overseas_general", {}).get("holdings_usd", []):
        all_tickers.add(h["ticker"])
    for h in snap.get("overseas_fractional", {}).get("holdings", []):
        all_tickers.add(h["ticker"])

    if not all_tickers:
        return "보유 종목 없음"

    # yfinance 일괄 조회
    import yfinance as yf
    import numpy as np

    prices: dict[str, float] = {}
    tickers = list(all_tickers)
    try:
        data = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        if not data.empty and "Close" in data.columns:
            close = data["Close"]
            if hasattr(close, "columns"):
                for t in tickers:
                    if t in close.columns:
                        s = close[t].dropna()
                        if not s.empty:
                            prices[t] = round(float(s.iloc[-1]), 2)
            else:
                s = data["Close"].dropna()
                if not s.empty:
                    prices[tickers[0]] = round(float(s.iloc[-1]), 2)
    except Exception:
        # fallback: 개별 조회
        for t in tickers:
            try:
                h = yf.Ticker(t).history(period="2d")
                if not h.empty:
                    prices[t] = round(float(h["Close"].iloc[-1]), 2)
            except Exception:
                pass

    if not prices:
        return "❌ 가격 조회 실패"

    # 스냅샷 업데이트
    updated = 0
    for h in snap.get("overseas_general", {}).get("holdings_usd", []):
        t = h["ticker"]
        if t not in prices:
            continue
        p   = prices[t]
        sh  = float(h.get("shares", 0))
        avg = float(h.get("avg_price_usd", p))
        h["current_price_usd"] = p
        h["value_usd"]         = round(sh * p, 4)
        h["cost_usd"]          = round(sh * avg, 4)
        h["pnl_usd"]           = round(sh * p - sh * avg, 4)
        h["return_pct"]        = round((p - avg) / avg * 100, 2) if avg > 0 else 0
        updated += 1

    for h in snap.get("overseas_fractional", {}).get("holdings", []):
        t = h["ticker"]
        if t not in prices:
            continue
        p  = prices[t]
        sh = float(h.get("shares", 0))
        h["value_usd"] = round(sh * p, 4)
        updated += 1

    _save(snap)
    return f"✅ {updated}개 종목 가격 갱신  ({datetime.now().strftime('%H:%M')} KST)"


def set_target_weight(updates: dict) -> str:
    """
    목표 비중 업데이트.
    updates: {"ORCL": 0.07, "AMD": 0.04}  (소수점) 또는 {"ORCL": 7, "AMD": 4} (%)
    """
    from barbell_strategy import save_target_weights, load_target_weights

    # 값 정규화
    normalized = {}
    for k, v in updates.items():
        v = float(v)
        normalized[k.upper()] = round(v / 100 if v > 1 else v, 4)

    save_target_weights(normalized)

    # 결과 표시
    all_targets = load_target_weights()
    lines = ["🎯 목표 비중 업데이트 완료", "━━━━━━━━━━━━━━━━━━━━━━━"]
    for t, w in sorted(all_targets.items(), key=lambda x: -x[1]):
        if t.startswith("_"):
            continue
        bar = "█" * int(w / 0.10 * 8) + "░" * (8 - int(w / 0.10 * 8))
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%")
    return "\n".join(lines)


def show_target_weights(portfolio: dict | None = None) -> str:
    """현재 목표 비중 표시 (보유 종목 기준 자동 추론 포함)."""
    from barbell_strategy import load_target_weights
    targets = load_target_weights(portfolio)
    if not targets:
        return "목표 비중 설정 없음\n/holding target TICKER WEIGHT% 로 설정"

    lines = ["🎯 목표 비중 현황", "━━━━━━━━━━━━━━━━━━━━━━━"]

    # 파일에 명시적 설정된 것
    from barbell_strategy import TARGET_WEIGHTS_FILE
    explicit = set()
    try:
        with open(TARGET_WEIGHTS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        explicit = {k for k in raw if not k.startswith("_")}
    except Exception:
        pass

    for t, w in sorted(targets.items(), key=lambda x: -x[1]):
        bar  = "█" * int(w / 0.10 * 8) + "░" * (8 - int(w / 0.10 * 8))
        tag  = "" if t in explicit else "  (자동 추론)"
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%{tag}")

    lines += [
        "",
        "수정: /holding target TICKER 비중% TICKER 비중% ...",
        "예) /holding target AMD 5 AMZN 4 PLTR 3",
    ]
    return "\n".join(lines)


def show_dca_weights() -> str:
    """DCA 비중 현황 출력."""
    w_normal, w_bear = get_dca_weights()
    lines = ["💸 현재 DCA 비중", "━━━━━━━━━━━━━━━━━━━━━━━", "  [정상 Phase 0~1]"]
    max_n = max(w_normal.values(), default=1.0)
    for t, w in sorted(w_normal.items(), key=lambda x: -x[1]):
        amt = int(40_000 * w)
        n = round(w / max_n * 8)
        bar = "█" * n + "░" * (8 - n)
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%  {amt:,}원")
    lines += ["", "  [하락 Phase 2+]"]
    max_b = max(w_bear.values(), default=1.0)
    for t, w in sorted(w_bear.items(), key=lambda x: -x[1]):
        amt = int(40_000 * w)
        n = round(w / max_b * 8)
        bar = "█" * n + "░" * (8 - n)
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%  {amt:,}원")
    return "\n".join(lines)
