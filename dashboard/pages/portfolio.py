"""포트폴리오 — 자산 성장·리스크·리밸런스·노출·인컴 (표시 전용·배분 불변)."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import cached, charts, data, theme

_NOBAR = {"displayModeBar": False}
_FAC_LABEL = {"mkt": "시장β(QQQ)", "rate": "금리β(TLT)"}


def render():
    st.title("💼 포트폴리오")
    st.caption("USD 해외북 · 표시 전용 · 배분 변경 아님 · 실계좌 주문 없음")

    hist = cached.port_history()
    rows = data.load_holdings()
    _headline(hist, rows)
    _growth_section(hist)
    _risk_section()
    st.divider()
    _rebalance_section(rows)
    _exposure_section(rows)
    _holdings_table(rows)
    _income_section(rows)
    _kr_section()

    with st.expander("리스크 전체 리포트 (텍스트)"):
        st.code(cached.risk(), language=None)
    st.caption("과거 실현 기반 · 미래 보장 아님 · 국내 제외(USD북)")


def _headline(hist, rows):
    """총액($·₩) + 기간 수익 분해 (환율 기여 — 원화 투자자 관점)."""
    fx = data.fx_attribution(hist, days=30)
    total = sum((r.get("value") or 0) for r in rows)
    last = hist[-1] if hist else {}
    m = st.columns(4)
    m[0].metric("총 평가액 ($)", data.f_usd(total, 0))
    krw = last.get("total_krw")
    m[1].metric("총 평가액 (₩)", f"₩{krw:,.0f}" if krw else "—",
                help=f"환율 {last.get('exchange_rate', 0):,.0f}원 기준 (일별 기록)")
    if fx:
        m[2].metric(f"수익률 $ ({fx['window_days']}일)", f"{fx['usd_ret']:+.2f}%")
        m[3].metric("환율 기여", f"{fx['fx_ret']:+.2f}%p",
                    help=f"₩수익 {fx['krw_ret']:+.2f}% 중 환차 몫 — "
                         f"{fx.get('from')}~{fx.get('to')}")
    else:
        m[2].metric("수익률", "—", help="일별 기록 2일 이상 필요")


def _growth_section(hist):
    """📈 자산 성장 곡선 — TWR(입출금 조정) vs QQQ."""
    g = data.growth_series(hist)
    st.markdown("##### 📈 자산 성장 — 내 포트(TWR) vs QQQ")
    if not g:
        st.caption("일별 기록 2일 이상 쌓이면 표시됩니다 (매일 23:00 UTC 크론이 적재)")
        return
    tw = data.twr_series(hist, cached.portfolio_flows())
    port = tw.get("twr") if tw else g["port"]
    st.plotly_chart(charts.growth_compare(g["dates"], port, g["qqq"]),
                    width="stretch", config=_NOBAR)
    _ft = (tw or {}).get("flows_total") or 0.0
    st.caption(f"기록 {g['n_days']}일 (매일 자동 누적) · **TWR = 입출금 조정 시간가중 수익률**"
               f"(거래 기록 기반 — 적립이 수익으로 안 잡힘 · 기간 순유입 "
               f"{data.f_usd(_ft, 0)}) · 첫 기록=0% · 참고용")


def _risk_section():
    s = cached.risk_struct()
    if s.get("error"):
        st.warning(f"리스크 분석 불가: {s['error']}")
        return
    lev = s.get("leverage") or {}
    rec = lev.get("recommend")
    a = st.columns(4)
    a[0].metric("포트 변동성(연)", data.f_frac_pct(s.get("port_vol")))
    a[1].metric("유효 종목수", data.f_ratio(s.get("n_eff"), 1),
                help=f"실보유 {s.get('n_assets', 0)} 중 분산 유효치(참여비 HHI 역수)")
    a[2].metric("추정 MDD", data.f_frac_pct(s.get("mdd_est")))
    a[3].metric("권장 레버리지", f"{rec:.2f}×" if rec else "—",
                help="낙폭예산 상한(robust·μ가정 무관) · 표시 전용")

    col1, col2 = st.columns(2)
    contribs = s.get("contributions") or []
    _h = max(300, 36 * len(contribs)) if contribs else 300   # 좌우 차트 높이 동기화
    with col1:
        if contribs:
            st.caption("위험 기여도 (참여비) — 포트 **안**에서 누가 위험을 만드나")
            st.plotly_chart(
                charts.hbar([t for t, _w, _pc in contribs],
                            [pc for _t, _w, pc in contribs], pct=True),
                width="stretch", config=_NOBAR)
    with col2:
        fn = s.get("factor_net") or {}
        if fn:
            st.caption("팩터 순베타 — 포트가 **바깥** 무엇에 노출됐나")
            _fig = charts.signed_bars([_FAC_LABEL.get(k, k) for k in fn.keys()],
                                      [round(float(v), 2) for v in fn.values()])
            _fig.update_layout(height=_h)                    # 좌측 기여도와 같은 높이
            st.plotly_chart(_fig, width="stretch", config=_NOBAR)
            _mkt = fn.get("mkt")
            st.caption(
                f"β = 지난 1년 일수익률 회귀. **시장β {_mkt:+.2f}** = QQQ 가 1% 움직일 때 "
                f"포트가 평균 {abs(_mkt):.1f}% 동행 — 개별 9종목이어도 시장 관점 실효 노출은 "
                f"이 한 숫자로 요약 (1보다 낮음 = SGOV·QQQI 완충이 작동 중). "
                f"**금리β {fn.get('rate', 0):+.2f}** ≈ 금리 변화에 사실상 중립. "
                + (s.get("factor_caveat") or "과거 실현 기반 — 미래 보장 아님")
                if _mkt is not None else (s.get("factor_caveat") or ""))

    kh = lev.get("kelly_half") or {}
    if kh:
        st.caption(
            f"½Kelly 밴드 — 보수 {data.f_ratio(kh.get('conservative'), 2)}× · "
            f"중립 {data.f_ratio(kh.get('moderate'), 2)}× · 추세 {data.f_ratio(kh.get('trailing'), 2)}× "
            f"· 낙폭예산 상한 {data.f_ratio(lev.get('dd_cap'), 2)}× "
            f"(현재 {data.f_ratio(lev.get('current'), 2)}×)")

    # 🏗️ Tier3 구조적 레버리지 게이트 (검증 통과한 유일한 공격 — 표시 전용)
    try:
        t3 = cached.tier3_gate()
        if t3.get("available"):
            state = (f"**GO ×{t3.get('reco_lev'):.2f}**" + ("" if t3.get("fresh") else " (stale)")
                     + f" · {t3.get('at', '')}")
        else:
            state = "미기록 — 게이트 NO-GO/평가 대기 또는 `ADAPTIVE_LEVERAGE_ENABLED` off"
        st.caption(f"🏗️ Tier3 구조적 레버리지 게이트: {state} · "
                   f"US 모의 슬리브 {'✅ ON' if t3.get('sleeve_env') else 'off'} — 실계좌 자동집행 없음(수동)")
    except Exception:
        pass


def _rebalance_section(rows):
    """🎯 목표 vs 현재 비중 갭 (봇 /rebalance 와 동일 소스 — 표시 전용)."""
    targets = cached.target_weights_map()
    rb = data.rebalance_gaps(rows, targets)
    gaps = (rb or {}).get("gaps") or []
    if not gaps:
        return
    st.markdown("##### 🎯 리밸런스 갭 — 목표 vs 현재 (목표 설정 종목만)")
    g1, g2 = st.columns([1.2, 1], vertical_alignment="center")
    with g1:
        st.plotly_chart(
            charts.signed_bars([g["ticker"] for g in gaps],
                               [round(g["gap_pp"], 2) for g in gaps]),
            width="stretch", config=_NOBAR)
    with g2:
        st.dataframe(pd.DataFrame([{
            "종목": g["ticker"], "현재%": g["cur"], "목표%": g["tgt"],
            "갭%p": g["gap_pp"], "조정 필요": g["usd_delta"],
        } for g in gaps]), hide_index=True, width="stretch",
            height=min(302, 44 + 35 * len(gaps)),
            column_config={
                "현재%": st.column_config.NumberColumn(format="%.1f"),
                "목표%": st.column_config.NumberColumn(format="%.1f"),
                "갭%p": st.column_config.NumberColumn(format="%+.1f"),
                "조정 필요": st.column_config.NumberColumn(
                    format="$%.0f", help="+ = 매수 필요 · − = 축소 필요 (표시 전용)"),
            })
    unt = (rb or {}).get("untargeted") or []
    _ROLE = {"현금성 (초단기 국채)": "실탄", "인컴 (커버드콜)": "배당재투자",
             "레버리지 ETF (Tier3)": "Tier3 레버 — Phase 규칙",
             "지수·팩터 ETF": "지수 추종", "개별주": "개별주"}
    unt_desc = " · ".join(f"{t}({_ROLE.get(data.asset_class_of(t), '기타')})" for t in unt)
    st.caption(f"+갭 = 목표 초과(축소 방향)·−갭 = 목표 미달(증액 방향) · 근거 = 봇 "
               f"`/holding target` 에 직접 설정한 목표(합 "
               f"{(rb or {}).get('target_sum_pct', 0):.0f}%) 대비 이탈 — 모델 추천 아님 · "
               f"목표 미설정(갭 제외·각자 규칙): {unt_desc or '없음'} · "
               f"표시 전용 — 실행은 수동")


def _exposure_section(rows):
    """🏭 섹터·자산군 노출 — 집중도 분해."""
    ex = data.exposures(rows)
    if not ex:
        return
    st.markdown("##### 🏭 노출 분해")
    e1, e2 = st.columns(2)
    with e1:
        st.caption("자산군")
        cls = ex.get("class") or {}
        st.plotly_chart(charts.hbar(list(cls.keys()), list(cls.values()),
                                    pct=False, x_range=(0, 100)),
                        width="stretch", config=_NOBAR)
    with e2:
        st.caption("섹터 (개별주) + 자산군")
        sec = ex.get("sector") or {}
        st.plotly_chart(charts.hbar(list(sec.keys()), list(sec.values()), pct=False),
                        width="stretch", config=_NOBAR)
    st.caption("섹터 = S&P500 정적 시드 기준 (해외·미등록은 기타) · 단위 %")


def _holdings_table(rows):
    """보유 상세 — 평단·현재가·손익 색·비중 바 · 행 클릭 → 종목 분석."""
    if not rows:
        return
    st.markdown("##### 📋 보유 상세 — 행 클릭 = 종목 분석")
    from datetime import date as _date
    table = []
    for r in rows:
        sh = r.get("shares") or 0
        cost = r.get("cost") or 0
        _ed = None
        try:
            from providers.etf_data import is_etf as _is_etf
            if not _is_etf(r["ticker"]):        # ETF 는 실적일 없음 — 404 방지
                _ed = cached.next_earnings(r["ticker"])
        except Exception:
            pass
        _dd = (f"D-{(_ed - _date.today()).days}"
               if _ed and (_ed - _date.today()).days >= 0 else "—")
        table.append({
            "종목": f"{r.get('name') or ''} ({r['ticker']})".strip(),
            "실적": _dd,
            "주수": sh,
            "평단": (cost / sh) if sh > 0 and cost > 0 else None,
            "현재가": (r.get("value") or 0) / sh if sh > 0 else None,
            "평가액": r.get("value"),
            "손익%": r.get("ret"),
            "비중%": r.get("weight"),
        })
    df = pd.DataFrame(table)

    def _pl(v):
        try:
            return (f"color: {theme.GREEN}" if v > 0
                    else f"color: {theme.RED}" if v < 0 else "")
        except TypeError:
            return ""

    event = st.dataframe(
        df.style.map(_pl, subset=["손익%"]), hide_index=True, width="stretch",
        on_select="rerun", selection_mode="single-row", key="_pf_tbl",
        height=min(460, 44 + 35 * len(df)),
        column_config={
            "종목": st.column_config.TextColumn(width="medium", pinned=True),
            "실적": st.column_config.TextColumn(width="small",
                                                help="다음 실적 발표까지 D-일 (yfinance)"),
            "주수": st.column_config.NumberColumn(format="%.4f"),
            "평단": st.column_config.NumberColumn(format="$%.2f"),
            "현재가": st.column_config.NumberColumn(format="$%.2f"),
            "평가액": st.column_config.NumberColumn(format="$%.0f"),
            "손익%": st.column_config.NumberColumn(format="%+.1f%%"),
            "비중%": st.column_config.ProgressColumn(format="%.1f%%",
                                                     min_value=0, max_value=100),
        })
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if sel and sel[0] < len(rows):
        st.session_state["ticker"] = rows[sel[0]]["ticker"]
        pg = st.session_state.get("_ticker_page")
        if pg:
            st.switch_page(pg)


def _kr_section():
    """🇰🇷 국내(KR)북 — 키움 잔고 동기화 표시 (리스크 모델은 USD북 한정 — 분리 명시)."""
    kr = data.load_kr_holdings()
    if not kr:
        return
    st.markdown("##### 🇰🇷 국내 보유 — 키움 동기화")
    k1, k2 = st.columns([1, 2.2], vertical_alignment="center")
    k1.metric("국내 평가액", f"₩{kr['total']:,.0f}",
              help=f"마지막 동기화 {kr.get('last_sync') or '—'} (평일 08:35 KST 크론)")
    with k2:
        st.dataframe(pd.DataFrame([{
            "종목": r["name"], "주수": r["shares"], "평단": r["avg"],
            "현재가": r["cur"], "평가액": r["value"], "손익%": r["ret"],
        } for r in kr["rows"]]), hide_index=True, width="stretch",
            height=min(250, 44 + 35 * len(kr["rows"])),
            column_config={
                "평단": st.column_config.NumberColumn(format="localized"),
                "현재가": st.column_config.NumberColumn(format="localized"),
                "평가액": st.column_config.NumberColumn(format="localized"),
                "손익%": st.column_config.NumberColumn(format="%+.1f%%"),
            })
    st.caption("위 리스크·성장·리밸런스 분석은 **USD 해외북 한정** — 국내북은 잔고 표시만"
               "(키움 kt00018 동기화·기록 전용)")


def _income_section(rows):
    """💰 인컴·적립 — 분배금 누적·QQQI 월 예상·자동 모으기 월 적립."""
    qqqi = next((r for r in rows if r.get("ticker") == "QQQI"), None)
    inc = cached.income_summary((qqqi or {}).get("shares", 0.0),
                                (qqqi or {}).get("value", 0.0))
    plans = []
    try:
        from lib import accumulation
        plans = [p for p in accumulation.load_plans() if p.get("enabled", True)]
    except Exception:
        pass
    if not inc.get("records") and inc.get("est_monthly") is None and not plans:
        return
    st.markdown("##### 💰 인컴 · 적립 현황")
    _MULT = {"매일": 21.0, "매주": 4.33, "매월": 1.0}
    krw_m = sum(p["amount"] * _MULT.get(p.get("freq"), 1) for p in plans
                if p.get("currency") == "KRW")
    usd_m = sum(p["amount"] * _MULT.get(p.get("freq"), 1) for p in plans
                if p.get("currency") == "USD")
    i = st.columns(4)
    i[0].metric("분배금 누적 (기록)", data.f_usd(inc.get("total", 0.0)),
                help=f"기록 {len(inc.get('records') or [])}건 — 봇 /holding dividend 와 동일 원장")
    est = inc.get("est_monthly")
    i[1].metric("QQQI 월 예상 분배", data.f_usd(est) if est else "—",
                help=(inc.get("est_detail") or {}).get("note", "추정 — 보장 아님"))
    i[2].metric("자동 모으기 월 적립(₩)", f"₩{krw_m:,.0f}" if krw_m else "—",
                help="활성 플랜 × 주기 환산 (매일×21·매주×4.33)")
    i[3].metric("자동 모으기 월 적립($)", f"${usd_m:,.2f}" if usd_m else "—")
    st.caption("분배 예상은 최근 배당 기준 추정(보장 아님) · 적립은 기록 전용 플랜 합산")
