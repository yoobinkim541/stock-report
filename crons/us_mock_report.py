#!/usr/bin/env python3
"""us_mock_report.py — 미국 모의 페이퍼트레이딩 일일 현황 + 로직 평가 스코어카드. kiwoom_mock_report 해외판.

★목표 가시화: 누적 초과수익(vs QQQ) + MDD(전략 vs 지수) + **로직 평가 스코어카드**(편입/퇴출 적중률·실현 IC).
= 사용자 요청 "로직이 맞게 작용하는지 평가". build_report()는 크론·/usmock 공용. read-only.

★정직: 선택 무엣지면 적중률 ~50%·IC ~0 으로 그대로 표시(과대광고 0).
크론 (평일 21:30 UTC = 미장 마감 후):
    30 21 * * 1-5 cd <repo> && flock -n /tmp/us_mock_report.lock uv run python crons/us_mock_report.py
"""
from __future__ import annotations

import logging
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import kis_mock
import fmt
from lib import mock_llm_execution as llm_exec
from lib import mock_llm_rationale as llm_rationale

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SEED_USD = float(os.getenv("KOREA_MOCK_SEED", "100000"))
_WD = ["월", "화", "수", "목", "금", "토", "일"]
_FACTOR_LABELS = {"value": "밸류", "quality": "퀄리티", "mom": "모멘텀"}
_FACTOR_RE = re.compile(r"\b(value|quality|mom)\s*([+-]?\d+(?:\.\d+)?)", re.I)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).lower() in ("1", "true", "yes", "on")


def _detail_mode(detail: bool | None) -> bool:
    if detail is not None:
        return bool(detail)
    return _env_bool("US_MOCK_REPORT_DETAIL", _env_bool("MOCK_REPORT_DETAIL", False))


def _position_payload(sym, p, total_value):
    value = p.get("value", 0) or 0
    avg = p.get("avg_price", 0) or 0
    cur = p.get("cur_price", 0) or 0
    ret = (cur - avg) / avg * 100 if avg > 0 else 0.0
    return {
        "ticker": sym,
        "weight_pct": round(value / total_value * 100.0, 1) if total_value else 0.0,
        "return_pct": ret,
        "value": value,
    }


def _decision_payload(d):
    return {
        "side": d.get("side"),
        "ticker": d.get("ticker"),
        "reason": (d.get("rationale") or {}).get("one_line_reason", ""),
        "policy_score": d.get("policy_score"),
    }


def _cash_line(cash: float | None, nav: float, bal: dict) -> str | None:
    if cash is None:
        return None
    cash_w = cash / nav * 100.0 if nav else 0.0
    weight = f" · 현금비중 {cash_w:.1f}%"
    if bal.get("cash_derived") and bal.get("fx"):
        return (f"여유 증거금 {fmt.money(cash)}{weight} (원화 환산·USD 예수금 "
                f"{fmt.money(bal.get('usd_deposit') or 0.0)}·환율 ₩{bal['fx']:,.0f})")
    return f"현금 {fmt.money(cash)}{weight}"


def _state_interpretation(
    cum_ret: float,
    q_ret: float | None,
    strat_mdd: float,
    q_mdd_pct: float | None,
    cash: float | None,
    nav: float,
) -> list[str]:
    lines: list[str] = []
    if q_ret is None:
        lines.append("QQQ 벤치 데이터가 없어 상대성과는 보류합니다.")
    else:
        excess = cum_ret - q_ret
        if excess >= 0 and q_mdd_pct is not None and strat_mdd <= q_mdd_pct:
            lines.append(f"QQQ 대비 {fmt.pct(excess)}p 앞서고 MDD도 낮아 방어 우위입니다.")
        elif excess >= 0:
            lines.append(f"QQQ 대비 {fmt.pct(excess)}p 앞서지만 낙폭 통제는 계속 확인해야 합니다.")
        else:
            lines.append(f"QQQ 대비 {fmt.pct(excess)}p 뒤처져 종목 선택과 현금비중 점검이 필요합니다.")

    if cash is not None and nav:
        cash_w = cash / nav * 100.0
        if cash_w >= 30:
            lines.append(f"현금 {cash_w:.1f}%는 하락 방어에 도움되지만 반등장에서는 지연 요인입니다.")
        elif cash_w >= 10:
            lines.append(f"현금 {cash_w:.1f}%로 신규 편입 여력은 남아 있습니다.")
        else:
            lines.append(f"현금 {cash_w:.1f}%라 추가 매수 여력은 제한적입니다.")

    if abs(cum_ret) < 1.0:
        lines.append("누적 성과는 아직 보합권입니다.")
    return lines[:3]


def _holding_stats(held: dict) -> list[dict]:
    stats = []
    for sym, p in held.items():
        avg = p.get("avg_price", 0) or 0
        cur = p.get("cur_price", 0) or 0
        sh = int(p.get("shares", 0) or 0)
        val = p.get("value", 0) or 0
        ret = (cur - avg) / avg * 100 if avg > 0 else 0.0
        pnl = (cur - avg) * sh
        stats.append({"ticker": sym, "avg": avg, "cur": cur, "shares": sh,
                      "value": val, "return_pct": ret, "pnl": pnl})
    return sorted(stats, key=lambda r: -(r.get("value", 0) or 0))


def _holding_summary_lines(stats: list[dict]) -> tuple[float, list[str]]:
    total_pnl = sum(float(r.get("pnl", 0) or 0) for r in stats)
    pos_value = sum(float(r.get("value", 0) or 0) for r in stats)
    cost = pos_value - total_pnl
    pnl_ret = (total_pnl / cost * 100.0) if cost else 0.0
    money = ("+" if total_pnl >= 0 else "-") + fmt.money(abs(total_pnl))
    lines = [f"평가손익 {fmt.spct(pnl_ret)} ({money})"]

    winners = sorted([r for r in stats if r["return_pct"] > 0],
                     key=lambda r: -r["return_pct"])[:2]
    laggards = sorted([r for r in stats if r["return_pct"] < 0],
                      key=lambda r: r["return_pct"])[:2]
    if winners:
        lines.append("상승 기여: " + ", ".join(
            f"{r['ticker']} {fmt.pct(r['return_pct'])}" for r in winners))
    if laggards:
        lines.append("부담 요인: " + ", ".join(
            f"{r['ticker']} {fmt.pct(r['return_pct'])}" for r in laggards))
    return total_pnl, lines


def _cost_burden_label(drag: float, turnover: float) -> str:
    if drag >= 0.75 or turnover >= 250:
        return "높음"
    if drag >= 0.25 or turnover >= 100:
        return "보통"
    return "낮음"


def _parse_decision_factors(reason: str) -> dict[str, float]:
    factors: dict[str, float] = {}
    for key, raw in _FACTOR_RE.findall(reason or ""):
        try:
            factors[key.lower()] = float(raw)
        except ValueError:
            continue
    return factors


def _factor_level(value: float) -> str:
    if value >= 0.65:
        return "강함"
    if value >= 0.35:
        return "보통"
    return "약함"


def _decision_interpretation(d: dict) -> str:
    reason = str((d.get("rationale") or {}).get("one_line_reason") or "")
    factors = _parse_decision_factors(reason)
    if factors:
        parts = [
            f"{_FACTOR_LABELS[k]} {_factor_level(v)}"
            for k, v in factors.items()
            if k in _FACTOR_LABELS
        ]
        if factors.get("quality", 1.0) < 0.2:
            parts.append("퀄리티 보완 필요")
        return " · ".join(parts)
    if d.get("side") == "편입":
        if "추세" in reason or "mom" in reason.lower():
            return "추세 근거가 편입 조건을 뒷받침합니다."
        return "현재 정책 조건에서 신규 편입 우선순위에 포함됐습니다."
    if d.get("side") == "퇴출":
        return "정책 조건에서 보유 우선순위가 낮아져 제외됐습니다."
    return "정책 조건 변화에 따른 포지션 조정입니다."


def _compact_holding_lines(stats: list[dict], nav: float, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for idx, row in enumerate(stats[:limit], start=1):
        weight = row["value"] / nav * 100.0 if nav else 0.0
        warn = " ⚠️" if row["return_pct"] <= -8 or weight >= 25 else ""
        lines.append(
            f"{idx}. {row['ticker']} {fmt.spct(row['return_pct'])} "
            f"{fmt.money(row['value'], abbrev=True)} ({weight:.1f}%){warn}"
        )
    if len(stats) > limit:
        remain = sum(float(r.get("value", 0) or 0) for r in stats[limit:])
        lines.append(f"외 {len(stats) - limit}종목 {fmt.money(remain, abbrev=True)}")
    return lines


def _scorecard_brief(sc: dict) -> str:
    if not (sc.get("n_buy") or sc.get("n_sell")):
        return "로직 평가 대기"
    parts: list[str] = []
    if sc.get("buy_hit") is not None:
        parts.append(f"편입 {sc['buy_hit']}%")
    if sc.get("sell_hit") is not None:
        parts.append(f"퇴출 {sc['sell_hit']}%")
    if sc.get("ic") is not None:
        parts.append(f"IC {fmt.signed(sc['ic'], 2)}")
    return " · ".join(parts) if parts else "로직 평가 대기"


def _llm_shadow_brief(shadow: dict, pending: int) -> str:
    if shadow.get("n"):
        return llm_exec.summary_line(shadow)
    if pending:
        return f"LLM shadow {pending}건 평가 대기"
    return "LLM shadow 축적 중"


def _detail_block(title: str, rows: list[str], *, html: bool) -> list[str]:
    """Readable detail section.

    Telegram HTML uses expandable blockquotes so full reports stay scannable.
    Plain text keeps the section expanded for logs/tests.
    """
    if not rows:
        return []
    if html:
        body = "\n".join(fmt.esc(row) for row in rows)
        return [fmt.expand(fmt.b(title), body)]
    return [fmt.sep(title), *rows]


def _drop_section_header(rows: list[str]) -> list[str]:
    if rows and str(rows[0]).startswith("── "):
        return rows[1:]
    return rows


def _llm_shadow_summary():
    try:
        from ml.adaptive import Ledger
        ledger = Ledger("us_mock_llm_shadow")
        rows = llm_exec.shadow_training_set(ledger)
        summary = llm_exec.summarize_shadow(rows, horizon=llm_exec.report_horizon())
        return summary, llm_exec.pending_shadow_count(ledger, horizons_=llm_exec.horizons())
    except Exception as e:
        logger.info("US LLM shadow summary skipped: %s", e)
        return {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0


def _pearson(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


# ── 평가 스코어카드 (순수 — 테스트 핵심) ──────────────────────────────────────

def compute_scorecard(rows: list[dict]) -> dict:
    """결정⋈결과 → 편입/퇴출 적중률 + 실현 IC(정책점수↔초과수익). '로직이 맞게 작용하는지' 정량."""
    buy = [r for r in rows if r.get("side") in ("편입", "증액") and r.get("correct") is not None]
    sell = [r for r in rows if r.get("side") in ("퇴출", "감액") and r.get("correct") is not None]

    def hit(rs):
        return round(sum(1 for r in rs if r.get("correct")) / len(rs) * 100.0, 1) if rs else None

    pairs = [(r.get("policy_score"), r.get("fwd_excess")) for r in buy
             if r.get("policy_score") is not None and r.get("fwd_excess") is not None]
    ic = round(_pearson([a for a, _ in pairs], [b for _, b in pairs]), 3) if len(pairs) >= 3 else None
    return {"buy_hit": hit(buy), "sell_hit": hit(sell), "ic": ic,
            "n_buy": len(buy), "n_sell": len(sell)}


def _scorecard_rows() -> list[dict]:
    try:
        from ml.adaptive import Ledger
        return Ledger("us_mock").training_set()
    except Exception as e:
        logger.warning("스코어카드 원장 조회 실패: %s", e)
        return []


def _snapshots() -> list[dict]:
    try:
        import store
        return [r for r in store.all("us_mock_history")
                if r.get("kind") == "snapshot" and r.get("nav") is not None]
    except Exception as e:
        logger.warning("히스토리 조회 실패: %s", e)
        return []


def _recent_decisions():
    try:
        from ml.adaptive import Ledger
        decs = Ledger("us_mock").read_decisions()
    except Exception as e:
        logger.warning("결정 원장 조회 실패: %s", e)
        return [], None
    if not decs:
        return [], None
    last = max(d.get("date", "") for d in decs)
    return [d for d in decs if d.get("date") == last and d.get("side") in ("편입", "퇴출")], last


def build_report(html: bool = False, detail: bool | None = None) -> str:
    """US 모의 현황 + 스코어카드.

    기본은 매일 읽기 쉬운 compact 출력이다. detail=True 또는
    US_MOCK_REPORT_DETAIL=true 일 때만 검증/LLM/단기 슬리브 상세를 펼친다.
    """
    _B = fmt.b if html else (lambda x: x)
    detail = _detail_mode(detail)
    bal = kis_mock.get_balance()
    today = datetime.now(KST)
    if detail:
        hdr = (f"🧪 [모의] 미국 페이퍼트레이딩 현황 (KIS 해외)\n"
               f"📅 {today.strftime('%Y-%m-%d')} ({_WD[today.weekday()]}) {today.strftime('%H:%M')} KST")
    else:
        hdr = f"🧪 US 모의 · {today.strftime('%Y-%m-%d')} ({_WD[today.weekday()]}) {today.strftime('%H:%M')} KST"
    if not bal.get("ok"):
        return hdr + "\n━━━━━━━━━━━━━━━\n  ⚠️ 잔고 조회 실패 — KOREA_MOCK_ENABLED·계좌#·모의 연결 확인"

    positions, cash = bal["positions"], bal["cash_usd"]
    pos_value = bal["pos_value"] or 0.0
    nav = bal["nav"] or (pos_value + (cash or 0.0)) or SEED_USD

    snaps = _snapshots()
    inception_nav = float(snaps[0]["nav"]) if snaps else SEED_USD
    inception_date = str(snaps[0]["date"])[:10] if snaps else today.strftime("%Y-%m-%d")
    today_d = today.strftime("%Y-%m-%d")
    prev = [s for s in snaps if str(s["date"])[:10] < today_d]
    prev_nav = float(prev[-1]["nav"]) if prev else inception_nav

    from ml.adaptive import reward as _reward
    strat_mdd = _reward.max_drawdown([float(s["nav"]) for s in snaps] + [float(nav)]) * 100.0
    cum_ret = (nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0
    day_ret = (nav / prev_nav - 1.0) * 100.0 if prev_nav else 0.0

    try:
        from providers import market_data
        bm = market_data.fetch_kospi_stats(inception_date, symbol="QQQ")   # US 벤치마크
    except Exception as e:
        logger.warning("QQQ 통계 실패: %s", e)
        bm = {"return_pct": None, "mdd": None}
    q_ret = bm.get("return_pct")
    q_mdd_pct = bm["mdd"] * 100.0 if bm.get("mdd") is not None else None

    held = {c: p for c, p in positions.items() if int(p.get("shares", 0) or 0) > 0}
    holding_stats = _holding_stats(held)
    total_pnl, summary_rows = _holding_summary_lines(holding_stats) if holding_stats else (0.0, [])

    lev_lines: list[str] = []
    try:
        from crons.us_mock_track import LEV_SLEEVE_ENABLED, LEV_SLEEVE_SYMBOL, load_lev_shadow
        lev_pos = positions.get(LEV_SLEEVE_SYMBOL) or {}
        lev_sh = int(lev_pos.get("shares", 0) or 0)
        if LEV_SLEEVE_ENABLED or lev_sh > 0:
            reco = load_lev_shadow()
            frac = (float(lev_pos.get("value", 0) or 0) / nav * 100.0) if nav else 0.0
            if reco:
                lev_lines.append(f"게이트 GO ×{reco:.2f} → 목표 {(reco - 1) * 100:.0f}%"
                                 f" · 보유 {LEV_SLEEVE_SYMBOL} {lev_sh}주 ({frac:.0f}%)")
            else:
                state = "게이트 미통과/stale → 목표 0%" if LEV_SLEEVE_ENABLED else "슬리브 off"
                lev_lines.append(f"{state} · 보유 {LEV_SLEEVE_SYMBOL} {lev_sh}주 ({frac:.0f}%)")
    except Exception:
        pass

    try:
        import store
        crows = [r for r in store.all("us_mock_history") if r.get("kind") == "cost"]
    except Exception:
        crows = []
    tot_cost = sum(float(r.get("cost", 0) or 0) for r in crows)
    tot_notional = sum(float(r.get("notional", 0) or 0) for r in crows)
    turnover = None
    drag = 0.0
    if tot_cost > 0 and inception_nav:
        avg_nav = (sum(float(s["nav"]) for s in snaps) / len(snaps)) if snaps else inception_nav
        drag = tot_cost / inception_nav * 100.0
        turnover = (tot_notional / avg_nav * 100.0) if avg_nav else 0.0

    sc = compute_scorecard(_scorecard_rows())
    shadow, pending_shadow = _llm_shadow_summary()
    recent, last = _recent_decisions()
    excess = (cum_ret - q_ret) if q_ret is not None else None

    if not detail:
        lines = [hdr, fmt.sep()]
        if excess is not None:
            lines.append(
                f"NAV {_B(fmt.money(nav, abbrev=True))} · 전일 {fmt.spct(day_ret, 2)} · "
                f"누적 {fmt.spct(cum_ret, 2)}"
            )
            lines.append(f"QQQ대비 {fmt.pct(excess)}p {'✅' if excess >= 0 else '⚠️'} · QQQ {fmt.spct(q_ret, 2)}")
        else:
            lines.append(f"NAV {_B(fmt.money(nav, abbrev=True))} · 전일 {fmt.spct(day_ret, 2)} · 누적 {fmt.spct(cum_ret, 2)}")
        if q_mdd_pct is not None:
            ok = "✅" if strat_mdd <= q_mdd_pct else "⚠️"
            lines.append(f"MDD {strat_mdd:.1f}% vs QQQ {q_mdd_pct:.1f}% {ok}")
        else:
            lines.append(f"MDD {strat_mdd:.1f}%")
        if cash is not None:
            cash_w = cash / nav * 100.0 if nav else 0.0
            label = "여유증거금" if bal.get("cash_derived") else "현금"
            lines.append(f"{label} {fmt.money(cash, abbrev=True)} · {cash_w:.1f}%")

        state_lines = _state_interpretation(cum_ret, q_ret, strat_mdd, q_mdd_pct, cash, nav)
        watch_lines: list[str] = []
        if summary_rows:
            watch_lines.append(summary_rows[0])
        if state_lines:
            watch_lines.append(state_lines[0])
        if tot_cost > 0 and inception_nav and turnover is not None:
            watch_lines.append(f"비용 {fmt.money(tot_cost)} · 회전율 {turnover:.0f}% · drag {drag:.2f}%p")
        if watch_lines:
            lines.append(fmt.sep("판단"))
            for row in watch_lines[:4]:
                lines.append(f"- {row}")

        lines.append(fmt.sep(f"보유 {len(held)}종목"))
        if holding_stats:
            lines.extend(_compact_holding_lines(holding_stats, nav))
        else:
            lines.append("(보유 없음 — 현금 100%)")

        if recent:
            first = recent[0]
            ticker = first.get("ticker") or first.get("code") or ""
            more = f" 외 {len(recent) - 1}건" if len(recent) > 1 else ""
            rr = (first.get("rationale") or {}).get("one_line_reason", "")
            lines.append(fmt.sep("최근 결정"))
            lines.append(f"{first.get('side')} {fmt.name(ticker, maxlen=16)}{more}")
            if rr:
                lines.append(f"근거: {rr}")
            lines.append(f"해석: {_decision_interpretation(first)}")

        check = [_scorecard_brief(sc), _llm_shadow_brief(shadow, pending_shadow)]
        if lev_lines:
            check.append("구조레버 " + lev_lines[0])
        lines.append(fmt.sep("체크"))
        for row in check:
            lines.append(f"- {row}")
        lines.append(fmt.sep())
        lines.append("⚠️ 모의투자 — 실거래 아님 · 상세: /paper us full")
        return "\n".join(lines)

    lines = [hdr, fmt.sep()]
    lines.append(fmt.headline(
        f"📊 NAV {_B(fmt.money(nav, abbrev=True))}",
        f"전일 {fmt.spct(day_ret, 2)}",
        f"누적 {_B(fmt.spct(cum_ret, 2))}",
    ))
    if excess is not None:
        lines.append(f"QQQ대비 {_B(fmt.pct(excess) + 'p')} {'✅' if excess >= 0 else '⚠️'} · QQQ {fmt.spct(q_ret, 2)}")
    if q_mdd_pct is not None:
        ok = "✅" if strat_mdd <= q_mdd_pct else "⚠️"
        lines.append(f"MDD {strat_mdd:.1f}% vs QQQ {q_mdd_pct:.1f}% {ok}")
    else:
        lines.append(f"MDD {strat_mdd:.1f}%")
    cash_summary = _cash_line(cash, nav, bal)
    if cash_summary:
        lines.append(cash_summary)

    state_lines = _state_interpretation(cum_ret, q_ret, strat_mdd, q_mdd_pct, cash, nav)
    if state_lines:
        lines.append(fmt.sep("상태 해석"))
        lines.extend(state_lines[:2])
    if summary_rows:
        lines.append(fmt.sep("보유 요약"))
        lines.extend(summary_rows)

    if holding_stats:
        lines.append(fmt.sep(f"보유 {len(held)}종목"))
        lines.extend(_compact_holding_lines(holding_stats, nav))
    else:
        lines.append(fmt.sep("보유 0종목"))
        lines.append("(보유 없음 — 현금 100%)")

    holding_detail: list[str] = []
    for row in holding_stats:
        sym = row["ticker"]
        avg = row["avg"]
        cur = row["cur"]
        sh = row["shares"]
        val = row["value"]
        ret = row["return_pct"]
        holding_detail.append(f"{fmt.name(sym, maxlen=16)} {fmt.spct(ret)}  {fmt.money(val)}")
        holding_detail.append(f"  {sh}주 · {avg:,.2f}→{cur:,.2f}")
    lines.extend(_detail_block("보유 가격 상세", holding_detail, html=html))

    cost_detail: list[str] = []
    if tot_cost > 0 and inception_nav and turnover is not None:
        cost_detail.extend([
            f"누적 {fmt.money(tot_cost)} · 회전율 {turnover:.0f}%",
            f"비용차감 누적 {fmt.spct(cum_ret - drag)} (표시 {fmt.spct(cum_ret)} − 비용 {drag:.2f}%p)",
            f"해석: 비용 부담 {_cost_burden_label(drag, turnover)} · 회전율 상승 여부 확인",
        ])
    lines.extend(_detail_block("거래비용", cost_detail, html=html))

    # ★로직 평가 스코어카드 — 무엣지 기준선 대비 판정 라벨 인라인
    validation_detail: list[str] = []
    if sc["n_buy"] or sc["n_sell"]:
        def _verdict(hit):
            return "약한 엣지" if (hit is not None and hit >= 55) else "무엣지 수준"
        if sc["buy_hit"] is not None:
            validation_detail.append(f"로직: 편입 적중률 {sc['buy_hit']}% (n={sc['n_buy']}) — {_verdict(sc['buy_hit'])}")
        if sc["sell_hit"] is not None:
            validation_detail.append(f"로직: 퇴출 적중률 {sc['sell_hit']}% (n={sc['n_sell']}) — {_verdict(sc['sell_hit'])}")
        if sc["ic"] is not None:
            ic_v = "변별력 있음" if abs(sc["ic"]) >= 0.05 else "≈0(무변별)"
            validation_detail.append(f"로직: 실현 IC {fmt.signed(sc['ic'], 2)} — {ic_v}")
        validation_detail.append("※ IC=예측↔초과수익 상관 · 무엣지면 적중률 ~50%·IC ~0 (정직)")
    else:
        validation_detail.append("검증 상태")
        validation_detail.append("로직: 성숙 결정 없음 — 평가 대기(horizon 경과 후)")
    validation_detail.append(f"LLM Shadow: {llm_exec.summary_line(shadow)}")
    if pending_shadow:
        validation_detail.append(f"미성숙 후보 {pending_shadow}건 — horizon 경과 후 평가")
    if lev_lines:
        validation_detail.append("")
        validation_detail.append("구조레버 슬리브")
        validation_detail.extend(lev_lines)
    lines.extend(_detail_block("검증 상태", validation_detail, html=html))

    # 🕐 단기 슬리브 (INTRADAY_MOCK — 데이터 없으면 섹션 숨음)
    try:
        from lib.intraday_status import intraday_section
        lines.extend(_detail_block("단기 슬리브", _drop_section_header(intraday_section("US", html=False)), html=html))
    except Exception as e:
        logger.warning("단기 섹션 실패(무시): %s", e)

    if recent:
        lines.append(fmt.sep(f"최근 결정 ({last})"))
        for d in recent:
            icon = "📥" if d.get("side") == "편입" else "📤"
            rr = (d.get("rationale") or {}).get("one_line_reason", "")
            ticker = d.get("ticker") or d.get("code") or ""
            lines.append(f"{icon} {d.get('side')}  {fmt.name(ticker, maxlen=16)}")
            lines.append(f"근거: {rr or d.get('action') or '—'}")
            lines.append(f"해석: {_decision_interpretation(d)}")

    llm_payload = llm_rationale.build_payload(
        market="US",
        nav=nav,
        day_ret=day_ret,
        cum_ret=cum_ret,
        benchmark_ret=q_ret,
        excess=excess,
        strat_mdd=strat_mdd,
        benchmark_mdd=q_mdd_pct,
        cash=cash,
        positions=[
            _position_payload(sym, p, pos_value)
            for sym, p in sorted(held.items(), key=lambda kv: -(kv[1].get("value", 0) or 0))
        ],
        recent_decisions=[_decision_payload(d) for d in recent],
        scorecard=sc,
        trading_cost=tot_cost,
        turnover=turnover,
    )
    llm_result, llm_status = llm_rationale.run(llm_payload)
    if llm_result:
        lines.extend(_detail_block("🧠 LLM 판단근거", llm_rationale.format_section(llm_result)[1:], html=html))
    else:
        logger.info("US mock LLM rationale skipped: %s", llm_status)

    lines.append(fmt.sep())
    lines.append("⚠️ 모의투자 — 실거래 아님")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== us_mock_report 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kis_mock.is_enabled():
        logger.info("KOREA_MOCK_ENABLED 아님 — 현황 보고 생략")
        return 0
    text = build_report()
    try:
        bal = kis_mock.get_balance()
        if bal.get("ok"):
            import store
            store.append("us_mock_history", {
                "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot", "at": "eod",
                "nav": bal.get("nav"), "cash": bal.get("cash_usd"),
                "positions": len([p for p in bal["positions"].values() if int(p.get("shares", 0) or 0) > 0])})
    except Exception as e:
        logger.warning("EOD 스냅샷 실패: %s", e)
    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
