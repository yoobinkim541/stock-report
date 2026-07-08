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
import fmt      # 출력 포맷 공통 레이어
from lib import trade_events

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


def _save_locked(snap: dict):
    """호출자가 이미 file_write_lock 을 보유한 상태의 원자 쓰기 (락 재획득 없음).

    mutator(buy/sell/refresh)가 load→mutate→write 를 하나의 락으로 감싸 lost update 를
    막을 때 사용한다. flock 은 같은 프로세스라도 재획득 시 데드락이므로 여기선 잡지 않는다.
    """
    snap["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")
    safe_io.atomic_write_json(PORTFOLIO_PATH, snap)
    # store 그림자 사본 (user_id 스코프 — 멀티유저 기반). 파일이 권위.
    store.shadow_doc("portfolio_snapshot", snap)


def _save(snap: dict):
    """단독 쓰기용 — file_write_lock 획득 후 원자 쓰기.

    교차 프로세스 쓰기 락(safe_io.file_write_lock)으로 kiwoom_sync_rest·
    portfolio_sync_server 와 동시 쓰기 시 lost update 를 방지한다. read-modify-write 를
    통째로 보호하려면 mutator 가 직접 file_write_lock 을 잡고 _save_locked 를 호출한다.
    """
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        _save_locked(snap)


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
        fmt.sep(),
    ]
    live_count = 0

    # 해외 일반
    gen = snap.get("overseas_general", {}).get("holdings_usd", [])
    if gen:
        lines.append(fmt.sep("해외 일반계좌"))
        for h in gen:
            ret, live = _rt_ret(h)
            live_count += int(live)
            lines.append(
                f"{h['ticker']} {h.get('shares', 0)}주 @${h.get('avg_price_usd', 0):.2f}  "
                f"{fmt.spct(ret)}{' ⚡' if live else ''}"
            )

    # 소수점
    frac = snap.get("overseas_fractional", {}).get("holdings", [])
    if frac:
        lines.append(fmt.sep("소수점계좌"))
        for h in frac:
            ret, live = _rt_ret(h)
            live_count += int(live)
            lines.append(
                f"{h['ticker']} {h.get('shares', 0):.4f}주  "
                f"{fmt.spct(ret)}{' ⚡' if live else ''}"
            )

    # 국내 (KR 실시간은 별도 스트림 — 현재는 스냅샷 기준)
    dom = snap.get("domestic", {}).get("holdings", [])
    if dom:
        lines.append(fmt.sep("국내계좌"))
        for h in dom:
            ret = h.get("return_pct", 0)
            lines.append(
                f"{h.get('ticker', h.get('name','?'))} {h.get('shares', 0)}주  {fmt.spct(ret)}"
            )

    lines.append(fmt.sep())
    if live_count:
        lines.append(f"  ⚡ {live_count}종목 실시간 · 그 외 스냅샷({today}) · 전체 평가 /portfolio")
    else:
        lines.append(f"  📸 스냅샷({today}) 기준 — 실시간 평가는 /portfolio")
    return "\n".join(lines)


def buy_holding(ticker: str, shares: float, price_usd: float,
                fractional: bool = False, note: str | None = None) -> str:
    """
    매수 기록: 기존 포지션 있으면 평단가 재계산, 없으면 신규 추가.
    fractional=True 이면 소수점 계좌에 기록.
    """
    ticker = ticker.upper()
    trade_rec = None
    # load→mutate→write 를 하나의 파일락으로 감싸 교차 프로세스(kiwoom_sync·sync_server) lost update 방지
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        snap = _load()
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
            trade_rec = {
                "ticker": ticker, "side": "buy", "qty": shares, "price": price_usd,
                "avg_price": new_avg, "account": "overseas_fractional" if fractional else "overseas_general",
                "source": "manual_holding", "market": "US", "currency": "USD",
                "note": note or "holding buy",
            }
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
            trade_rec = {
                "ticker": ticker, "side": "buy", "qty": shares, "price": price_usd,
                "avg_price": price_usd, "account": "overseas_fractional" if fractional else "overseas_general",
                "source": "manual_holding", "market": "US", "currency": "USD",
                "note": note or "holding buy new",
            }
            msg = (
                f"✅ 신규 포지션 추가\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  종목    {ticker}\n"
                f"  수량    {shares}주  @${price_usd:.2f}\n"
                f"  계좌    {'소수점' if fractional else '일반'}"
            )

        _save_locked(snap)

    if trade_rec:
        trade_events.record_trade(**trade_rec)

    # 매수 후 전체 가격 갱신 (신규 종목 포함) — refresh 는 자체 락 획득
    refresh_msg = refresh_portfolio_prices()
    return msg + f"\n\n{refresh_msg}"


def sell_holding(ticker: str, shares: float | None = None, price_usd: float | None = None) -> str:
    """
    매도 기록.
    shares=None 이면 전량 청산.
    두 계좌(일반 + 소수점) 모두 탐색.
    """
    ticker = ticker.upper()
    trade_recs = []
    # load→mutate→write 를 하나의 파일락으로 감싸 교차 프로세스 lost update 방지
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        snap = _load()
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
                avg = float(h.get("avg_price_usd") or 0) or None
                px = price_usd or h.get("current_price_usd") or avg

                if sell_qty >= existing_shares:
                    # 전량 청산
                    snap[section][key].pop(i)
                    msgs.append(f"  [{section.replace('overseas_', '')}] {ticker} 전량 청산 ({existing_shares:.4f}주)")
                else:
                    h["shares"] = round(existing_shares - sell_qty, 4)
                    h["cost_usd"] = round(h["shares"] * h.get("avg_price_usd", 0), 4)
                    msgs.append(f"  [{section.replace('overseas_', '')}] {ticker} {sell_qty:.4f}주 매도  →  잔여 {h['shares']:.4f}주")
                sold_any = True
                trade_recs.append({
                    "ticker": ticker,
                    "side": "sell",
                    "qty": sell_qty,
                    "price": px,
                    "avg_price": avg,
                    "account": section,
                    "source": "manual_holding",
                    "market": "US",
                    "currency": "USD",
                    "note": "holding sell",
                })
                break

        if not sold_any:
            return f"❌ {ticker} 포지션을 찾을 수 없습니다."

        _save_locked(snap)

    for rec in trade_recs:
        trade_events.record_trade(**rec)

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


def undo_trade(event_id: str) -> str:
    """최신 수동 기록 1건 역산 복원 (대시보드 되돌리기 — 기록 전용·실주문 아님).

    안전 가드: ① manual_holding 만(동기화·모의 거부) ② 해외 수동 계좌만(국내는
    kiwoom_sync 가 권위) ③ 해당 티커 **최신** 수동 이벤트만(이후 평단 재계산 보호)
    ④ 평단 일치 검증(이중 undo·외부 변경 방어 — 절대 완화 금지). 반복 클릭 시
    ④가 자연 차단(멱등). 성공 시 원장 이벤트 제거 + 가격 갱신.
    """
    ev = next((r for r in trade_events.all_trades()
               if r.get("event_id") == event_id), None)
    if not ev:
        return "❌ 기록을 찾을 수 없습니다."
    if str(ev.get("source") or "") != "manual_holding":
        return "❌ 수동 기록만 되돌릴 수 있습니다 (동기화·모의 기록 불가)."
    account = str(ev.get("account") or "")
    if account not in ("overseas_general", "overseas_fractional"):
        return "❌ 해외 수동 계좌 기록만 되돌릴 수 있습니다 (국내는 증권사 동기화가 권위)."
    ticker = str(ev.get("ticker") or "").upper()
    latest = trade_events.latest_manual_event(ticker)
    if not latest or latest.get("event_id") != event_id:
        return "❌ 최신 기록만 되돌릴 수 있습니다 (이후 기록의 평단 정합 보호)."
    try:
        qty = float(ev.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    price = ev.get("price")
    side = str(ev.get("side") or "")
    if qty <= 0:
        return "❌ 수량 정보가 없어 되돌릴 수 없습니다."

    section = "overseas_fractional" if account == "overseas_fractional" else "overseas_general"
    key = "holdings" if section == "overseas_fractional" else "holdings_usd"

    with safe_io.file_write_lock(PORTFOLIO_PATH):
        snap = _load()
        if not snap:
            return "❌ portfolio_snapshot.json 로드 실패"
        holdings = snap.setdefault(section, {}).setdefault(key, [])
        existing = next((h for h in holdings
                         if str(h.get("ticker", "")).upper() == ticker), None)
        if side == "buy":
            if existing is None:
                return "❌ 보유 내역이 없어 매수 기록을 되돌릴 수 없습니다."
            cur_shares = float(existing.get("shares", 0))
            cur_avg = float(existing.get("avg_price_usd", 0) or 0)
            ev_avg = ev.get("avg_price")
            if ev_avg is None or abs(cur_avg - float(ev_avg)) > 0.01:
                return ("❌ 현재 평단이 기록 시점과 달라 되돌릴 수 없습니다 "
                        "(이미 되돌렸거나 외부 변경 — 안전상 중단).")
            if cur_shares + 1e-6 < qty:
                return "❌ 보유 수량이 기록 수량보다 적어 되돌릴 수 없습니다."
            old_shares = round(cur_shares - qty, 4)
            if old_shares <= 1e-4:
                holdings.remove(existing)               # 신규 매수 취소 → 포지션 제거
            else:
                if price is None:
                    return "❌ 체결가 정보가 없어 평단을 복원할 수 없습니다."
                old_avg = (cur_avg * cur_shares - qty * float(price)) / old_shares
                if old_avg <= 0:
                    return "❌ 평단 역산 결과가 비정상 — 안전상 중단."
                existing["shares"] = old_shares
                existing["avg_price_usd"] = round(old_avg, 4)
                existing["cost_usd"] = round(old_shares * round(old_avg, 4), 4)
                for k in ("current_price_usd", "pnl_usd", "return_pct"):
                    existing.pop(k, None)
        else:                                           # 매도 되돌리기
            ev_avg = ev.get("avg_price") if ev.get("avg_price") is not None else price
            if existing is None:                        # 전량 매도 복원
                if ev_avg is None:
                    return "❌ 평단 정보가 없어 전량 매도 기록을 복원할 수 없습니다."
                holdings.append({"ticker": ticker, "shares": round(qty, 4),
                                 "avg_price_usd": round(float(ev_avg), 4),
                                 "cost_usd": round(qty * float(ev_avg), 4)})
            else:                                       # 부분 매도 복원 (평단 불변)
                cur_avg = float(existing.get("avg_price_usd", 0) or 0)
                if (ev.get("avg_price") is not None
                        and abs(cur_avg - float(ev["avg_price"])) > 0.01):
                    return ("❌ 현재 평단이 기록 시점과 달라 되돌릴 수 없습니다 "
                            "(이미 되돌렸거나 외부 변경 — 안전상 중단).")
                new_shares = round(float(existing.get("shares", 0)) + qty, 4)
                existing["shares"] = new_shares
                existing["cost_usd"] = round(new_shares * cur_avg, 4)
                for k in ("current_price_usd", "pnl_usd", "return_pct"):
                    existing.pop(k, None)
        _save_locked(snap)

    removed = trade_events.remove_event(event_id)
    refresh_msg = refresh_portfolio_prices()
    warn = ("" if removed else
            "\n⚠️ 원장 이벤트 제거 실패 — 차트 마커가 남을 수 있음(평단 검증이 중복 복원은 차단)")
    return (f"↩️ 되돌리기 완료 — {ticker} {'매수' if side == 'buy' else '매도'} "
            f"{qty:g}주 기록 취소{warn}\n\n{refresh_msg}")


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

    # yfinance 조회(수 초)는 락 밖에서 끝냈다. 이제 최신 스냅샷을 재로드해 가격만 적용하고
    # 한 락으로 저장 → 다운로드 중 들어온 도메스틱/외부 잔고 갱신을 보존(lost update 방지).
    with safe_io.file_write_lock(PORTFOLIO_PATH):
        snap = _load()
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

        _save_locked(snap)
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
