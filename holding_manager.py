#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
holding_manager.py — portfolio_snapshot.json CRUD + DCA 비중 관리

텔레그램 /holding 명령어의 백엔드.
portfolio_snapshot.json과 dca_weights.json을 직접 수정한다.
"""

import json
import os
from datetime import datetime

PORTFOLIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_snapshot.json")


# ══════════════════════════════════════════════════════════════════════
#  내부 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _load() -> dict:
    try:
        return json.loads(open(PORTFOLIO_PATH, encoding="utf-8").read())
    except Exception:
        return {}


def _save(snap: dict):
    snap["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)


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

def list_holdings() -> str:
    """현재 보유 종목 텍스트 출력."""
    snap = _load()
    if not snap:
        return "⚠️ portfolio_snapshot.json 로드 실패"

    today = snap.get("snapshot_date", "?")
    lines = [
        f"📋 보유 종목 현황  ({today})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # 해외 일반
    gen = snap.get("overseas_general", {}).get("holdings_usd", [])
    if gen:
        lines.append("  [해외 일반계좌]")
        for h in gen:
            ret  = h.get("return_pct", 0)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h['ticker']:<6}  {h.get('shares', 0)}주  "
                f"@${h.get('avg_price_usd', 0):.2f}  "
                f"{sign}{abs(ret):.1f}%"
            )

    # 소수점
    frac = snap.get("overseas_fractional", {}).get("holdings", [])
    if frac:
        lines.append("  [소수점계좌]")
        for h in frac:
            ret  = h.get("return_pct", 0)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h['ticker']:<6}  {h.get('shares', 0):.4f}주  "
                f"{sign}{abs(ret):.1f}%"
            )

    # 국내
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
    return msg


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
    return "✅ 매도 기록\n━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(msgs)


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
    updates: {"NOW": 18, "ORCL": 18, "CRM": 10, ...}  (퍼센트 또는 소수점)
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


def show_dca_weights() -> str:
    """DCA 비중 현황 출력."""
    w_normal, w_bear = get_dca_weights()
    lines = ["💸 현재 DCA 비중", "━━━━━━━━━━━━━━━━━━━━━━━", "  [정상 Phase 0~1]"]
    for t, w in sorted(w_normal.items(), key=lambda x: -x[1]):
        amt = int(40_000 * w)
        bar = "█" * int(w / 0.25 * 8) + "░" * (8 - int(w / 0.25 * 8))
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%  {amt:,}원")
    lines += ["", "  [하락 Phase 2+]"]
    for t, w in sorted(w_bear.items(), key=lambda x: -x[1]):
        amt = int(40_000 * w)
        bar = "█" * int(w / 0.25 * 8) + "░" * (8 - int(w / 0.25 * 8))
        lines.append(f"  {t:<6}  {bar}  {w*100:.1f}%  {amt:,}원")
    return "\n".join(lines)
