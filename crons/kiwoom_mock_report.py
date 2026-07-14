#!/usr/bin/env python3
"""
kiwoom_mock_report.py — 국내 모의 페이퍼트레이딩 일일 현황 보고.

★목표 가시화: 누적 초과수익(vs KOSPI) + MDD(전략 vs 지수). 1순위 아웃퍼폼, 2순위 MDD≤지수.
보유 평가·손익·NAV + 최근 편입/퇴출 사유(불변 원장에서)도 함께. build_report()는 크론·/mock 공용.

크론 (평일 06:40 UTC = 15:40 KST, 장 마감 직후):
    40 6 * * 1-5 cd <repo> && flock -n /tmp/kiwoom_mock_report.lock uv run python crons/kiwoom_mock_report.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import kiwoom_mock
import fmt
from lib import mock_llm_execution as llm_exec
from lib import mock_llm_rationale as llm_rationale

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SEED_KRW = float(os.getenv("KIWOOM_MOCK_SEED", "10000000"))
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).lower() in ("1", "true", "yes", "on")


def _detail_mode(detail: bool | None) -> bool:
    if detail is not None:
        return bool(detail)
    return _env_bool("KIWOOM_MOCK_REPORT_DETAIL", _env_bool("MOCK_REPORT_DETAIL", False))


def _detail_block(title: str, rows: list[str], *, html: bool) -> list[str]:
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


def _position_payload(code, p, total_value):
    value = p.get("value", 0) or 0
    return {
        "code": code,
        "name": p.get("name", "") or code,
        "weight_pct": round(value / total_value * 100.0, 1) if total_value else 0.0,
        "return_pct": p.get("return_pct", 0) or 0,
        "value": value,
    }


def _decision_payload(d):
    return {
        "side": d.get("side"),
        "code": d.get("code"),
        "reason": (d.get("rationale") or {}).get("one_line_reason", ""),
    }


def _kr_label(code: str | None, name: str | None = None, ticker: str | None = None) -> str:
    """국내 종목 표시명: 회사명 (티커). 네트워크 없이 큐레이트/캐시만 사용."""
    c = str(code or ticker or "").replace(".KS", "").replace(".KQ", "").strip().zfill(6)
    nm = (name or "").strip()
    if not nm or nm in (c, ticker, f"{c}.KS", f"{c}.KQ"):
        try:
            import kr200_meta
            nm = kr200_meta.NAME.get(c) or nm
        except Exception:
            pass
    if not nm or nm in (c, ticker, f"{c}.KS", f"{c}.KQ"):
        try:
            import ticker_names
            nm = ticker_names.display_name(ticker or f"{c}.KS", allow_net=False) or nm
        except Exception:
            pass
    if nm and nm not in (c, ticker, f"{c}.KS", f"{c}.KQ"):
        return f"{nm} ({c})"
    return c


def _decision_interpretation(d: dict) -> str:
    """최근 결정 한 줄 해석. 원장 근거만 기반으로 보수적으로 요약."""
    side = d.get("side")
    reason = str((d.get("rationale") or {}).get("one_line_reason") or "")
    if side == "편입":
        if "일일 신호 긍정" in reason:
            return "재무 품질과 단기 신호가 편입 조건을 충족"
        return "현재 정책 조건에서 신규 편입 우선순위에 포함"
    if side == "퇴출":
        return "정책 조건에서 보유 우선순위가 낮아져 제외"
    return "정책 조건 변화에 따른 포지션 조정"


def _state_interpretation(
    cum_ret: float,
    k_ret: float | None,
    strat_mdd: float,
    k_mdd_pct: float | None,
    cash: float | None,
    nav: float,
) -> list[str]:
    lines: list[str] = []
    if k_ret is None:
        lines.append("KOSPI 벤치 데이터가 없어 상대성과는 보류합니다.")
    else:
        excess = cum_ret - k_ret
        if excess >= 0 and k_mdd_pct is not None and strat_mdd <= k_mdd_pct:
            lines.append(f"KOSPI 대비 {fmt.pct(excess)}p 앞서고 MDD도 낮아 방어 우위입니다.")
        elif excess >= 0:
            lines.append(f"KOSPI 대비 {fmt.pct(excess)}p 앞서지만 낙폭 통제는 계속 확인해야 합니다.")
        else:
            lines.append(f"KOSPI 대비 {fmt.pct(excess)}p 뒤처져 종목 선택과 현금비중 점검이 필요합니다.")
    if cash is not None and nav:
        cash_w = cash / nav * 100.0
        if cash_w >= 30:
            lines.append(f"현금 {cash_w:.1f}%는 하락 방어에 도움되지만 반등장에서는 지연 요인입니다.")
        elif cash_w >= 10:
            lines.append(f"현금 {cash_w:.1f}%로 신규 편입 여력은 남아 있습니다.")
        else:
            lines.append(f"현금 {cash_w:.1f}%라 추가 매수 여력은 제한적입니다.")
    return lines[:3]


def _holding_stats(held: dict) -> list[dict]:
    stats: list[dict] = []
    for code, p in held.items():
        ret = p.get("return_pct", 0) or 0
        stats.append({
            "code": code,
            "name": p.get("name", "") or code,
            "shares": int(p.get("shares", 0) or 0),
            "avg": p.get("avg_price", 0) or 0,
            "cur": p.get("cur_price", 0) or 0,
            "value": p.get("value", 0) or 0,
            "pnl": p.get("pnl", 0) or 0,
            "return_pct": ret,
        })
    return sorted(stats, key=lambda r: -(r.get("value", 0) or 0))


def _holding_summary_lines(stats: list[dict]) -> tuple[float, list[str]]:
    total_pnl = sum(float(r.get("pnl", 0) or 0) for r in stats)
    pos_value = sum(float(r.get("value", 0) or 0) for r in stats)
    cost = pos_value - total_pnl
    pnl_ret = (total_pnl / cost * 100.0) if cost else 0.0
    sm = ("+" if total_pnl >= 0 else "-") + fmt.money(abs(total_pnl), "₩", abbrev=True)
    lines = [f"평가손익 {fmt.spct(pnl_ret)} ({sm})"]
    winners = sorted([r for r in stats if r["return_pct"] > 0],
                     key=lambda r: -r["return_pct"])[:2]
    laggards = sorted([r for r in stats if r["return_pct"] < 0],
                      key=lambda r: r["return_pct"])[:2]
    if winners:
        lines.append("상승 기여: " + ", ".join(
            f"{r['name']} {fmt.pct(r['return_pct'])}" for r in winners))
    if laggards:
        lines.append("부담 요인: " + ", ".join(
            f"{r['name']} {fmt.pct(r['return_pct'])}" for r in laggards))
    return total_pnl, lines


def _compact_holding_lines(stats: list[dict], nav: float, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for idx, row in enumerate(stats[:limit], start=1):
        weight = row["value"] / nav * 100.0 if nav else 0.0
        warn = " ⚠️" if row["return_pct"] <= -8 or weight >= 25 else ""
        lines.append(
            f"{idx}. {row['name']} {fmt.spct(row['return_pct'])} "
            f"{fmt.money(row['value'], '₩', abbrev=True)} ({weight:.1f}%){warn}"
        )
    if len(stats) > limit:
        remain = sum(float(r.get("value", 0) or 0) for r in stats[limit:])
        lines.append(f"외 {len(stats) - limit}종목 {fmt.money(remain, '₩', abbrev=True)}")
    return lines


def _llm_shadow_summary():
    try:
        from ml.adaptive import Ledger
        ledger = Ledger("kr_mock_llm_shadow")
        rows = llm_exec.shadow_training_set(ledger)
        summary = llm_exec.summarize_shadow(rows, horizon=llm_exec.report_horizon())
        return summary, llm_exec.pending_shadow_count(ledger, horizons_=llm_exec.horizons())
    except Exception as e:
        logger.info("KR LLM shadow summary skipped: %s", e)
        return {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0


def _snapshots() -> list[dict]:
    try:
        import store
        return [r for r in store.all("kr_mock_history")
                if r.get("kind") == "snapshot" and r.get("nav") is not None]
    except Exception as e:
        logger.warning("히스토리 조회 실패: %s", e)
        return []


def _recent_decisions() -> tuple[list[dict], str | None]:
    """가장 최근 집행일의 편입/퇴출 결정(사유 포함)."""
    try:
        from ml.adaptive import Ledger
        decs = Ledger("kr_mock").read_decisions()
    except Exception as e:
        logger.warning("결정 원장 조회 실패: %s", e)
        return [], None
    if not decs:
        return [], None
    last_date = max(d.get("date", "") for d in decs)
    recent = [d for d in decs if d.get("date") == last_date and d.get("side") in ("편입", "퇴출")]
    return recent, last_date


def build_report(html: bool = False, detail: bool | None = None) -> str:
    """모의 현황 리포트 문자열.

    기본은 compact 출력이다. detail=True 또는 KIWOOM_MOCK_REPORT_DETAIL=true 일 때만
    검증/LLM/가격 상세를 펼치며, HTML 전송에서는 접힌 상세로 묶는다.
    """
    _B = fmt.b if html else (lambda x: x)
    detail = _detail_mode(detail)
    bal = kiwoom_mock.get_balance()
    today = datetime.now(KST)
    hdr = (f"🧪 [모의] 국내 모의투자 · "
           f"{today.strftime('%Y-%m-%d')} ({_WEEKDAY_KR[today.weekday()]}) {today.strftime('%H:%M')} KST")
    if not bal.get("ok"):
        return hdr + "\n━━━━━━━━━━━━━━━━━━━\n  ⚠️ 잔고 조회 실패 — 모의 연결/모의투자 신청 확인 필요"

    positions = bal["positions"]
    cash = bal["cash_krw"]
    pos_value = bal["pos_value"] or 0.0
    nav = bal["nav"]
    if nav is None:
        nav = pos_value + (cash or 0.0) or SEED_KRW

    # NAV 시계열 → 인셉션·전일·전략 MDD
    snaps = _snapshots()
    inception_nav = float(snaps[0]["nav"]) if snaps else SEED_KRW
    inception_date = str(snaps[0]["date"])[:10] if snaps else today.strftime("%Y-%m-%d")
    today_d = today.strftime("%Y-%m-%d")
    prev = [s for s in snaps if str(s["date"])[:10] < today_d]
    prev_nav = float(prev[-1]["nav"]) if prev else inception_nav

    from ml.adaptive import reward as _reward
    nav_series = [float(s["nav"]) for s in snaps] + [float(nav)]
    strat_mdd = _reward.max_drawdown(nav_series) * 100.0
    cum_ret = (nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0
    day_ret = (nav / prev_nav - 1.0) * 100.0 if prev_nav else 0.0

    # KOSPI 벤치 (인셉션~오늘)
    try:
        from providers import market_data
        kospi = market_data.fetch_kospi_stats(inception_date)
    except Exception as e:
        logger.warning("KOSPI 통계 실패: %s", e)
        kospi = {"return_pct": None, "mdd": None}
    k_ret, k_mdd = kospi.get("return_pct"), kospi.get("mdd")
    k_mdd_pct = k_mdd * 100.0 if k_mdd is not None else None

    excess = (cum_ret - k_ret) if k_ret is not None else None
    held = {c: p for c, p in positions.items() if int(p.get("shares", 0) or 0) > 0}
    holding_stats = _holding_stats(held)
    _total_pnl, summary_rows = _holding_summary_lines(holding_stats) if holding_stats else (0.0, [])

    try:
        import store
        crows = [r for r in store.all("kr_mock_history") if r.get("kind") == "cost"]
    except Exception:
        crows = []
    tot_cost = sum(float(r.get("cost", 0) or 0) for r in crows)
    tot_notional = sum(float(r.get("notional", 0) or 0) for r in crows)
    drag = 0.0
    turnover = None
    if tot_cost > 0 and inception_nav:
        avg_nav = (sum(float(s["nav"]) for s in snaps) / len(snaps)) if snaps else inception_nav
        drag = tot_cost / inception_nav * 100.0
        turnover = (tot_notional / avg_nav * 100.0) if avg_nav else 0.0

    recent, last_date = _recent_decisions()
    shadow, pending_shadow = _llm_shadow_summary()

    if not detail:
        lines = [hdr, fmt.sep()]
        lines.append(f"NAV {_B(fmt.money(nav, '₩', abbrev=True))} · 전일 {fmt.spct(day_ret, 2)} · 누적 {fmt.spct(cum_ret, 2)}")
        if excess is not None:
            lines.append(f"KOSPI 대비 {fmt.pct(excess)}p {'✅' if excess >= 0 else '⚠️'} · KOSPI {fmt.spct(k_ret, 2)}")
        if k_mdd_pct is not None:
            ok_mdd = "✅" if strat_mdd <= k_mdd_pct else "⚠️"
            lines.append(f"MDD {strat_mdd:.1f}% vs KOSPI {k_mdd_pct:.1f}% {ok_mdd}")
        else:
            lines.append(f"MDD {strat_mdd:.1f}%")
        if cash is not None:
            cash_w = cash / nav * 100.0 if nav else 0.0
            lines.append(f"현금 {fmt.money(cash, '₩', abbrev=True)} · {cash_w:.1f}%")

        state_lines = _state_interpretation(cum_ret, k_ret, strat_mdd, k_mdd_pct, cash, nav)
        if state_lines:
            lines.append(fmt.sep("판단"))
            for row in state_lines[:2]:
                lines.append(f"- {row}")
        if summary_rows:
            lines.append(fmt.sep("보유 요약"))
            lines.extend(summary_rows)
        lines.append(fmt.sep(f"보유 {len(held)}종목"))
        if holding_stats:
            lines.extend(_compact_holding_lines(holding_stats, nav))
        else:
            lines.append("(보유 없음 — 현금 100%)")

        if recent:
            first = recent[0]
            code = first.get("code") or str(first.get("ticker", "")).replace(".KS", "").replace(".KQ", "")
            qty = first.get("qty")
            qty_s = f" · {int(qty)}주" if isinstance(qty, (int, float)) and qty else ""
            more = f" 외 {len(recent) - 1}건" if len(recent) > 1 else ""
            rr = (first.get("rationale") or {}).get("one_line_reason", "")
            lines.append(fmt.sep("최근 결정"))
            lines.append(f"{first.get('side')} {_kr_label(code, first.get('name'), first.get('ticker'))}{qty_s}{more}")
            if rr:
                lines.append(f"근거: {rr}")
            lines.append(f"해석: {_decision_interpretation(first)}")

        lines.append(fmt.sep("체크"))
        lines.append(f"- LLM Shadow: {llm_exec.summary_line(shadow)}")
        if pending_shadow:
            lines.append(f"- 미성숙 후보 {pending_shadow}건")
        if tot_cost > 0 and turnover is not None:
            lines.append(f"- 비용 {fmt.money(tot_cost, '₩', abbrev=True)} · 회전율 {turnover:.0f}% · drag {drag:.2f}%p")
        lines.append(fmt.sep())
        lines.append("⚠️ 모의투자 — 실거래 아님 · 상세: /paper kr full")
        return "\n".join(lines)

    lines = [hdr, fmt.sep()]
    lines.append(fmt.headline(
        f"📊 NAV {_B(fmt.money(nav, '₩', abbrev=True))}",
        f"전일 {fmt.spct(day_ret, 2)}",
        f"누적 {_B(fmt.spct(cum_ret, 2))}",
    ))
    if excess is not None:
        lines.append(f"KOSPI 대비 {_B(fmt.pct(excess) + 'p')} {'✅' if excess >= 0 else '⚠️'} · KOSPI {fmt.spct(k_ret, 2)}")
    if k_mdd_pct is not None:
        ok_mdd = "대비 방어 ✅" if strat_mdd <= k_mdd_pct else "보다 깊음 ⚠️"
        lines.append(f"MDD {strat_mdd:.1f}% vs KOSPI {k_mdd_pct:.1f}% {ok_mdd}")
    else:
        lines.append(f"MDD {strat_mdd:.1f}%")
    if cash is not None:
        cash_w = cash / nav * 100.0 if nav else 0.0
        lines.append(f"현금 {fmt.money(cash, '₩')} · 현금비중 {cash_w:.1f}%")

    state_lines = _state_interpretation(cum_ret, k_ret, strat_mdd, k_mdd_pct, cash, nav)
    if state_lines:
        lines.append(fmt.sep("상태 해석"))
        lines.extend(state_lines[:2])
    if summary_rows:
        lines.append(fmt.sep("보유 요약"))
        lines.extend(summary_rows)
    lines.append(fmt.sep(f"보유 {len(held)}종목"))
    if holding_stats:
        lines.extend(_compact_holding_lines(holding_stats, nav))
    else:
        lines.append("(보유 없음 — 현금 100%)")

    holding_detail: list[str] = []
    for row in holding_stats:
        mark = "▲" if row["return_pct"] > 0 else ("▼" if row["return_pct"] < 0 else "·")
        holding_detail.append(f"{mark} {row['name'][:10]}  {fmt.pct(row['return_pct'])}  {fmt.money(row['value'], '₩')}")
        holding_detail.append(f"  {row['shares']}주 · {row['avg']:,.0f} → {row['cur']:,.0f}")
    lines.extend(_detail_block("보유 가격 상세", holding_detail, html=html))

    cost_detail: list[str] = []
    if tot_cost > 0 and turnover is not None:
        cost_detail.extend([
            f"누적 {fmt.money(tot_cost, '₩', abbrev=True)} · 회전율 {turnover:.0f}%",
            f"비용 차감 후 누적 {fmt.spct(cum_ret - drag)}",
            f"성과 차감: -{drag:.2f}%p",
        ])
    lines.extend(_detail_block("비용 체크", cost_detail, html=html))

    try:
        from lib.intraday_status import intraday_section
        lines.extend(_detail_block("단기 슬리브", _drop_section_header(intraday_section("KR", html=False)), html=html))
    except Exception as e:
        logger.warning("단기 섹션 실패(무시): %s", e)

    if recent:
        lines.append(fmt.sep("최근 결정"))
        for d in recent:
            icon = "📥" if d.get("side") == "편입" else "📤"
            rr = (d.get("rationale") or {}).get("one_line_reason", "")
            code = d.get("code") or str(d.get("ticker", "")).replace(".KS", "").replace(".KQ", "")
            qty = d.get("qty")
            qty_s = f" · {int(qty)}주" if isinstance(qty, (int, float)) and qty else ""
            lines.append(f"{icon} {d.get('side')}  {_kr_label(code, d.get('name'), d.get('ticker'))}{qty_s}")
            lines.append(f"근거: {rr or d.get('action') or '—'}")
            lines.append(f"해석: {_decision_interpretation(d)}")

    shadow_detail = [llm_exec.summary_line(shadow)]
    if pending_shadow:
        shadow_detail.append(f"미성숙 후보 {pending_shadow}건 — horizon 경과 후 평가")
    lines.extend(_detail_block("LLM Shadow", shadow_detail, html=html))

    llm_payload = llm_rationale.build_payload(
        market="KR",
        nav=nav,
        day_ret=day_ret,
        cum_ret=cum_ret,
        benchmark_ret=k_ret,
        excess=excess,
        strat_mdd=strat_mdd,
        benchmark_mdd=k_mdd_pct,
        cash=cash,
        positions=[
            _position_payload(code, p, pos_value)
            for code, p in sorted(held.items(), key=lambda kv: -(kv[1].get("value", 0) or 0))
        ],
        recent_decisions=[_decision_payload(d) for d in recent],
        trading_cost=tot_cost,
        turnover=turnover if tot_cost > 0 and inception_nav else None,
    )
    llm_result, llm_status = llm_rationale.run(llm_payload)
    if llm_result:
        lines.extend(_detail_block("🧠 LLM 판단근거", llm_rationale.format_section(llm_result)[1:], html=html))
    else:
        logger.info("KR mock LLM rationale skipped: %s", llm_status)

    lines.append(fmt.sep())
    lines.append("⚠️ 모의투자 — 실거래 아님")
    return "\n".join(lines)


def main() -> int:
    logger.info("=== kiwoom_mock_report 시작 [%s] ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    if not kiwoom_mock.is_enabled():
        logger.info("KIWOOM_MOCK_ENABLED 아님 — 현황 보고 생략")
        return 0

    text = build_report()

    # EOD NAV 스냅샷 적재(종가 NAV 시계열 — 전일대비는 이전 날짜 기준이라 동일자 중복 무해)
    try:
        bal = kiwoom_mock.get_balance()
        if bal.get("ok"):
            import store
            store.append("kr_mock_history", {
                "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot", "at": "eod",
                "nav": bal.get("nav"), "cash": bal.get("cash_krw"),
                "positions": len([p for p in bal["positions"].values() if int(p.get("shares", 0) or 0) > 0]),
            })
    except Exception as e:
        logger.warning("EOD 스냅샷 실패: %s", e)

    try:
        import notify
        notify.send_telegram(text, token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)
    logger.info("현황 보고 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
