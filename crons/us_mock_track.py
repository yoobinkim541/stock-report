#!/usr/bin/env python3
"""us_mock_track.py — 미국주식 자동 페이퍼트레이딩 루프 (KIS 해외 모의투자). kiwoom_mock_track 해외판.

흐름:
  1) US 유니버스 선택신호 (us_policy + ranker best-effort → policy_score)
  2) 모의계좌 잔고 (kis_mock.get_balance — 모의 도메인 하드락)
  3) 목표 바스켓(상위 N 균등) vs 보유 → 정수주 리밸런스(plan_rebalance)
  4) 모의 지정가 주문 (kis_mock.place_order)
  5) 결정+판단근거 불변원장(Ledger "us_mock") + NAV 기록 + 텔레그램

안전:
  - KOREA_MOCK_ENABLED=true 아니면 아무것도 안 함(dry-run 제외). 주문은 전부 모의계좌(kis_mock).
  - 실거래 경로 없음. 잔고 실패 시 매수 보류. flock 중복집행 방지(크론).
  - 정수주만(해외) — 분수 floor, 비중 드리프트 원장 기록.

★정직: 6티어가 US 선택 무엣지 입증 → 이 루프는 *정직 측정 + OOS 안전개선*용. 알파 보장 아님.

크론 (평일 15:00 UTC = 미 개장 후·연중안전: 여름 11:00 ET·겨울 10:00 ET → 당일 체결):
    0 15 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/us_mock_track.py

env: US_MOCK_UNIVERSE(쉼표 티커, 기본 내장)·US_MOCK_MAX_POS(5)·US_MOCK_INVEST(0.9)·KOREA_MOCK_SEED(100000 USD)
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

import kis_mock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 기본 US 선택 유니버스 (시장 유니버스 — 보유종목 아님). ticker-ok
_DEFAULT_UNIVERSE = ["MSFT", "NVDA", "GOOGL", "AAPL", "AMZN", "META", "AVGO", "ORCL",   # ticker-ok
                     "AMD", "ADBE", "CRM", "NFLX", "QCOM", "TXN", "INTC", "CSCO",   # ticker-ok
                     "PEP", "COST", "AMAT", "MU"]   # ticker-ok (시장 유니버스 — 보유종목 아님)


def _int_env(n, d):
    try:
        return int(os.getenv(n, str(d)))
    except ValueError:
        return d


def _float_env(n, d):
    try:
        return float(os.getenv(n, str(d)))
    except ValueError:
        return d


def _universe() -> list[str]:
    raw = os.getenv("US_MOCK_UNIVERSE", "")
    return [t.strip().upper() for t in raw.split(",") if t.strip()] or _DEFAULT_UNIVERSE


MAX_POS = _int_env("US_MOCK_MAX_POS", 5)
INVEST = _float_env("US_MOCK_INVEST", 0.9)
SEED_USD = _float_env("KOREA_MOCK_SEED", 100_000)
SLIPPAGE = _float_env("US_MOCK_SLIPPAGE", 0.01)
QUOTE_STALE_S = _int_env("REALTIME_QUOTE_STALE_S", 10)


def _rt_best(sym: str, side: str):
    """실시간 우호가(매수=ask·매도=bid) — 활성·신선시. 없으면 None(정적 슬리피지/신호가 폴백)."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            return realtime_quotes.best(sym, side, max_age_s=QUOTE_STALE_S)
    except Exception:
        pass
    return None


# ── 선택 신호 (런타임·네트워크) ────────────────────────────────────────────────

def compute_us_signals(universe: list[str] | None = None) -> list[dict]:
    """유니버스별 us_policy 점수 + 현재가 + 판단근거. ranker 는 best-effort(있으면 횡단정규화 주입)."""
    from ml import us_policy
    universe = universe or _universe()
    out = []
    for tk in universe:
        try:
            earnings = _safe_earnings(tk)
            sig = _safe_signals(tk)
            fund = _safe_fund(tk)
            feats = us_policy.extract_features(fund, earnings, sig)
            price = float((sig.get("price_info") or {}).get("current_price") or 0) or (kis_mock.get_price(tk) or 0)
            out.append({
                "ticker": tk, "price": price, "features": feats,
                "rationale": {
                    "one_line_reason": f"value {feats['value']:.2f}·quality {feats['quality']:.2f}·mom {feats['mom']:.2f}",
                    "per": earnings.get("per"), "pbr": earnings.get("pbr"), "roe": earnings.get("roe"),
                },
            })
        except Exception as e:
            logger.warning("US 신호 실패 %s: %s", tk, e)

    # US ranker(가치모델) best-effort → 횡단면 정규화 주입
    try:
        from ml import ranker
        raw = ranker.scores_by_ticker([s["ticker"] for s in out]) if hasattr(ranker, "scores_by_ticker") else {}
        vals = [raw[s["ticker"]] for s in out if s["ticker"] in raw]
        if vals:
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            for s in out:
                if s["ticker"] in raw:
                    s["features"]["ranker"] = round((raw[s["ticker"]] - lo) / rng, 4)
    except Exception as e:
        logger.info("US ranker 주입 생략(폴백: 규칙 가중만): %s", e)

    params = us_policy.load_params()
    for s in out:
        s["policy_score"] = round(us_policy.score(s["features"], params), 6)
    return out


def _safe_earnings(tk: str) -> dict:
    try:
        from providers import earnings_data
        return earnings_data.valuation_metrics(tk) or {}
    except Exception:
        return {}


def _safe_signals(tk: str) -> dict:
    try:
        from reports.daily_signals import detect_signals
        return detect_signals(tk) or {}
    except Exception:
        return {}


def _safe_fund(tk: str) -> dict:
    try:
        from reports.fundamental_score import score_ticker
        return score_ticker(tk) or {}
    except Exception:
        return {}


# ── 리밸런스 (순수 함수 — 테스트 핵심) ────────────────────────────────────────

def plan_rebalance(signals: list[dict], positions: dict, budget_usd: float,
                   max_positions: int, cash_usd: float | None = None,
                   slippage: float = 0.0, quote_fn=None) -> list[dict]:
    """목표 바스켓(policy_score 상위 N 균등) vs 보유 → 정수주 지정가 주문계획.

    반환: [{symbol, side('buy'|'sell'), qty, reason}]. 매도 먼저(현금확보)·예산0/음수면 매수생략·현금 러닝캡.
    """
    orders: list[dict] = []
    buys = sorted([s for s in signals if s.get("price", 0) > 0],
                  key=lambda s: -(s.get("policy_score") or 0))[:max_positions]
    target = {s["ticker"] for s in buys}

    for sym, p in positions.items():
        sh = int(p.get("shares", 0) or 0)
        if sh > 0 and sym not in target:
            orders.append({"symbol": sym, "side": "sell", "qty": sh, "reason": "타깃이탈"})

    per = (budget_usd / len(buys)) if (buys and budget_usd > 0) else 0.0
    remaining = cash_usd if (cash_usd is not None and cash_usd > 0) else None
    for s in buys:
        sym, price = s["ticker"], s["price"]
        if per <= 0 or price <= 0:
            continue
        cur = int(positions.get(sym, {}).get("shares", 0) or 0)
        eff = price * (1.0 + max(0.0, slippage))
        if quote_fn:                                       # 라이브 호가(ask) 있으면 실제 체결가로 사이징
            try:
                q = quote_fn(sym, "buy")
            except Exception:
                q = None
            if q and q > 0:
                eff = q
        tgt = int(per // eff)                              # 정수주 floor
        if remaining is not None:
            tgt = min(tgt, cur + int(remaining // eff))
        tgt = max(0, tgt)
        delta = tgt - cur
        if delta > 0:
            orders.append({"symbol": sym, "side": "buy", "qty": delta, "reason": "신규/추가"})
            if remaining is not None:
                remaining -= delta * eff
        elif delta < 0:
            orders.append({"symbol": sym, "side": "sell", "qty": -delta, "reason": "비중축소"})
    return orders


def _classify_kind(side: str, qty: int, cur_shares: int) -> str:
    if side == "buy":
        return "편입" if cur_shares <= 0 else "증액"
    return "퇴출" if qty >= cur_shares else "감액"


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    logger.info("=== us_mock_track 시작 [%s]%s ===",
                datetime.now(KST).strftime("%Y-%m-%d %H:%M"), " [DRY-RUN]" if dry else "")
    if not dry and not kis_mock.is_enabled():
        logger.info("KOREA_MOCK_ENABLED 아님 — US 모의 페이퍼트레이딩 생략")
        return 0

    if not dry:
        bal = kis_mock.get_balance()
        if not bal["ok"]:
            logger.error("KIS 모의 잔고 조회 실패 — 주문 보류")
            return 1
        positions, cash, nav = bal["positions"], bal["cash_usd"], bal["nav"]
    else:
        positions, cash, nav = {}, SEED_USD, SEED_USD    # dry-run: 시드 가정 미리보기
    if nav is None:
        nav = (bal["pos_value"] if not dry else 0) or SEED_USD

    signals = compute_us_signals()
    if not signals:
        logger.warning("US 신호 0건 — 종료")
        return 0
    budget = nav * INVEST
    plan = plan_rebalance(signals, positions, budget, MAX_POS, cash_usd=cash,
                          slippage=SLIPPAGE, quote_fn=_rt_best)
    logger.info("리밸런스 계획 %d건 (예산 $%.0f·목표 %d종목)", len(plan), budget, MAX_POS)

    if dry:
        for o in plan:
            logger.info("  [DRY] %s %s %s주 · %s", o["side"], o["symbol"], o["qty"], o.get("reason"))
        if not plan:
            logger.info("  [DRY] 주문 없음")
        return 0

    from ml.adaptive import Ledger
    ledger = Ledger("us_mock")
    sig_by = {s["ticker"]: s for s in signals}
    today = datetime.now(KST).strftime("%Y-%m-%d")
    results = []
    for o in plan:
        cur = int(positions.get(o["symbol"], {}).get("shares", 0) or 0)
        kind = _classify_kind(o["side"], o["qty"], cur)
        s = sig_by.get(o["symbol"], {})
        px = _rt_best(o["symbol"], o["side"]) or s.get("price") or kis_mock.get_price(o["symbol"]) or 0
        r = kis_mock.place_order(o["symbol"], o["qty"], o["side"], price=px)
        results.append({**o, "kind": kind, **r})
        _log_decision(ledger, s, o["symbol"], kind, o["side"], o["qty"], r.get("ok"), today)
        logger.info("%s(%s) %s %s주 → %s %s", o["side"], kind, o["symbol"], o["qty"],
                    "OK" if r.get("ok") else "FAIL", r.get("msg", ""))
        time.sleep(0.4)   # KIS 레이트리밋
    _record_snapshot(nav, cash, positions)
    _notify(nav, results)
    logger.info("=== 완료: 집행 %d건 ===", sum(1 for r in results if r.get("ok")))
    return 0


def _log_decision(ledger, sig, sym, kind, order_side, qty, ok, today):
    try:
        ledger.log_decision({
            "date": today, "ticker": sym, "side": kind, "order_side": order_side, "qty": qty,
            "price": sig.get("price"), "policy_score": sig.get("policy_score"),
            "rationale": sig.get("rationale"), "features": sig.get("features"), "ok": ok,
        })
        if kind in ("편입", "퇴출"):
            icon = "📥" if kind == "편입" else "📤"
            rr = (sig.get("rationale") or {}).get("one_line_reason", "")
            ledger.append_journal(today, f"- {today} {icon} {kind} {sym} — {rr} (정책 {sig.get('policy_score','')})")
    except Exception as e:
        logger.warning("결정 원장 기록 실패 %s: %s", sym, e)


def _record_snapshot(nav, cash, positions):
    try:
        import store
        store.append("us_mock_history", {
            "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot",
            "nav": nav, "cash": cash,
            "positions": len([p for p in positions.values() if int(p.get("shares", 0) or 0) > 0])})
    except Exception as e:
        logger.warning("US 모의 스냅샷 기록 실패: %s", e)


def _notify(nav, results):
    _ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖"}
    lines = ["🧪 [모의] 미국 페이퍼트레이딩 (KIS 해외)", "━━━━━━━━━━━━━━"]
    if nav is not None:
        lines.append(f"  NAV  ${nav:,.0f}")
    if not results:
        lines.append("  주문 없음 (목표 = 보유)")
    for r in results:
        mark = "✅" if r.get("ok") else "❌"
        lines.append(f"  {mark} {_ICON.get(r.get('kind'), '')}{r.get('kind')} {r['symbol']} {r['qty']}주")
        if not r.get("ok") and r.get("msg"):
            lines.append(f"     ↳ {r['msg']}")
    lines.append(f"  집행 {sum(1 for r in results if r.get('ok'))} · 실패 {sum(1 for r in results if not r.get('ok'))}")
    lines.append("  ⚠️ 모의투자 — 실거래 아님")
    try:
        import notify
        notify.send_telegram("\n".join(lines), token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


if __name__ == "__main__":
    sys.exit(main())
