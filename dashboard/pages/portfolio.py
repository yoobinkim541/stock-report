"""포트폴리오 — 리스크 시각화 + 배분 (표시 전용·배분 불변)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import cached, charts, data

_NOBAR = {"displayModeBar": False}
_FAC_LABEL = {"mkt": "시장β(QQQ)", "rate": "금리β(TLT)"}


def render():
    st.title("💼 포트폴리오")
    st.caption("USD 해외북 · 표시 전용 · 배분 변경 아님")

    s = cached.risk_struct()
    if s.get("error"):
        st.warning(f"리스크 분석 불가: {s['error']}")
    else:
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
        with col1:
            contribs = s.get("contributions") or []
            if contribs:
                st.caption("위험 기여도 (참여비)")
                st.plotly_chart(
                    charts.hbar([t for t, _w, _pc in contribs],
                                [pc for _t, _w, pc in contribs], pct=True),
                    width="stretch", config=_NOBAR)
        with col2:
            fn = s.get("factor_net") or {}
            if fn:
                st.caption("팩터 순베타")
                st.plotly_chart(
                    charts.signed_bars([_FAC_LABEL.get(k, k) for k in fn.keys()],
                                       [round(float(v), 2) for v in fn.values()]),
                    width="stretch", config=_NOBAR)
                if s.get("factor_caveat"):
                    st.caption(s["factor_caveat"])

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

    st.divider()
    rows = data.load_holdings()
    if rows:
        c1, c2 = st.columns([1, 1.3])
        with c1:
            st.caption("배분")
            st.plotly_chart(charts.allocation_donut(rows), width="stretch", config=_NOBAR)
        with c2:
            st.caption("보유 상세")
            df = pd.DataFrame([{
                "종목": r["ticker"], "이름": (r["name"] or "")[:18], "주수": r["shares"],
                "평가액($)": round(r["value"]), "손익%": round(r["ret"], 1), "비중%": round(r["weight"], 1),
            } for r in rows])
            st.dataframe(df, hide_index=True, width="stretch")

    with st.expander("리스크 전체 리포트 (텍스트)"):
        st.code(cached.risk(), language=None)
    st.caption("과거 1년 실현 기반 · 미래 보장 아님 · 국내 제외(USD북)")
