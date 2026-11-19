"""리서치 — 종목 랭킹 스크리너 + ML 전략 백테스트 + 정책 학습곡선.

무거운 계산(스크리너·백테스트 각 최대 1분)은 **진입 시 자동실행 안 함** — 섹션 셀렉터로
한 번에 하나만, 그 안에서 ▶실행 버튼을 눌러야 계산. 각 섹션은 @st.fragment 라 슬라이더·
버튼 조작이 페이지 전체가 아니라 그 섹션만 rerun(자연스러운 부분 갱신).
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_console import context as agent_context
from dashboard import cached, charts, data, strategy_studio, ui_actions

_NOBAR = {"displayModeBar": False}
_SECTIONS = ["종목 랭킹", "전략 스튜디오", "전략 백테스트", "정책 학습", "축 게이트"]
_AXIS_LABEL = {
    "hi52": "52주고점", "lowvol": "저변동", "vol_inv": "저변동", "mom12": "12개월",
    "mom": "모멘텀", "rev1": "단기반전", "liq": "유동성", "size": "시총",
    "balanced": "균형", "small": "소형주", "anti-small": "대형주",
    "quality-ish": "품질형", "mom_scaled": "조정 모멘텀",
}
_GATE_CODE = {"OBSERVE": "관찰", "GO": "통과", "NO-GO": "차단", "NO_GO": "차단",
              "CONFIRMED": "확인", "ROBUST": "견고", "MIXED": "혼재", "IN-SAMPLE": "표본 안"}


def _axis_label(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    if raw in _AXIS_LABEL:
        return _AXIS_LABEL[raw]
    parts = []
    token = ""
    for ch in raw:
        if ch in "+-":
            if token:
                parts.append(_AXIS_LABEL.get(token, token))
                token = ""
            parts.append(ch)
        else:
            token += ch
    if token:
        parts.append(_AXIS_LABEL.get(token, token))
    return "".join(parts) or raw


def _axis_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " · ".join(_axis_label(v) for v in value) or "—"
    return _axis_label(value)


def _yes_no(value) -> str:
    if value is True:
        return "예"
    if value is False:
        return "아니오"
    return "—" if value in (None, "") else str(value)


def _gate_headline(value) -> str:
    text = str(value or "")
    for code, label in (("NO-GO", "차단"), ("NO_GO", "차단"), ("OBSERVE", "관찰"), ("GO", "통과")):
        text = text.replace(code, label)
    return text.replace("OOS", "검증구간")






def render():
    st.title("리서치")
    sec = st.segmented_control("섹션", _SECTIONS, default="종목 랭킹",
                               key="research_section", label_visibility="collapsed") or "종목 랭킹"
    if sec == "전략 스튜디오":
        _strategy_studio_section()
    elif sec == "전략 백테스트":
        _backtest_section()
    elif sec == "정책 학습":
        _learning_section()
    elif sec == "축 게이트":
        _axes_gate_section()
    else:
        _screener_section()


@st.fragment
def _screener_section():
    st.subheader("종목 랭킹 스크리너")
    st.caption("나스닥 100 · 모델이 QQQ 대비 초과수익을 예측")
    topn = st.slider("상위 N", 10, 50, 20, 5, key="scr_topn")
    last = cached.screener_last()
    c_run, c_info = st.columns([1, 2.2], vertical_alignment="center")
    run = c_run.button("🔄 다시 실행 (최대 1분)" if last else "▶ 스크리너 실행 (최대 1분)",
                       key="scr_btn", type="primary", width="stretch")
    if run:
        cached.screener.clear()                       # 인메모리 캐시 무시 — 진짜 재계산
        sc = cached.screener(topn)
        cached.screener_last.clear()
        st.session_state["scr_done"] = True
        st.session_state["scr_ran_n"] = topn
    elif st.session_state.get("scr_done"):
        sc = cached.screener(st.session_state.get("scr_ran_n") or topn)
    elif last:
        sc = last                                     # 💾 마지막 실행 결과 즉시 표시
        saved_n = last.get("topn")
        note = (f"💾 마지막 실행 {last.get('asof', '—')} · 상위 {saved_n if saved_n is not None else '?'} "
                "— 최신화는 다시 실행")
        if isinstance(saved_n, int) and topn > saved_n:
            note += f" · 저장분은 {saved_n}건 — 더 보려면 다시 실행"
        c_info.caption(note)
    else:
        st.info("버튼을 눌러 스크리너를 실행하세요 — 무거운 ML 계산이라 진입 시 자동 실행하지 않습니다. "
                "한 번 실행하면 결과가 저장돼 다음부터 즉시 표시됩니다.")
        return
    meta = sc.get("meta") or {}
    if meta:
        from dashboard import theme
        _ic = meta.get("ic")
        _ic_col = theme.GREEN if (_ic or 0) > 0 else theme.RED
        st.markdown(
            _chip(f"OOS IC {data.f_ratio(_ic, 3)}", _ic_col)
            + _chip(f"ICIR {data.f_ratio(meta.get('icir'), 2)}", theme.MUTED)
            + _chip(f"상위10% 초과 {data.f_frac_pct_s(meta.get('top_decile'))}",
                    theme.GREEN if (meta.get("top_decile") or 0) > 0 else theme.RED)
            + _chip(f"학습 {meta.get('train_end', '—')}", theme.MUTED),
            unsafe_allow_html=True)
    rows = (sc.get("rows") or [])[:int(topn)]
    if rows:
        from dashboard import theme
        prev_ranks = sc.get("prev_ranks") or {}
        table = [{
            "순위": data.rank_badge(r.get("rank")),
            "변동": (data.rank_move(r.get("rank"), prev_ranks.get(r.get("ticker")))
                     if prev_ranks else "—"),
            "종목": (f"{r['name']} ({r['ticker']})" if r.get("name") else r.get("ticker", ""))
                    + (" ⚠️" if r.get("surv_flag") else ""),
            "점수": r.get("score"),
            "가격": r.get("price"),
            "기술등급": r.get("tech_rating") or "—",
            "RSI": r.get("rsi_14"),
            "52주고점比": (r["close_vs_52w_high"] * 100
                           if r.get("close_vs_52w_high") is not None else None),
            "6개월%": (r["mom_126d"] * 100 if r.get("mom_126d") is not None else None),
            "QQQ대비%p": (r["excess_mom_60d"] * 100
                          if r.get("excess_mom_60d") is not None else None),
            "재무점수": r.get("fund_score"),
            "판단근거": r.get("reason") or "—",
        } for r in rows]
        df = pd.DataFrame(table)

        def _updown(v):                                  # 등락 색 (± 셀 텍스트)
            try:
                return (f"color: {theme.GREEN}" if v > 0
                        else f"color: {theme.RED}" if v < 0 else "")
            except TypeError:
                return ""

        def _movecolor(v):
            sv = str(v)
            if sv.startswith("▲") or sv == "신규":
                return f"color: {theme.GREEN}"
            if sv.startswith("▼"):
                return f"color: {theme.RED}"
            return f"color: {theme.MUTED}"

        sty = (df.style
               .map(_updown, subset=["6개월%", "QQQ대비%p"])
               .map(_movecolor, subset=["변동"])
               .map(lambda v: (f"color: {theme.RED}" if isinstance(v, (int, float)) and v >= 70
                               else f"color: {theme.GREEN}"
                               if isinstance(v, (int, float)) and v <= 30 else ""),
                    subset=["RSI"]))
        _smin, _smax = score_bounds(df["점수"])
        event = st.dataframe(
            sty, hide_index=True, width="stretch",
            height=min(302, 44 + 35 * len(df)),
            on_select="rerun", selection_mode="single-row", key="_scr_tbl",
            column_config={
                "순위": st.column_config.TextColumn(width="small"),
                "변동": st.column_config.TextColumn(
                    width="small", help="직전 실행 대비 순위 변동 — 신규 = 처음 진입"),
                "종목": st.column_config.TextColumn(width="medium", pinned=True),
                "점수": st.column_config.ProgressColumn(
                    format="%.3f", min_value=_smin, max_value=_smax,
                    help="LGBM 상대순위 점수 (임의 스케일 — 바는 그룹 내 상대 위치)"),
                "가격": st.column_config.NumberColumn(format="$%.2f"),
                "RSI": st.column_config.NumberColumn(format="%.0f",
                                                     help="70↑ 과열(적)·30↓ 과매도(녹)"),
                "52주고점比": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100,
                    help="52주 고점 대비 현재가 위치"),
                "6개월%": st.column_config.NumberColumn(format="%+.1f"),
                "QQQ대비%p": st.column_config.NumberColumn(format="%+.1f"),
                "재무점수": st.column_config.ProgressColumn(
                    format="%.0f", min_value=0, max_value=100),
                "판단근거": st.column_config.TextColumn(width="large",
                                                        help="두드러진 특징 상위 3 (모델 기여도 아님)"),
            })
        _screener_detail(event, rows, sc.get("feats") or {},
                         (sc.get("meta") or {}).get("importance") or {})
    elif sc.get("gate_reasons"):
        st.info(gate_message(sc["gate_reasons"], (sc.get("meta") or {}).get("train_end")))
    else:
        st.warning(ui_actions.user_message(sc.get("error"), "랭킹이 없습니다."))
    _pa = sc.get("prev_asof")
    st.caption("⚠️ 생존편향(⚠️) + 검증상 종목선택 무엣지 — 정보·표시용, 매매신호 아님 · "
               "행 클릭 = 상세" + (f" · 변동 기준 = 직전 실행 {_pa}" if _pa else ""))


_GATE_REASON_LABELS = (
    ("stale", "모델이 오래됨"),
    ("oos_ic", "검증 IC 가 0 이하"),
    ("oos_excess", "검증 초과수익 없음"),
    ("purged_walk_forward", "워크포워드 검증 없음"),
    ("finite:", "검증 지표 누락"),
)


def gate_message(reasons: list[str], train_end=None) -> str:
    """ranker_gate 사유 코드 → 사용자 문장. 같은 뜻의 코드는 한 번만."""
    labels: list[str] = []
    for code in reasons:
        label = next((lab for key, lab in _GATE_REASON_LABELS if key in str(code)), None)
        if label and label not in labels:
            labels.append(label)
    why = " · ".join(labels) or "검증 기준 미달"
    trained = f" (학습 {train_end})" if train_end else ""
    return (f"모델이 검증 게이트를 통과하지 못해 랭킹을 내지 않습니다{trained} — {why}. "
            "검증상 우위가 없는 모델로 종목을 줄 세우지 않기 위한 안전장치이며, 재학습이 통과하면 다시 표시됩니다.")


def score_bounds(scores: pd.Series) -> tuple[float, float]:
    """ProgressColumn 범위 — 유한값만 쓰고 max>min 을 보장한다.
    1행이거나 점수가 모두 같으면 min==max 가 되어 셀이 'Invalid min/max parameters' 로 깨졌다."""
    vals = pd.to_numeric(scores, errors="coerce").dropna()
    vals = vals[vals.abs() != float("inf")]
    if vals.empty:
        return 0.0, 1.0
    lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        return lo - 0.5, hi + 0.5
    return lo, hi


def _chip(text, color):
    return (f'<span style="display:inline-block;margin:2px 6px 2px 0;padding:4px 11px;'
            f'border-radius:999px;color:{color};font-weight:600;'
            f'font-size:0.78rem;background:{color}1f">{text}</span>')


def _screener_detail(event, rows, feats, importance):
    """선택 행 상세 카드 — 선정 근거 칩·지표 밴드 2열·전체 피처(한글·카테고리) 표."""
    from dashboard import theme
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if not sel or sel[0] >= len(rows):
        return
    r = rows[sel[0]]
    t = r.get("ticker", "")
    f = feats.get(t) or {}

    # ── 헤더 카드: 순위·점수·기술등급 + 선정 근거 칩 ──
    reason_chips = "".join(_chip(x.strip(), theme.BLUE)
                           for x in (r.get("reason") or "").split("·") if x.strip() and x.strip() != "—")
    rating = r.get("tech_rating") or "—"
    r_col = theme.GREEN if "매수" in rating else theme.RED if "매도" in rating else theme.MUTED
    st.markdown(
        f'<div style="background:{theme.PANEL};border-radius:20px;padding:16px 20px">'
        f'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
        f'<b style="font-size:1.15rem">🔎 {r.get("name") or t} ({t})</b>'
        f'<span style="color:{theme.MUTED};font-size:0.82rem">랭킹 {r.get("rank")}위 · '
        f'점수 {r.get("score"):.3f}</span>'
        f'{_chip(rating, r_col)}</div>'
        f'<div style="margin-top:6px"><span style="color:{theme.MUTED};font-size:0.74rem;'
        f'margin-right:6px">선정 근거</span>{reason_chips or "—"}</div>'
        f'<div style="color:{theme.MUTED};font-size:0.7rem;margin-top:4px">'
        f'근거 = 두드러진 특징 서술(모델 기여도 아님) · 표시·참고용 · 매매신호 아님</div></div>',
        unsafe_allow_html=True)

    # ── 지표 밴드 — 1행: 스크리너 피처(무네트워크) · 2행: 밸류·매집(선택 티커만 조회) ──
    m1 = st.columns(5)
    m1[0].metric("RSI(14)", data.f_ratio(f.get("rsi_14"), 1) if f.get("rsi_14") is not None else "—")
    m1[1].metric("52주 고점比", f"{r['close_vs_52w_high'] * 100:.0f}%"
                 if r.get("close_vs_52w_high") is not None else "—")
    m1[2].metric("6개월 모멘텀", f"{r['mom_126d'] * 100:+.1f}%"
                 if r.get("mom_126d") is not None else "—")
    m1[3].metric("QQQ 대비(60일)", f"{r['excess_mom_60d'] * 100:+.1f}%p"
                 if r.get("excess_mom_60d") is not None else "—")
    m1[4].metric("재무 점수", f"{r['fund_score']:.0f}" if r.get("fund_score") is not None else "—",
                 help="수익성·이익의 질·안정성·성장·자본배분 종합 (0~100)")
    v = cached.valuation(t) or {}
    m = v.get("metrics") or {}
    _pt = data.peg_textbook(m)
    _g = data.eps_growth_fwd(m)
    inst = cached.institutional(t) or {}
    ac = inst.get("accum") or {}
    sig = ac.get("signals") or {}
    cmf_val = sig.get("cmf", f.get("cmf_21"))            # 매집 조회 실패 시 피처 폴백
    m2 = st.columns(5)
    m2[0].metric("PER", data.f_ratio(m.get("per")))
    m2[1].metric("PEG (계산)", data.f_ratio((_pt or {}).get("peg")),
                 help="PER ÷ 예상 EPS 증가율 (선행/최근 1년)")
    m2[2].metric("EPS 성장률", f"{_g:+.1f}%" if _g is not None else "—")
    m2[3].metric("매집 강도", data.f_ratio(ac.get("accum_score"), 1) if ac else "—",
                 help="OBV·CMF·상승/하락 거래량 종합 — 조회 실패 시 —")
    m2[4].metric("CMF 자금흐름", data.f_ratio(cmf_val, 2) if cmf_val is not None else "—",
                 help="+ 유입 / − 유출 (매집 조회 실패 시 스크리너 피처값)")

    # ── 🧠 모델이 보는 핵심 피처 — 중요도 상위 (값 병기) ──
    tb = data.top_feature_bars(f, importance, top=8)
    if tb:
        st.plotly_chart(charts.hbar(tb["labels"], tb["values"],
                                    "🧠 모델 중요도 상위 — 이 종목의 값", pct=False),
                        width="stretch", config=_NOBAR)

    # ── 전체 피처 — 한글 라벨·카테고리·스마트 포맷 (중요도 순) ──
    fr = data.format_screener_features(f, importance)
    tcol, bcol = st.columns([2.1, 0.9], vertical_alignment="bottom")
    with tcol:
        st.markdown("**전체 피처** — 모델 중요도 순 · 한글 라벨")
    with bcol:
        if st.button("🔍 종목 분석 열기", key=f"_scr_open_{t}", width="stretch"):
            ui_actions.open_ticker(str(t), toast=f"종목 분석 → {t}")
    if fr:
        cats = ["전체"] + sorted({x["구분"] for x in fr})
        pick = st.pills("구분", cats, default="전체", selection_mode="single",
                        key=f"_scr_cat_{t}", label_visibility="collapsed") or "전체"
        show = fr if pick == "전체" else [x for x in fr if x["구분"] == pick]
        from dashboard import theme as _th

        def _valcolor(v):                            # 부호·플래그 시맨틱 색
            sv = str(v)
            if sv.startswith("+") or sv == "✓":
                return f"color: {_th.GREEN}"
            if sv.startswith("-"):
                return f"color: {_th.RED}"
            return ""

        sdf = pd.DataFrame(show).style.map(_valcolor, subset=["값"])
        st.dataframe(sdf, hide_index=True, width="stretch",
                     height=min(330, 44 + 35 * len(show)),
                     column_config={
                         "지표": st.column_config.TextColumn(width="medium"),
                         "값": st.column_config.TextColumn(width="small"),
                         "구분": st.column_config.TextColumn(width="small"),
                     })


@st.fragment
def _strategy_studio_section():
    st.subheader("전략 스튜디오")
    st.caption("어떤 전략이든 스펙으로 저장하고, AI 패치와 버전 관리로 발전시키는 범용 백테스트 공간입니다.")
    try:
        pack = agent_context.context_pack("lab", hours=int(st.session_state.get("agent_hours", 72)))
    except Exception:
        pack = {"ok": False, "strategy_studio": {"error": "컨텍스트를 불러오지 못했습니다.", "spec_count": 0, "version_count": 0, "latest": None}}
    try:
        catalog = cached.strategy_studio_catalog()
    except Exception:
        catalog = {"ok": False, "error": "전략 목록을 불러오지 못했습니다.", "catalog": {"count": 0, "specs": []}}
    strategy_studio.render_strategy_lab("research", pack, mode="research", catalog=catalog)


@st.fragment
def _backtest_section():
    st.subheader("전략 백테스트")
    st.caption("QQQ 3년 실제 데이터 (중첩 표본외)")
    last = cached.backtest_last()
    c_run, c_info = st.columns([1, 2.2], vertical_alignment="center")
    run = c_run.button("🔄 다시 실행 (최대 1분)" if last else "▶ 백테스트 실행 (최대 1분)",
                       key="bt_btn", type="primary", width="stretch")
    if run:
        cached.backtest.clear()                       # 인메모리 캐시 무시 — 진짜 재계산
        bt = cached.backtest()
        cached.backtest_last.clear()
        st.session_state["bt_done"] = True
    elif st.session_state.get("bt_done"):
        bt = cached.backtest()
    elif last:
        bt = last                                     # 💾 마지막 실행 결과 즉시 표시
        c_info.caption(f"💾 마지막 실행 {last.get('asof', '—')} — 최신화는 다시 실행")
    else:
        st.info("버튼을 눌러 백테스트를 실행하세요 — 무거운 계산이라 진입 시 자동 실행하지 않습니다. "
                "한 번 실행하면 결과가 저장돼 다음부터 즉시 표시됩니다.")
        return
    if bt.get("error"):
        st.warning(ui_actions.user_message(bt.get("error"), "백테스트를 끝내지 못했습니다. 다시 실행하세요."))
    else:
        a = st.columns(3)
        a[0].metric("모델 연환산", data.f_frac_pct(bt["ml"]["cagr"]))
        a[1].metric("모델 샤프", data.f_ratio(bt["ml"]["sharpe"], 2))
        a[2].metric("모델 낙폭", data.f_frac_pct(bt["ml"]["mdd"]))
        b = st.columns(3)
        b[0].metric("QQQ 연환산", data.f_frac_pct(bt["qqq"]["cagr"]))
        b[1].metric("QQQ 샤프", data.f_ratio(bt["qqq"]["sharpe"], 2))
        b[2].metric("QQQ 낙폭", data.f_frac_pct(bt["qqq"]["mdd"]))
        v = bt.get("verdict", "")
        (st.success if ("채택" in v and "비채택" not in v) else st.warning)(v)
        for r in bt.get("reasons", []):
            st.caption("· " + r)
        eq = bt.get("equity")
        if eq is not None:
            try:
                st.plotly_chart(charts.equity_curve(eq), width="stretch", config=_NOBAR)
            except Exception:
                pass
    st.caption("⚠️ 검증상 모델 종목선택·장중 타이밍은 우위가 없습니다 — 정보·표시용 (검증을 통과한 공격은 구조 레버뿐)")


@st.fragment
def _axes_gate_section():
    st.subheader("🚦 가격축 ★게이트")
    st.caption("주간 재검증(토) — 순비용 워크포워드 vs 지수 · 국내는 25년 시가총액(상폐 포함) · "
               "미국은 S&P500 시점 멤버십(상폐 가격이 없으면 커버리지를 낮춤)")
    g = cached.axes_gate()
    specs = (("kr", "🇰🇷 국내 — KOSPI 시총 상위 200 · 상위 5 월간 리밸 vs 시총가중"),
             ("us", "🇺🇸 미국 — S&P500 멤버십 · 상위 5 월간 리밸 vs QQQ"))
    for mk, title in specs:
        e = g.get(mk) or {}
        st.markdown(f"##### {title}")
        if not e.get("available"):
            st.info("검증 결과 없음 — 토요일 재검증 후 생성됩니다")
            continue
        v = e.get("verdict") or {}
        st.markdown(f"**{_gate_headline(v.get('label', ''))}**")
        oos, b = v.get("oos") or {}, v.get("bench") or {}
        m = st.columns(4)
        m[0].metric("검증 순초과/년", data.f_frac_pct_s(v.get("net_excess_cagr"), 2),
                    help="검증구간 연결 연환산 − 지수 연환산 (비용 차감)")
        m[1].metric("MDD 전략/지수",
                    f"{(oos.get('mdd') or 0)*100:.0f}%/{(b.get('mdd') or 0)*100:.0f}%",
                    help="★목적함수 2순위 제약: 전략 MDD ≤ 지수")
        m[2].metric("보정 샤프", data.f_ratio(v.get("dsr"), 3), help="관문 ≥0.95 — 다중검정 보정")
        m[3].metric("과적합 확률", data.f_ratio(v.get("pbo"), 3), help="관문 <0.5 — 과적합확률(CSCV)")
        if mk == "us" and e.get("coverage") is not None:
            st.caption(f"멤버십 가격 커버리지 {e['coverage']*100:.0f}% — 90% 미만이면 통과가 관찰로 내려갑니다")
        rec = e.get("recommendation")
        if rec:
            pw = rec.get("policy_weights") or {}
            w_str = " · ".join(
                f"{_axis_label(k[2:] if str(k).startswith('w_') else k)} {val:.2f}"
                for k, val in sorted(pw.items()) if val > 0)
            st.write(f"📌 현재 권고 축: **{_axis_label(rec.get('chosen'))}** → {w_str or '—'}")
        sh = e.get("shadow")
        if sh:
            st.caption(("✅ 가상 기록 **반영 중** (모의 선택 전용)" if sh.get("applied")
                        else "⏸️ 가상 기록은 있으나 설정이 꺼졌거나 오래되어 반영하지 않음") + f" · {sh.get('asof', '')}")
        else:
            st.caption("가상 기록 없음 — 축 평가 설정이 꺼져 있어 평가·표시만 합니다")
        ch = e.get("chosen_history") or {}
        if ch:
            st.caption("워크포워드 폴드 채택 이력: "
                       + " · ".join(f"{_axis_label(k)} ×{cnt}" for k, cnt in list(ch.items())[:6]))

        # 🛡️ 레짐 방어 오버레이 (KR — 수익 아님·낙폭 방어 추적)
        ro = e.get("regime_overlay") or {}
        if ro and not ro.get("error"):
            with st.expander(f"🛡️ 레짐 방어 오버레이 — {_GATE_CODE.get(str(ro.get('code') or ''), _axis_label(ro.get('code')))}"):
                st.caption("강세(지수>200MA)=고가모멘텀 · 약세=저변동 전환. **수익 엔진 아님 — 낙폭 방어용.**")
                ov, bn, of = ro.get("overlay") or {}, ro.get("bench") or {}, ro.get("offense_alone") or {}
                k = st.columns(4)
                k[0].metric("오버레이 MDD", data.f_frac_pct(ov.get("mdd")),
                            help="순공격(52주 고점 단독) 대비 낙폭")
                k[1].metric("순공격比 MDD", f"{ro.get('mdd_vs_offense_pp', 0):+.0f}%p",
                            help="음수 = 낙폭 개선")
                k[2].metric("약세해 방어", ro.get("bear_defend_years", "—"),
                            help="지수 하락 연도 중 방어 성공")
                k[3].metric("초과 DSR", data.f_ratio(ro.get("dsr"), 3),
                            help="관문 ≥0.95 — 미달이면 초과수익은 통계 무의미(위기 집중·휩소)")
                st.caption("👀 방어 기전은 확인(약세해 다수 방어)이나 초과수익 통계 미달 — "
                           "V자 반등에서 휩소 위험 · 추적 전용·자동집행 0")

        # 💸 비용·회전율 (확실한 실행 권고)
        cs = e.get("cost_sensitivity") or {}
        if cs and not cs.get("error"):
            with st.expander("💸 비용·회전율 최적화 — 확실한 실행 권고"):
                cur, best = cs.get("current") or {}, cs.get("best") or {}
                st.caption(f"축 {_axis_text(cs.get('axis'))} · 월간 리밸 회전율 비용이 순수익을 **연 {cur.get('drag_pp')}%p** 갉아먹음")
                st.dataframe(pd.DataFrame([{
                    "스킴": r["scheme"], "순CAGR": data.f_frac_pct_s(r["net_cagr"]),
                    "드래그%p": r["drag_pp"], "회전율": r["turnover"],
                    "순초과%p": r["net_excess_pp"], "MDD": data.f_frac_pct(r["mdd"]),
                } for r in cs.get("rows", [])], ), hide_index=True, width="stretch")
                oos = cs.get("oos") or {}
                if oos:
                    reco = oos.get("live_reco") or {}
                    vc = {"ROBUST": "✅", "MIXED": "🟡", "IN-SAMPLE": "⚠️"}.get(oos.get("verdict"), "")
                    verdict = {"ROBUST": "견고", "MIXED": "혼재", "IN-SAMPLE": "표본 안"}.get(
                        oos.get("verdict"), oos.get("verdict") or "—")
                    st.caption(f"**표본외 검증 {vc} {verdict}** — 반기가 월간을 이긴 연도 "
                               f"{int((oos.get('year_win_rate') or 0)*100)}% · 비용 전 수익 보존 {_yes_no(oos.get('gross_preserved'))}"
                               f"(월간 {(oos.get('gross_mo') or 0)*100:.1f}%/반기 {(oos.get('gross_semi') or 0)*100:.1f}%) · "
                               f"다른 축 확인 {_yes_no(oos.get('cross_axis_confirmed'))}")
                    if reco.get("min_hold_days"):
                        st.caption(f"→ 라이브 권고: **최소 보유 {reco['min_hold_days']}일** "
                                   f"(고정 주기 위상위험 회피·연속) · ~{reco['expected_drag_save_pp']}%p 절감 · "
                                   f"{reco.get('caveat', '')}")
                    else:
                        st.caption("→ 견고 미확인 — 현행 유지(과적합 회피)")
    st.caption("⚠️ 관찰 = 엣지 단정 불가(정직) · 반영은 모의 한정 · 실계좌 자동집행 0")


@st.fragment
def _learning_section():
    st.subheader("🧬 정책 학습 곡선")
    st.caption("모의 자기개선 — 주별 표본외 + 정직 판정 (순비용 기준 · 발전하면 보이고 안 되면 무엣지)")
    mk = st.radio("시장", ["kr_mock", "us_mock"], horizontal=True, key="evo_market",
                  format_func=lambda s: "국내 (KR)" if s == "kr_mock" else "미국 (US)")
    ev = cached.learning_evolution(mk)
    v, snap = ev.get("verdict") or {}, ev.get("snapshot") or {}
    if v:
        st.markdown(f"### {v.get('emoji', '')} {v.get('label', '')}")
        st.caption(v.get("note", ""))
    m = st.columns(4)
    m[0].metric("성숙 결정", int(snap.get("n", 0)))
    m[1].metric("순비용 IC", data.f_ratio(snap.get("realized_ic"), 3))
    m[2].metric("적중률", data.f_pct(snap.get("buy_hit")))
    m[3].metric("누적 엣지", data.f_frac_pct_s(snap.get("cum_net_excess")))
    series = ev.get("series") or []
    if len([s for s in series if s.get("excess") is not None]) >= 2:
        st.plotly_chart(charts.learning_curve(series), width="stretch", config=_NOBAR)
    else:
        st.info("학습 이력 축적 중 — 주간 재학습(토)마다 누적됩니다 (콜드스타트는 정상)")
    if ev.get("adoptions"):
        st.caption("채택 이력")
        st.dataframe(pd.DataFrame([{"주": a.get("date"), "OOS 초과": a.get("excess_challenger")}
                                   for a in ev["adoptions"]]), hide_index=True, width="stretch")
    st.caption("표시·모의 정책 · 실거래 미반영 · 무엣지면 정직 공개")
