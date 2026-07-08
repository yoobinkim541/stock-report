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


def _fmt_plan_amt(p: dict) -> str:
    return (f"₩{p.get('amount', 0):,.0f}" if p.get("currency") == "KRW"
            else f"${p.get('amount', 0):,.2f}")


def sidebar_rail() -> None:
    """사이드바 컴팩트 레일 — 오늘 총액·Phase 배율 + 🔁 자동 플랜 목록 + 관리 버튼."""
    plan = cached.accumulation()
    from lib import accumulation
    aps = accumulation.load_plans()
    if (not plan or not plan.get("rows")) and not aps:
        return                                     # 계획·플랜 모두 없음 — 조용히 생략
    head = ""
    if plan and plan.get("rows"):
        mult = plan.get("mult")
        head = (
            f'<div style="display:flex;justify-content:space-between;padding:0 2px">'
            f'<span style="color:{theme.MUTED};font-size:0.78rem">오늘 배분</span>'
            f'<b style="font-family:JetBrains Mono,monospace">{_f_krw(plan.get("total_krw"))}'
            f' · {len(plan["rows"])}종목 · {plan.get("emoji", "")} {mult:g}×</b></div>')
    # 🔁 자동 기록 플랜 — 모으는 중인 종목·금액·주기 한눈에 (OFF 는 흐리게)
    rows_html = ""
    for p_ in aps:
        dim = "" if p_.get("enabled", True) else "opacity:0.45"
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;padding:2px 2px;{dim}">'
            f'<span style="font-size:0.78rem">{p_.get("ticker", "")}</span>'
            f'<span style="color:{theme.MUTED};font-size:0.74rem;'
            f'font-family:JetBrains Mono,monospace">{p_.get("freq", "")} '
            f'{_fmt_plan_amt(p_)}</span></div>')
    auto = ""
    if rows_html:
        auto = (f'<div style="color:{theme.MUTED};font-size:0.7rem;'
                f'padding:6px 2px 2px;border-top:1px solid {theme.BORDER};margin-top:5px">'
                f'🔁 자동 모으기 (종가 자동 기록)</div>{rows_html}')
    st.markdown(
        f'<div class="tn-wl"><div style="display:flex;justify-content:space-between;'
        f'align-items:center;padding:2px 2px 6px"><b style="font-size:0.85rem">💰 주식 모으기</b>'
        f'</div>{head}{auto}</div>',
        unsafe_allow_html=True)
    if st.button("💰 모으기 관리", width="stretch", key="_accum_btn",
                 help="오늘 배분 계획·기록·자동 플랜 금액/주기 편집 (기록 전용 — 실주문 아님)"):
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

    # 💱 환전 타이밍 (아침 리포트와 동일 산식 — 원화→달러 분할 환전 가이드)
    ft = cached.fx_timing()
    if ft.get("ok"):
        _c = {"🟢": theme.GREEN, "🟡": theme.AMBER, "🔴": theme.RED}.get(ft.get("emoji"), theme.MUTED)
        st.markdown(
            f'<div style="border-left:3px solid {_c};background:{theme.PANEL};'
            f'border:1px solid {theme.BORDER};border-radius:8px;padding:7px 12px;'
            f'font-size:0.84rem">💱 <b>환전 타이밍</b> {ft.get("emoji", "")} '
            f'<b style="color:{_c}">{ft.get("verdict", "")}</b> — {ft.get("action", "")} · '
            f'현재 {ft.get("rate", 0):,.1f}원 · 5y 위치 {ft.get("pct_display", "—")}%ile · '
            f'분할 환전 배율 <b>{ft.get("multiplier", 1):g}×</b></div>',
            unsafe_allow_html=True)

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

    # ── 🔁 자동 기록 플랜 — 금액·주기 즉시 편집 (last_run 보존·재트리거 없음) ──
    from lib import accumulation
    aps = accumulation.load_plans()
    st.markdown("##### 🔁 자동 기록 플랜 — 금액·주기 바로 편집")
    _FREQS = ["매일", "매주", "매월"]
    if aps:
        for p_ in aps:
            t_ = p_.get("ticker", "")
            is_krw = p_.get("currency") == "KRW"
            r1, r2, r3, r4, r5 = st.columns([1.7, 1.3, 1.0, 0.6, 0.6],
                                            vertical_alignment="center")
            r1.markdown(f"**{ticker_names.label(t_, maxlen=20)}**<br>"
                        f"<span style='color:{theme.MUTED};font-size:0.72rem'>"
                        f"마지막 {p_.get('last_run') or '아직 없음'}</span>",
                        unsafe_allow_html=True)
            new_amt = r2.number_input(
                f"금액 ({'₩' if is_krw else '$'})", min_value=0.0,
                value=float(p_.get("amount", 0)),
                step=(1000.0 if is_krw else 1.0),
                format=("%.0f" if is_krw else "%.2f"), key=f"_ap_amt_{t_}")
            cur_freq = p_.get("freq", "매일")
            new_freq = r3.selectbox("주기", _FREQS,
                                    index=_FREQS.index(cur_freq) if cur_freq in _FREQS else 0,
                                    key=f"_ap_frq_{t_}")
            # 변경 즉시 저장 — last_run·ON/OFF 보존이라 이미 기록된 주기 재트리거 없음
            if (new_amt > 0 and abs(new_amt - float(p_.get("amount", 0))) > 1e-9) \
                    or new_freq != cur_freq:
                accumulation.update_plan(t_, amount=new_amt, freq=new_freq)
                st.toast(f"💾 {t_} 플랜 저장 — {new_freq} "
                         f"{'₩' if is_krw else '$'}{new_amt:,.0f}")
            on = r4.toggle("ON", value=p_.get("enabled", True), key=f"_ap_on_{t_}")
            if on != p_.get("enabled", True):
                accumulation.set_enabled(t_, on)
            if r5.button("삭제", key=f"_ap_del_{t_}"):
                accumulation.remove_plan(t_)
                st.rerun()
        st.caption("변경 즉시 저장(다음 기록부터 반영) · 매 미국 세션 마감(06:10 KST) 후 "
                   "그날 종가·확정 종가 환율로 자동 **기록** — 실계좌 주문 아님 · "
                   "매주=주 첫 거래일·매월=월 첫 거래일")
    else:
        st.caption("등록된 플랜 없음 — 아래에서 바로 추가하거나 종목분석 ⚙️ 적립 폼에서 등록")

    # 신규 플랜 추가 (다이얼로그에서 바로)
    with st.expander("➕ 새 자동 모으기 추가", expanded=not aps):
        n1, n2, n3, n4 = st.columns([1.6, 1.1, 0.9, 0.9], vertical_alignment="bottom")
        _cand = [t for t in ticker_names.universe()
                 if not any(p.get("ticker") == t for p in aps)]
        new_t = n1.selectbox("종목", _cand, format_func=ticker_names.search_label,
                             accept_new_options=True, key="_ap_new_t",
                             help="목록 밖 티커 직접 입력 가능")
        new_cur = n2.segmented_control("통화", ["₩ 원화", "$ 달러"], default="₩ 원화",
                                       key="_ap_new_cur")
        new_amt2 = n3.number_input("금액", min_value=0.0,
                                   value=10_000.0 if new_cur == "₩ 원화" else 10.0,
                                   key="_ap_new_amt")
        new_frq2 = n4.selectbox("주기", _FREQS, key="_ap_new_frq")
        if st.button("🔁 자동 모으기 등록", key="_ap_new_btn", type="primary",
                     disabled=(not new_t or new_amt2 <= 0)):
            tk = ticker_names.normalize_input(str(new_t)) or str(new_t).upper()
            msg = accumulation.upsert_plan(tk, new_amt2,
                                           "KRW" if new_cur == "₩ 원화" else "USD",
                                           new_frq2)
            (st.success if msg.startswith("🔁") else st.warning)(msg)
            if msg.startswith("🔁"):
                st.rerun()

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
