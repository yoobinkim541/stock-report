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
REBAL_BAND = _float_env("KR_MOCK_REBAL_BAND", 0.25)  # 무거래 밴드(목표比 ±25% 벗어날 때만 조정·회전율↓)
EXIT_BUFFER = _int_env("KR_MOCK_EXIT_BUFFER", 2)     # 히스테리시스(top-N+2 안이면 보유 유지·경계 flip 방지)
# ★최소 보유기간 — backtest/kr_policy_backtest 비용 OOS 실증(슬로우 신호 과잉거래가 순수익 ~2.4%p
# 잠식·최소보유가 gross 보존하며 비용만 절감·반기>월간 64% 연도·cross-axis=ROBUST). **모의 활성**:
# 기본 60(OOS 권고값 반영·모의로 라이브 검증). KR_MOCK_MIN_HOLD_DAYS=0 으로 되돌리면 현행 무제한 회전.
# 모의 한정 — 실계좌 자동 경로 없음. 꼬리위험(2023 등) 있어 실계좌는 모의 검증 후 수동 판단.
MIN_HOLD_DAYS = _int_env("KR_MOCK_MIN_HOLD_DAYS", 60)
# ★분할매수·분할매도 — 회당 목표의 1/N 만 거래(N회에 평균진입/청산). 분산 축소(알파 아님)·
# 모의 bps 비용 불변. 기본 3(분할 활성)·1=현행 일괄. min_hold(청산 지연)와 독립 합성.
TRANCHES = _int_env("KR_MOCK_TRANCHES", 3)
# 부분체결 스텁 예외(B) — 트란치 빌드 중 신호 이탈로 목표의 이 비율 미만인 반쪽 포지션은
# min_hold 보호에서 제외(청산 허용). 없으면 저비중 잔재가 최대 60일 자본 잠식.
STUB_EXEMPT_FRAC = _float_env("KR_MOCK_STUB_FRAC", 0.5)
QUOTE_STALE_S = _int_env("REALTIME_QUOTE_STALE_S", 10)


def _rt_best(code: str, side: str):
    """실시간 우호가(매수=ask·매도=bid) — 활성·신선시. 없으면 None(정적 슬리피지 폴백). 시장가 주문이라 사이징만 개선."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            return realtime_quotes.best(code, side, max_age_s=QUOTE_STALE_S)
    except Exception:
        pass
    return None

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

    from ml import kr_policy

    out = []
    for tk in KOSPI_TOP30[:limit]:
        try:
            fund = score_ticker(tk)
            sig  = detect_signals(tk)
            dec  = _decision_v2(fund, sig, fund.get("grade", "N/A"), ticker=tk)
            price = float((sig.get("price_info") or {}).get("current_price") or 0)
            action = dec.get("action", "")
            feats = kr_policy.extract_features(fund, sig, dec)
            # ★가격 축(mom12·hi52·lowvol) — 12M 수정주가 point-in-time (그래이스풀:
            # 이력 부족/네트워크 실패 시 미기록 → score() 가 사용분만 재정규화)
            try:
                from providers.market_data import _history_cached
                h = _history_cached(tk, period="1y")
                if h is not None and "Close" in getattr(h, "columns", []):
                    feats.update(kr_policy.price_axes(h["Close"]))
            except Exception:
                pass
            # ★LLM 뉴스 구조화 축 — news_llm_snapshot 라벨 집계 (없으면 미기록 → 재정규화)
            try:
                from providers import news_labels
                na = news_labels.news_axis(tk)
                if na is not None:
                    feats["news"] = na
            except Exception:
                pass
            out.append({
                "ticker": tk,
                "code":   tk.replace(".KS", "").replace(".KQ", ""),
                "action": action,
                "score":  int(fund.get("total_score", 0) or 0),
                "price":  price,
                "is_buy":  action in _BUY_ACTIONS,
                "is_sell": action in _SELL_ACTIONS,
                # 근거(원장·리포트용) + point-in-time 피처(학습 입력)
                "rationale": {
                    "one_line_reason": dec.get("one_line_reason", ""),
                    "confidence": dec.get("confidence"),
                    "grade": fund.get("grade", "N/A"),
                    "financial": (dec.get("financial") or {}).get("status"),
                    "timing": (dec.get("timing") or {}).get("status"),
                    "news": (dec.get("news") or {}).get("status"),
                    "risk": (dec.get("risk") or {}).get("status"),
                },
                "features": feats,
            })
        except Exception as e:
            logger.warning("KR 신호 실패 %s: %s", tk, e)

    # KR ranker(가치모델) 점수를 횡단면 정규화해 피처에 주입 → policy_score 산출
    try:
        from ml import kr_ranker
        raw = kr_ranker.kr_scores_by_ticker(top_n=max(limit, len(out)))
        vals = [raw[s["ticker"]] for s in out if s["ticker"] in raw]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
        rng = (hi - lo) or 1.0
        for s in out:
            rv = raw.get(s["ticker"])
            if rv is not None:
                s["features"]["ranker"] = round((rv - lo) / rng, 4)   # [0,1] 정규화
    except Exception as e:
        logger.warning("KR ranker 점수 주입 실패(폴백: 규칙 가중만): %s", e)

    params = kr_policy.load_params()
    for s in out:
        s["policy_score"] = round(kr_policy.score(s["features"], params), 6)
    return out


def held_days_from_decisions(decisions: list[dict], codes, today: str) -> dict:
    """보유 종목별 보유일수 = today − 가장 최근 편입일 (원장 결정에서). 순수·테스트 핵심.

    같은 종목 재편입 시 최신 편입일 기준(중간 청산 후 재진입은 새 보유). 편입 기록 없으면 제외.
    """
    import datetime as _dt
    entry: dict[str, str] = {}
    for d in decisions or []:
        if d.get("side") == "편입":
            c = d.get("code") or (d.get("ticker") or "").replace(".KS", "").replace(".KQ", "")
            dt = str(d.get("date", ""))[:10]
            if c and dt and dt > entry.get(c, ""):
                entry[c] = dt
    out = {}
    try:
        t0 = _dt.date.fromisoformat(today[:10])
    except Exception:
        return out
    for c in codes:
        e = entry.get(c)
        if not e:
            continue
        try:
            out[c] = (t0 - _dt.date.fromisoformat(e)).days
        except Exception:
            continue
    return out


def _held_days(positions: dict) -> dict:
    """라이브 보유 종목의 보유일수 (원장 편입일 기준). 실패 시 {} (→ 최소보유 미적용)."""
    try:
        from datetime import datetime
        from ml.adaptive import Ledger
        held = [c for c, p in positions.items() if int(p.get("shares", 0) or 0) > 0]
        return held_days_from_decisions(Ledger("kr_mock").read_decisions(), held,
                                        datetime.now(KST).strftime("%Y-%m-%d"))
    except Exception as e:
        logger.warning("보유일수 산출 실패(최소보유 미적용): %s", e)
        return {}


# ── 리밸런스 (순수 함수 — 테스트 핵심) ────────────────────────────────────────

def plan_rebalance(signals: list[dict], positions: dict, budget_krw: float,
                   max_positions: int, cash_krw: float | None = None,
                   slippage: float = 0.0, quote_fn=None,
                   rebal_band: float = 0.0, exit_buffer: int = 0,
                   min_hold_days: int = 0, held_days: dict | None = None,
                   stub_frac: float = 0.0) -> list[dict]:
    """목표 바스켓 vs 현재 보유 → 시장가 주문계획.

    signals:   [{code, action, score, price, is_buy, is_sell}, ...]
    positions: {code: {shares, cur_price, ...}}
    cash_krw:  알려진 가용현금(없으면 None) — 매수 총액을 현금으로 러닝 캡.
    slippage:  매수 사이징 시 가격에 더할 버퍼(예: 0.01 = +1%) — 시가 갭/슬리피지 흡수.
    rebal_band: >0 이면 보유종목 조정을 |현재가치−목표가치|/목표가치 > band 일 때만(잔챙이 조정 skip·회전율↓).
    exit_buffer: >0 이면 보유종목이 top-(N+buffer) 안이면 유지(경계 flip-flop 방지·회전율↓).
    min_hold_days: >0 이면 편입 후 이 일수 미만 보유 종목은 타깃이탈이어도 청산 보류(회전율↓).
      **★backtest/kr_policy_backtest 실증**: 슬로우 신호(hi52 등) 과잉거래가 순수익을 연 ~2.4%p
      갉아먹음 → 최소 보유기간이 gross 보존하며 비용만 절감(OOS 64% 연도·cross-axis·gross 보존).
      held_days: {code: 보유일수} (main 이 원장 편입일에서 산출). 기본 0 = 현행(무제한 회전).
    stub_frac: >0 이면 포지션 가치 < (budget/max_positions)×비율 인 스텁(트란치 빌드 중
      이탈한 반쪽 포지션)은 min_hold 보호 제외 → 청산 허용(저비중 잔재 자본잠식 방지·B).
    반환:      [{code, side('buy'|'sell'), qty, reason}, ...]

    규칙:
      - 목표 = is_buy 중 score 상위 max_positions, 균등배분(budget/N).
      - 매도 = 보유 중 목표 바스켓에 없는 종목 전량 (매도신호 종목은 is_buy 가 아니라
        애초에 목표에서 빠지므로 자동 청산됨). **매도는 항상 먼저(현금 확보).**
      - 매수/조정 = 목표 종목별 목표주수까지 delta. 예산 0/음수면 매수 생략(유령매도 방지),
        가용현금 알면 그 한도까지만(over-spend 방지).
    """
    orders: list[dict] = []
    held_days = held_days or {}
    # 랭킹 = 학습된 정책 점수(policy_score) 우선, 없으면 펀더멘털 score 폴백
    ranked = sorted(
        [s for s in signals if s.get("is_buy") and s.get("price", 0) > 0],
        key=lambda s: -(s.get("policy_score") if s.get("policy_score") is not None else s.get("score", 0) / 100.0),
    )
    buys = ranked[:max_positions]
    # 히스테리시스: 매도는 top-(N+buffer) 밖 종목만 (경계 flip-flop 방지)
    keep_codes = {s["code"] for s in ranked[:max_positions + max(0, exit_buffer)]}

    # 1) 매도 먼저: 보유 중 keep(top-N+buffer) 밖 종목 전량 (현금 확보)
    #    단 min_hold_days>0 이면 편입 후 그 일수 미만 종목은 청산 보류(회전율 억제)
    #    — 스텁(목표의 stub_frac 미만 반쪽 포지션)은 보호 제외(트란치 빌드 중 이탈분 정리·B)
    per_target = (budget_krw / max(max_positions, 1)) if budget_krw > 0 else 0.0
    for code, p in positions.items():
        sh = int(p.get("shares", 0) or 0)
        if sh > 0 and code not in keep_codes:
            if min_hold_days > 0 and 0 <= held_days.get(code, 10 ** 9) < min_hold_days:
                val = sh * float(p.get("cur_price", 0) or 0)
                is_stub = stub_frac > 0 and per_target > 0 and val < stub_frac * per_target
                if not is_stub:
                    continue   # 최소 보유기간 미충족(제대로 빌드된 포지션) — 유지
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
        if quote_fn:                                      # 라이브 호가(ask) 있으면 실제 체결가로 사이징
            try:
                q = quote_fn(code, "buy")
            except Exception:
                q = None
            if q and q > 0:
                eff_price = q
        # 무거래 밴드: 이미 보유 중이고 목표 대비 band 이내면 조정 skip (신규 진입은 항상 매수)
        if rebal_band > 0 and cur > 0 and abs(cur * eff_price - per) <= rebal_band * per:
            continue
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


def _classify_kind(side: str, qty: int, cur_shares: int) -> str:
    """편입(신규 매수)/증액/퇴출(전량 매도)/감액 분류."""
    if side == "buy":
        return "편입" if cur_shares <= 0 else "증액"
    return "퇴출" if qty >= cur_shares else "감액"


def _log_decision(ledger, sig: dict, code: str, kind: str, order_side: str,
                  qty: int, ok, today: str) -> None:
    """결정+근거를 불변 원장(kr_decisions.jsonl)에 적재 + 편입/퇴출은 MD 저널에도."""
    try:
        ledger.log_decision({
            "date": today,
            "ticker": sig.get("ticker", f"{code}.KS"),
            "code": code,
            "side": kind,                         # 편입/증액/퇴출/감액
            "order_side": order_side,             # buy/sell
            "qty": qty,
            "price": sig.get("price"),
            "action": sig.get("action"),
            "policy_score": sig.get("policy_score"),
            "score": sig.get("score"),
            "rationale": sig.get("rationale"),
            "features": sig.get("features"),      # point-in-time — 학습 입력
            "ok": ok,
        })
        if kind in ("편입", "퇴출"):
            icon = "📥" if kind == "편입" else "📤"
            rr = (sig.get("rationale") or {}).get("one_line_reason", "")
            ledger.append_journal(
                today, f"- {today} {icon} {kind} {code} {sig.get('action','')} — {rr} (점수 {sig.get('score','')})")
    except Exception as e:
        logger.warning("결정 원장 기록 실패 %s: %s", code, e)


# ── 텔레그램 요약 ─────────────────────────────────────────────────────────────

# 개별 주문이 아니라 '전 주문이 동일하게 막히는' 계좌/시장 레벨 상황 신호
_ACCOUNT_SIGNS = ("종료된 계좌", "다시 신청", "개인공매도", "공매도이수", "RC4091", "RC5006", "계좌번호를 확인")
_MARKET_SIGNS = ("장종료", "장 종료", "RC4058", "장운영시간")


def _order_blocker(msg) -> str | None:
    """주문 실패 사유가 '전 주문 공통 차단'인지 분류 — 개별 주문 문제(부족·틱 등)와 구분.

    'account' = 계좌 종료/유형/미신청 → 재신청 필요 · 'market' = 장 마감 → 장중 재시도 · None = 개별.
    """
    m = str(msg or "")
    if any(s in m for s in _ACCOUNT_SIGNS):
        return "account"
    if any(s in m for s in _MARKET_SIGNS):
        return "market"
    return None


def _notify(nav: float | None, results: list[dict], signals: list[dict]) -> None:
    name_by_code, reason_by_code = {}, {}
    try:
        from reports.investment_report import _company_name
        name_by_code = {s["code"]: _company_name(s["ticker"]) for s in signals}
    except Exception:
        pass
    reason_by_code = {s["code"]: (s.get("rationale") or {}).get("one_line_reason", "") for s in signals}

    lines = ["🧪 [모의] 국내 페이퍼트레이딩", "━━━━━━━━━━━━━━━━━━━━━━━"]
    if nav is not None:
        lines.append(f"  추정 NAV  ₩{nav:,.0f}")

    # 계좌/시장 레벨 차단이면 개별 실패 도배 대신 명확 안내 1건
    blocker = next((_order_blocker(r.get("msg")) for r in results
                    if not r.get("ok") and _order_blocker(r.get("msg"))), None)
    if blocker == "account":
        emsg = next(r.get("msg") for r in results if _order_blocker(r.get("msg")) == "account")
        lines += ["  ⚠️ 키움 모의계좌 문제 — 주문 중단", f"     ↳ {emsg}",
                  "  👉 키움에서 '주식 모의투자(국내)' 재신청·활성화 필요",
                  "━━━━━━━━━━━━━━━━━━━━━━━"]
    elif blocker == "market":
        lines += ["  ⚠️ 장 마감 — 주문 보류 (장중 09:00~15:30 KST 자동 재시도)",
                  "━━━━━━━━━━━━━━━━━━━━━━━"]
    else:
        _KIND_ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖"}
        placed = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        if not results:
            lines.append("  주문 없음 (목표 = 현 보유)")
        for r in results:
            nm = name_by_code.get(r["code"], "")
            mark = "✅" if r.get("ok") else "❌"
            kind = r.get("kind", "매수" if r["side"] == "buy" else "매도")
            icon = _KIND_ICON.get(kind, "")
            lines.append(f"  {mark} {icon}{kind} {r['code']} {nm} {r['qty']}주")
            # 편입/퇴출은 사유(one_line_reason) 병기
            if kind in ("편입", "퇴출"):
                rr = reason_by_code.get(r["code"], "")
                if rr:
                    lines.append(f"     사유: {rr}")
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

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    logger.info("=== kiwoom_mock_track 시작 [%s]%s ===",
                datetime.now(KST).strftime("%Y-%m-%d %H:%M"), " [DRY-RUN]" if dry else "")

    # dry-run 은 미리보기(주문 미집행)라 비활성 상태에서도 허용 — 켜기 전 계획 확인용
    if not dry and not kiwoom_mock.is_enabled():
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
    held_days = _held_days(positions) if MIN_HOLD_DAYS > 0 else {}
    plan = plan_rebalance(signals, positions, budget, MAX_POS, cash_krw=cash,
                          slippage=SLIPPAGE, quote_fn=_rt_best,
                          rebal_band=REBAL_BAND, exit_buffer=EXIT_BUFFER,
                          min_hold_days=MIN_HOLD_DAYS, held_days=held_days,
                          stub_frac=STUB_EXEMPT_FRAC)
    # ★분할매수/매도: 각 주문을 회당 목표의 1/N 로 상한 (N회에 평균 진입·청산)
    if TRANCHES > 1 and plan:
        from lib.tranche import plan_tranches
        _px = {s["code"]: s.get("price") for s in signals if s.get("code")}
        for c, p in positions.items():
            _px.setdefault(c, p.get("cur_price"))
        plan = plan_tranches(plan, budget / max(MAX_POS, 1), lambda c: _px.get(c), TRANCHES,
                             id_key="code")
    logger.info("리밸런스 계획 %d건 (예산 ₩%s, 현금 %s, 목표 %d종목, %d분할)",
                len(plan), f"{budget:,.0f}",
                f"₩{cash:,.0f}" if cash is not None else "미확인", MAX_POS, TRANCHES)

    if dry:
        logger.info("[DRY-RUN] 주문 미집행 — 계획만 출력:")
        for o in plan:
            logger.info("  [DRY] %s %s %s주 · %s", o["side"], o["code"], o["qty"], o.get("reason"))
        if not plan:
            logger.info("  [DRY] 주문 없음 (목표 = 현 보유)")
        return 0

    # 시작 스냅샷 먼저 기록(크래시에도 당일 NAV 시계열 보존)
    _append_history({"kind": "snapshot", "nav": nav, "cash": cash,
                     "positions": len([p for p in positions.values() if int(p.get("shares", 0) or 0) > 0])})

    from ml.adaptive import Ledger
    ledger = Ledger("kr_mock")
    sig_by_code = {s["code"]: s for s in signals}
    today = datetime.now(KST).strftime("%Y-%m-%d")

    from ml.adaptive import costs
    results = []
    day_cost = day_notional = 0.0
    for o in plan:
        cur = int(positions.get(o["code"], {}).get("shares", 0) or 0)
        kind = _classify_kind(o["side"], o["qty"], cur)
        r = kiwoom_mock.place_order(o["code"], o["qty"], o["side"])
        results.append({**o, "kind": kind, **r})
        logger.info("%s(%s) %s %s주 → %s %s",
                    o["side"], kind, o["code"], o["qty"], "OK" if r.get("ok") else "FAIL", r.get("msg", ""))
        # 체결분 거래비용 적립 (수수료+증권거래세 — 회전율 드래그 정직 계기)
        if r.get("ok"):
            px = (sig_by_code.get(o["code"], {}).get("price")
                  or positions.get(o["code"], {}).get("cur_price") or 0)
            notion = abs(o["qty"]) * float(px or 0)
            day_notional += notion
            day_cost += costs.order_cost(notion, o["side"], "KR")
        # 주문별 즉시 기록 — store(현재뷰) + 불변 원장(학습/감사, 절대 삭제 안 함)
        _append_history({"kind": "order", "code": o["code"], "side": o["side"], "qty": o["qty"],
                         "reason": o.get("reason"), "ok": r.get("ok"), "msg": r.get("msg")})
        _log_decision(ledger, sig_by_code.get(o["code"], {}), o["code"], kind, o["side"],
                      o["qty"], r.get("ok"), today)
        if not r.get("ok") and _order_blocker(r.get("msg")):
            logger.error("전 주문 공통 차단(%s) 감지 — 남은 주문 중단: %s",
                         _order_blocker(r.get("msg")), r.get("msg"))
            break
        time.sleep(0.5)   # 레이트리밋 여유 (모의 주문 API 429 완화 · _post 는 429 재시도)

    if day_notional > 0:                       # 당일 거래비용 1건 적재 (리포트 누적·회전율용)
        _append_history({"kind": "cost", "cost": round(day_cost, 2), "notional": round(day_notional, 2)})
    _notify(nav, results, signals)
    logger.info("=== 완료: 집행 %d건 ===", sum(1 for r in results if r.get("ok")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
