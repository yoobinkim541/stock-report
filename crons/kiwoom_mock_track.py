#!/usr/bin/env python3
"""
kiwoom_mock_track.py — 국내주식 자동 페이퍼트레이딩 루프 (키움 모의투자).

흐름:
  1) 코스피 신호 수집 (score_ticker + detect_signals + _decision_v2 → 매수/매도 분류)
  2) 모의계좌 잔고 조회 (kiwoom_mock.get_balance)
  3) 목표 바스켓(상위 매수신호 N종목 균등) vs 현재 보유 → 리밸런스 주문계획(plan_rebalance)
  4) 모의 시장가 주문 집행 (kiwoom_mock.place_order — 모의 도메인 하드락)
  5) NAV·체결결과 기록(store) + 텔레그램 요약

안전:
  - KIWOOM_MOCK_ENABLED=true 가 아니면 아무것도 안 함(신청 전 오작동 방지).
  - 주문은 전부 모의계좌(kiwoom_mock). 실거래 경로 없음.
  - 봇 자동 집행은 **모의 한정** — 실계좌 자동매매는 이 모듈에 존재하지 않음.
  - 잔고조회 실패 시 매수 보류(블라인드 풀바스켓 매수 방지), 음수예산 유령매도 차단,
    매수 총액은 가용현금 한도 + 슬리피지 버퍼로 캡, 주문은 즉시 기록(크래시 감사추적).
  - 크론은 flock 으로 중복/재실행 집행 방지.

알려진 제약 (라이브 모의계좌 연결 후 확정/보강):
  - 현금(예수금) 요약 필드명은 kt00018 라이브 응답으로 확정 필요 — 미확인 시 get_balance 가
    응답 키를 로깅하고 cash_krw=None 으로 보수 동작(추정예탁자산으로 NAV 근사).
  - KRX 휴장일 가드는 없음 — 휴장일엔 주문이 거부될 뿐(모의·무해, 로그만). 정밀 캘린더가
    필요하면 exchange_calendars('XKRX') 도입 검토.

크론 (평일 00:30 UTC = 09:30 KST, 장 개장 직후·리포트 이후):
    30 0 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/kiwoom_mock_track.py

env(선택, 기본값):
    KR_MOCK_UNIVERSE   20   (코스피 스캔 상위 N)
    KR_MOCK_MAX_POS    5    (보유 목표 종목 수)
    KR_MOCK_INVEST     0.9  (투자비중 — 나머지는 현금 버퍼)
    KIWOOM_MOCK_SEED   10000000  (NAV 미확인 시 가정 시드, 원)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import kiwoom_mock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


UNIVERSE   = _int_env("KR_MOCK_UNIVERSE", 20)
MAX_POS    = _int_env("KR_MOCK_MAX_POS", 5)
INVEST     = _float_env("KR_MOCK_INVEST", 0.9)
SEED_KRW   = _float_env("KIWOOM_MOCK_SEED", 10_000_000)
SLIPPAGE   = _float_env("KR_MOCK_SLIPPAGE", 0.01)   # 매수 사이징 슬리피지 버퍼(+1%)

_BUY_ACTIONS  = ("강한 매수후보", "관심/분할매수")
_SELL_ACTIONS = ("매도검토", "손절/매도검토")


# ── 신호 수집 ─────────────────────────────────────────────────────────────────

def compute_kr_signals(limit: int = UNIVERSE) -> list[dict]:
    """코스피 상위 universe 의 매수/매도 신호 + 현재가.

    반환: [{ticker, code, action, score, price, is_buy, is_sell}, ...]
    (기존 일일 리포트와 동일한 _decision_v2 의사결정을 재사용)
    """
    from reports.investment_report import KOSPI_TOP30, _decision_v2
    from reports.fundamental_score import score_ticker
    from reports.daily_signals import detect_signals

    out = []
    for tk in KOSPI_TOP30[:limit]:
        try:
            fund = score_ticker(tk)
            sig  = detect_signals(tk)
            dec  = _decision_v2(fund, sig, fund.get("grade", "N/A"), ticker=tk)
            price = float((sig.get("price_info") or {}).get("current_price") or 0)
            action = dec.get("action", "")
            out.append({
                "ticker": tk,
                "code":   tk.replace(".KS", "").replace(".KQ", ""),
                "action": action,
                "score":  int(fund.get("total_score", 0) or 0),
                "price":  price,
                "is_buy":  action in _BUY_ACTIONS,
                "is_sell": action in _SELL_ACTIONS,
            })
        except Exception as e:
            logger.warning("KR 신호 실패 %s: %s", tk, e)
    return out


# ── 리밸런스 (순수 함수 — 테스트 핵심) ────────────────────────────────────────

def plan_rebalance(signals: list[dict], positions: dict, budget_krw: float,
                   max_positions: int, cash_krw: float | None = None,
                   slippage: float = 0.0) -> list[dict]:
    """목표 바스켓 vs 현재 보유 → 시장가 주문계획.

    signals:   [{code, action, score, price, is_buy, is_sell}, ...]
    positions: {code: {shares, cur_price, ...}}
    cash_krw:  알려진 가용현금(없으면 None) — 매수 총액을 현금으로 러닝 캡.
    slippage:  매수 사이징 시 가격에 더할 버퍼(예: 0.01 = +1%) — 시가 갭/슬리피지 흡수.
    반환:      [{code, side('buy'|'sell'), qty, reason}, ...]

    규칙:
      - 목표 = is_buy 중 score 상위 max_positions, 균등배분(budget/N).
      - 매도 = 보유 중 목표 바스켓에 없는 종목 전량 (매도신호 종목은 is_buy 가 아니라
        애초에 목표에서 빠지므로 자동 청산됨). **매도는 항상 먼저(현금 확보).**
      - 매수/조정 = 목표 종목별 목표주수까지 delta. 예산 0/음수면 매수 생략(유령매도 방지),
        가용현금 알면 그 한도까지만(over-spend 방지).
    """
    orders: list[dict] = []
    buys = sorted(
        [s for s in signals if s.get("is_buy") and s.get("price", 0) > 0],
        key=lambda s: -s.get("score", 0),
    )[:max_positions]
    target_codes = {s["code"] for s in buys}

    # 1) 매도 먼저: 보유 중 목표 바스켓에 없는 종목 전량 (현금 확보)
    for code, p in positions.items():
        sh = int(p.get("shares", 0) or 0)
        if sh > 0 and code not in target_codes:
            orders.append({"code": code, "side": "sell", "qty": sh, "reason": "타깃이탈"})

    # 2) 매수/조정: 예산 0/음수면 전면 생략 (음수 예산 → 유령매도 방지)
    per = (budget_krw / len(buys)) if (buys and budget_krw > 0) else 0.0
    remaining = cash_krw if (cash_krw is not None and cash_krw > 0) else None
    for s in buys:
        code, price = s["code"], s["price"]
        if per <= 0 or price <= 0:
            continue
        cur = int(positions.get(code, {}).get("shares", 0) or 0)
        eff_price = price * (1.0 + max(0.0, slippage))   # 슬리피지 버퍼
        tgt = int(per // eff_price)
        if remaining is not None:                         # 가용현금 러닝 캡
            tgt = min(tgt, cur + int(remaining // eff_price))
        tgt = max(0, tgt)                                  # 음수 목표 클램프(유령매도 차단)
        delta = tgt - cur
        if delta > 0:
            orders.append({"code": code, "side": "buy", "qty": delta, "reason": "신규/추가"})
            if remaining is not None:
                remaining -= delta * eff_price
        elif delta < 0:
            orders.append({"code": code, "side": "sell", "qty": -delta, "reason": "비중축소"})
    return orders


# ── 기록 ──────────────────────────────────────────────────────────────────────

def _append_history(rec: dict) -> None:
    """kr_mock_history 에 레코드 1건 즉시 적재 (스냅샷/주문 — 크래시에도 감사추적 보존)."""
    try:
        import store
        store.append("kr_mock_history", {"date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), **rec})
    except Exception as e:
        logger.warning("모의 기록 실패: %s", e)


# ── 텔레그램 요약 ─────────────────────────────────────────────────────────────

def _notify(nav: float | None, results: list[dict], signals: list[dict]) -> None:
    name_by_code = {}
    try:
        from reports.investment_report import _company_name
        name_by_code = {s["code"]: _company_name(s["ticker"]) for s in signals}
    except Exception:
        pass

    lines = ["🧪 [모의] 국내 페이퍼트레이딩"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    if nav is not None:
        lines.append(f"  추정 NAV  ₩{nav:,.0f}")
    placed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    if not results:
        lines.append("  주문 없음 (목표 = 현 보유)")
    for r in results:
        nm = name_by_code.get(r["code"], "")
        mark = "✅" if r.get("ok") else "❌"
        side = "매수" if r["side"] == "buy" else "매도"
        lines.append(f"  {mark} {side} {r['code']} {nm} {r['qty']}주 · {r.get('reason','')}")
        if not r.get("ok") and r.get("msg"):
            lines.append(f"     ↳ {r['msg']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"  집행 {len(placed)} · 실패 {len(failed)}")
    lines.append("  ⚠️ 모의투자 — 실거래 아님")

    try:
        import notify
        notify.send_telegram("\n".join(lines),
                             token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> int:
    logger.info("=== kiwoom_mock_track 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))

    if not kiwoom_mock.is_enabled():
        logger.info("KIWOOM_MOCK_ENABLED 아님 — 모의 페이퍼트레이딩 생략 (모의투자 신청 후 활성화)")
        return 0

    bal = kiwoom_mock.get_balance()
    if not bal["ok"]:
        # 잔고를 모르면 매수하면 안 됨(빈 보유로 오인 → 블라인드 풀바스켓 매수 방지)
        logger.error("모의 잔고 조회 실패 — 주문 보류")
        return 1
    positions = bal["positions"]
    cash = bal["cash_krw"]
    nav  = bal["nav"]
    if nav is None:
        nav = bal["pos_value"] or SEED_KRW   # 요약필드 전부 미확인 → 보유액/시드 근사
        logger.info("NAV 근사치 사용: ₩%s", f"{nav:,.0f}")

    signals = compute_kr_signals(UNIVERSE)
    if not signals:
        logger.warning("신호 0건 — 종료")
        return 0

    budget = nav * INVEST
    plan = plan_rebalance(signals, positions, budget, MAX_POS, cash_krw=cash, slippage=SLIPPAGE)
    logger.info("리밸런스 계획 %d건 (예산 ₩%s, 현금 %s, 목표 %d종목)",
                len(plan), f"{budget:,.0f}",
                f"₩{cash:,.0f}" if cash is not None else "미확인", MAX_POS)

    # 시작 스냅샷 먼저 기록(크래시에도 당일 NAV 시계열 보존)
    _append_history({"kind": "snapshot", "nav": nav, "cash": cash,
                     "positions": len([p for p in positions.values() if int(p.get("shares", 0) or 0) > 0])})

    results = []
    for o in plan:
        r = kiwoom_mock.place_order(o["code"], o["qty"], o["side"])
        results.append({**o, **r})
        logger.info("%s %s %s주 → %s %s",
                    o["side"], o["code"], o["qty"], "OK" if r.get("ok") else "FAIL", r.get("msg", ""))
        # 주문별 즉시 기록 — 루프 도중 크래시해도 집행 내역 감사추적 남김(#멱등/감사)
        _append_history({"kind": "order", "code": o["code"], "side": o["side"], "qty": o["qty"],
                         "reason": o.get("reason"), "ok": r.get("ok"), "msg": r.get("msg")})
        time.sleep(0.3)   # 레이트리밋 여유

    _notify(nav, results, signals)
    logger.info("=== 완료: 집행 %d건 ===", sum(1 for r in results if r.get("ok")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
