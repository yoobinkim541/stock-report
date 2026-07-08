"""dashboard/accumulate.py — 사이드바 💰 주식 모으기 통합 관리.

계획(오늘 배분·Phase 배율) = `bot/order_generator.build()`(봇 /order 와 동일 산식)
→ 사이드바 컴팩트 레일 + st.dialog 관리(계획표·오늘 모으기 기록·비중 편집).

- **기록 전용** — 실제 매수는 키움 앱 소수점 매수로 수동(실계좌 자동주문 0 원칙).
  기록은 봇 `/holding buy` 와 동일하게 `holding_manager.buy_holding(fractional)` 경유.
- 비중 편집 = `holding_manager.set_dca_weights`(합계 자동 정규화·0=삭제) — 봇과 동일 소스.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import ticker_names
from dashboard import cached, theme


def _f_krw(v):
    try:
        return f"{v:,.0f}원"
    except (TypeError, ValueError):
        return "—"


def sidebar_rail() -> None:
    """사이드바 컴팩트 레일 — 오늘 총액·Phase 배율 + 관리 다이얼로그 버튼."""
    plan = cached.accumulation()
    if not plan or not plan.get("rows"):
        return                                     # 계획 없음/조회 실패 — 조용히 생략
    mult = plan.get("mult")
    st.markdown(
        f'<div class="tn-wl"><div style="display:flex;justify-content:space-between;'
        f'align-items:center;padding:2px 2px 6px"><b style="font-size:0.85rem">💰 주식 모으기</b>'
        f'<span style="color:{theme.MUTED};font-size:0.72rem">'
        f'{plan.get("emoji", "")} {mult:g}×</span></div>'
        f'<div style="display:flex;justify-content:space-between;padding:0 2px">'
        f'<span style="color:{theme.MUTED};font-size:0.78rem">오늘 배분</span>'
        f'<b style="font-family:JetBrains Mono,monospace">{_f_krw(plan.get("total_krw"))}'
        f' · {len(plan["rows"])}종목</b></div></div>',
        unsafe_allow_html=True)
    if st.button("💰 모으기 관리", width="stretch", key="_accum_btn",
                 help="오늘 배분 계획·기록·비중 편집 (기록 전용 — 실주문 아님)"):
        _manage_dialog()


@st.dialog("💰 주식 모으기 관리", width="large")
def _manage_dialog():
    plan = cached.accumulation()
    if not plan or not plan.get("rows"):
        st.caption("계획 데이터 없음 (네트워크/DCA 비중 확인)")
        return
    st.caption(f"{plan.get('emoji', '')} {plan.get('label', '')} · QQQ 낙폭 "
               f"{plan.get('dd', 0):+.1f}% · 배율 {plan.get('mult', 1):g}× · "
               f"환율 {plan.get('fx', 0):,.0f}원 **(확정 종가 자동 — 하루 고정)** · "
               f"{plan.get('now', '')}")

    # ── 오늘 배분 계획표 ──
    rows = plan["rows"]
    import pandas as pd
    st.dataframe(pd.DataFrame([{
        "종목": ticker_names.label(r["ticker"], maxlen=24),
        "금액": _f_krw(r["krw_amt"]),
        "수량": (f"{r['qty']:.4f}주" if r.get("qty") is not None else "가격 조회 실패"),
        "현재가": (f"${r['price']:,.2f}" if r.get("price") else "—"),
    } for r in rows]), hide_index=True, width="stretch")
    st.caption(f"합계 {_f_krw(plan.get('total_krw'))} ≈ ${plan.get('total_usd', 0):,.2f}"
               " · 산식 = 봇 /order 와 동일 (Phase 배율·안전가드 반영)")

    # ── 오늘 모으기 기록 (소수점 계좌 · 기록 전용) ──
    ok_rows = [r for r in rows if r.get("qty")]
    done_key = "_accum_recorded"
    already = st.session_state.get(done_key) == date.today().isoformat()
    c1, c2 = st.columns([1, 1.6])
    if c1.button(f"📥 오늘 모으기 기록 ({len(ok_rows)}종목)", type="primary",
                 disabled=(already or not ok_rows), key="_accum_rec"):
        import holding_manager
        okc, fails = 0, []
        for r in ok_rows:
            try:
                holding_manager.buy_holding(r["ticker"], r["qty"], r["price"],
                                            fractional=True, note="주식 모으기(대시보드)")
                okc += 1
            except Exception:
                fails.append(r["ticker"])
        st.session_state[done_key] = date.today().isoformat()
        cached.accumulation.clear()
        if fails:
            st.warning(f"{okc}건 기록 · 실패: {', '.join(fails)}")
        else:
            st.success(f"✅ {okc}종목 매수 기록 완료 (소수점 계좌·평단 자동 재계산)")
    with c2:
        if already:
            st.caption("오늘은 이미 기록했습니다 (중복 방지 — 내일 다시 활성)")
        st.caption("⚠️ **기록 전용** — 실제 매수는 키움 앱 → 해외주식 → 소수점 매수")

    # ── 🔁 자동 기록 플랜 (매 세션 미 종가·확정 종가 환율로 자동 기록 — 크론) ──
    from lib import accumulation
    aps = accumulation.load_plans()
    st.markdown("##### 🔁 자동 기록 플랜")
    if aps:
        for p_ in aps:
            t_ = p_.get("ticker", "")
            amt_ = (f"₩{p_.get('amount', 0):,.0f}" if p_.get("currency") == "KRW"
                    else f"${p_.get('amount', 0):,.2f}")
            r1, r2, r3, r4 = st.columns([2.4, 1.2, 0.8, 0.7],
                                        vertical_alignment="center")
            r1.markdown(f"**{ticker_names.label(t_, maxlen=22)}** — "
                        f"{p_.get('freq')} {amt_}")
            r2.caption(f"마지막 {p_.get('last_run') or '아직 없음'}")
            on = r3.toggle("ON", value=p_.get("enabled", True), key=f"_ap_on_{t_}")
            if on != p_.get("enabled", True):
                accumulation.set_enabled(t_, on)
            if r4.button("삭제", key=f"_ap_del_{t_}"):
                accumulation.remove_plan(t_)
                st.rerun()
        st.caption("매 미국 세션 마감(06:10 KST) 후 그날 종가·확정 종가 환율로 소수점 계좌에 "
                   "자동 **기록** — 실계좌 주문 아님(키움 주식모으기 결과의 거울) · "
                   "매주=주 첫 거래일·매월=월 첫 거래일 · 등록은 종목분석 ⚙️ 적립 폼")
    else:
        st.caption("등록된 플랜 없음 — 종목분석 → ⚙️ 내 포지션 관리 → 💧 적립에서 "
                   "'🔁 자동 기록 등록'을 누르면 매 세션 종가로 자동 기록됩니다.")

    # ── 비중 편집 (봇 /holding dca 와 동일 소스·합계 자동 정규화) ──
    with st.expander("⚖️ 모으기 비중 편집", expanded=False):
        try:
            import holding_manager
            w_normal, _ = holding_manager.get_dca_weights()
        except Exception:
            st.caption("비중 로드 실패")
            return
        edited = {}
        cols = st.columns(3)
        for i, (t, w) in enumerate(sorted(w_normal.items(), key=lambda x: -x[1])):
            edited[t] = cols[i % 3].number_input(
                ticker_names.label(t, maxlen=18), min_value=0.0, max_value=100.0,
                value=round(w * 100, 1), step=1.0, key=f"_accw_{t}")
        nc1, nc2 = st.columns([1, 1])
        new_t = nc1.text_input("신규 티커 추가", key="_accw_new",
                               placeholder="예: AVGO").strip().upper()
        new_w = nc2.number_input("신규 비중 %", min_value=0.0, max_value=100.0,
                                 value=0.0, step=1.0, key="_accw_neww")
        if st.button("💾 비중 저장 (합계 자동 정규화 · 0% = 제외)", key="_accw_save"):
            # % → 분수로 변환해 전달 (set_dca_weights 의 `v>1 → /100` 휴리스틱이
            # 정확히 1% 입력을 분수 1.0=100% 로 오인하는 경계 방어)
            updates = {t: v / 100.0 for t, v in edited.items()}
            if new_t and new_w > 0:
                tk = ticker_names.normalize_input(new_t) or new_t
                updates[tk] = new_w / 100.0
            import holding_manager
            msg = holding_manager.set_dca_weights(updates, mode="normal")
            cached.accumulation.clear()
            (st.success if msg.startswith("✅") else st.warning)(msg.splitlines()[0])
            st.caption("다음 계획부터 반영 — 표 새로고침은 다이얼로그 재열기")
        st.caption("약세장(Phase2+) 전용 비중은 봇 `/holding dca bear ...` 로 편집")
