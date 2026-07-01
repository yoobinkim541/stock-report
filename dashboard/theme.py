"""dashboard/theme.py — Terminal Noir 테마 (TradingView/토스증권 영감).

단일 진실원: 팔레트 상수 + **순수 HTML/SVG 컴포넌트 빌더**(테스트가능·streamlit 무관)
+ plotly 테마(순수) + `inject_global_css()`(streamlit lazy import).

`import dashboard.theme` 는 streamlit 을 끌어오지 않는다(charts.py 가 팔레트만 가져가도 순수 유지).
색은 .streamlit/config.toml 과 일치시킨다.
"""
from __future__ import annotations

import math

# ── 팔레트 (config.toml 과 동일) ─────────────────────────────────────────────
BG = "#0a0e17"
PANEL = "#131722"
PANEL2 = "#1a2030"
BORDER = "#222631"
GRID = "#1e222d"
TEXT = "#d1d4dc"
MUTED = "#9198a6"   # 캡션·라벨 가독 상향(기존 #787b86 은 너무 흐림)
GREEN = "#26a69a"
RED = "#ef5350"
BLUE = "#2962ff"
AMBER = "#f7a600"
VIOLET = "#9b5de5"

_MONO = "'JetBrains Mono', ui-monospace, monospace"


# ── plotly 테마 (순수·plotly lazy) ───────────────────────────────────────────
def apply_plotly_theme(fig):
    """모든 차트에 적용하는 TradingView 풍 다크 레이아웃."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_MONO, size=12, color=MUTED),
        colorway=[BLUE, GREEN, RED, AMBER, VIOLET, "#00bcd4", "#ff7043", "#66bb6a"],
        hovermode="x unified",
        hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER, font_family=_MONO, font_size=12),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED, size=11)),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER,
                     tickfont=dict(color=MUTED, family=_MONO), title_font=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER,
                     tickfont=dict(color=MUTED, family=_MONO), title_font=dict(color=MUTED))
    return fig


# ── 순수 HTML/SVG 빌더 ───────────────────────────────────────────────────────
def _num(x, dec=2):
    try:
        return f"{float(x):,.{dec}f}"
    except (TypeError, ValueError):
        return "—"


def ticker_hero_html(symbol, name="", price=None, change=None, change_pct=None,
                     asof="", currency="USD") -> str:
    """원형 심볼 뱃지 + 대형 등폭 가격 + 등락 pill (TradingView 히어로)."""
    up = (change_pct or 0) >= 0
    col = GREEN if up else RED
    arrow = "▲" if up else "▼"
    badge = (symbol or "?").replace(".KS", "")[:4]
    has = isinstance(price, (int, float))
    delta = (f"{arrow} {_num(abs(change))}  {_num(abs(change_pct))}%"
             if has and change is not None else "—")
    asof_html = f'<div class="tn-asof">{asof}</div>' if asof else ""
    return f'''<div class="tn-hero">
  <div class="tn-badge" style="color:{col};background:{col}1f;border-color:{col}66">{badge}</div>
  <div class="tn-hero-main">
    <div class="tn-hero-name">{name or symbol}</div>
    <div class="tn-hero-sym">{symbol}</div>
    <div class="tn-hero-row">
      <span class="tn-price">{_num(price)}</span>
      <span class="tn-cur">{currency}</span>
      <span class="tn-delta" style="color:{col};background:{col}1a">{delta}</span>
    </div>
    {asof_html}
  </div>
</div>'''


_GAUGE_ZONES = [
    ("#ef5350", "강력매도"), ("#ef9a9a", "매도"), ("#5d6673", "중립"),
    ("#80cbc4", "매수"), ("#26a69a", "강력매수"),
]


def rating_gauge_html(score, verdict="", sub="") -> str:
    """반원 속도계 게이지 (score∈[-1,1]: -1 강력매도 ↔ +1 강력매수). stroke-dasharray 5존."""
    try:
        score = max(-1.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        score = 0.0
    cx, cy, R = 110, 112, 90
    track = f"M {cx - R} {cy} A {R} {R} 0 0 1 {cx + R} {cy}"  # 하단? sweep=1 → 위로 (아래 검증)
    # 위 반원: sweep=0
    track = f"M {cx - R} {cy} A {R} {R} 0 0 0 {cx + R} {cy}"
    arcs = "".join(
        f'<path d="{track}" pathLength="100" fill="none" stroke="{c}" stroke-width="15" '
        f'stroke-dasharray="19.4 100" stroke-dashoffset="-{i * 20:.0f}" stroke-linecap="butt"/>'
        for i, (c, _) in enumerate(_GAUGE_ZONES))
    th = math.radians(180 - (score + 1) / 2 * 180)
    nx, ny = cx + (R - 14) * math.cos(th), cy - (R - 14) * math.sin(th)
    vcol = GREEN if score > 0.15 else RED if score < -0.15 else MUTED
    if not verdict:
        verdict = next(z[1] for i, z in enumerate(_GAUGE_ZONES)
                       if i == min(4, int((score + 1) / 2 * 5)))
    return f'''<div class="tn-gauge">
  <svg viewBox="0 0 220 150" width="100%" height="150">
    {arcs}
    <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="{TEXT}" stroke-width="3" stroke-linecap="round"/>
    <circle cx="{cx}" cy="{cy}" r="6" fill="{TEXT}"/>
    <text x="14" y="146" fill="{MUTED}" font-size="10" font-family="{_MONO}">강력매도</text>
    <text x="206" y="146" fill="{MUTED}" font-size="10" font-family="{_MONO}" text-anchor="end">강력매수</text>
  </svg>
  <div class="tn-gauge-verdict" style="color:{vcol}">{verdict}</div>
  {f'<div class="tn-gauge-sub">{sub}</div>' if sub else ''}
</div>'''


def sparkline_svg(values, w=124, h=30) -> str:
    """미니 스파크라인 (마지막≥처음 → 초록, 아니면 빨강)."""
    vals = [float(v) for v in (values or []) if isinstance(v, (int, float))]
    if len(vals) < 2:
        return f'<svg width="{w}" height="{h}"></svg>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n, pad = len(vals), 3
    pts = " ".join(
        f"{pad + i * (w - 2 * pad) / (n - 1):.1f},{h - pad - (v - lo) / rng * (h - 2 * pad):.1f}"
        for i, v in enumerate(vals))
    col = GREEN if vals[-1] >= vals[0] else RED
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" class="tn-spark">'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def watchlist_row_html(symbol, last=None, chg_pct=None, spark=None, name="") -> str:
    up = (chg_pct or 0) >= 0
    col = GREEN if up else RED
    chg = f"{'+' if up else ''}{_num(chg_pct)}%" if chg_pct is not None else "—"
    # 회사명은 title 툴팁으로(좁은 4열 그리드 유지 — 호버 시 노출)
    tip = f' title="{name}"' if name and name != symbol else ""
    # 사이드바(≈340px)에선 스파크라인 생략 → 3열(종목·값·등락%)로 등락%열 잘림 방지
    return f'''<div class="tn-wl-row">
  <span class="tn-wl-sym"{tip}>{symbol}</span>
  <span class="tn-wl-last">{_num(last)}</span>
  <span class="tn-wl-chg" style="color:{col}">{chg}</span>
</div>'''


def watchlist_html(rows: list[dict], title="워치리스트") -> str:
    body = "".join(watchlist_row_html(r.get("symbol", "?"), r.get("last"),
                                      r.get("chg_pct"), r.get("spark"), r.get("name", ""))
                   for r in rows)
    return f'<div class="tn-wl"><div class="tn-wl-head">{title}</div>{body}</div>'


def section_label_html(text, accent=BLUE) -> str:
    return f'<div class="tn-sec" style="border-color:{accent}"><span>{text}</span></div>'


# ── 전역 CSS 주입 (streamlit lazy) ───────────────────────────────────────────
def inject_global_css():
    import streamlit as st
    st.markdown(_CSS, unsafe_allow_html=True)


def render(html: str):
    """순수 빌더 HTML 을 화면에 렌더."""
    import streamlit as st
    st.markdown(html, unsafe_allow_html=True)


_CSS = f"""
<style>
/* ── 캔버스: 깊은 블루블랙 + 상단 글로우 (depth) ───────────────────────── */
.stApp {{
  background:
    radial-gradient(1100px 460px at 50% -8%, #16203a55 0%, transparent 60%),
    {BG};
}}
/* 상단 헤더 투명화 */
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.4rem; padding-bottom: 3rem; max-width: 1500px; }}

/* ── 타이포 ──────────────────────────────────────────────────────────── */
html, body, [class*="st-"], .stApp, p, span, div, label, input, button {{
  font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif;
}}
/* Streamlit 머티리얼 아이콘 폰트 복원 — 위 광역 span override 가 아이콘 ligature 를
   덮어 '_arrow_right_' 처럼 텍스트로 새는 것 방지 (expander 셰브런 등). */
[data-testid="stIconMaterial"], .material-symbols-rounded, .material-symbols-outlined,
[class*="material-symbols"], span[translate="no"] {{
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
}}
h1, h2, h3 {{ letter-spacing: -0.02em; font-weight: 700; }}
h1 {{ font-size: 1.7rem !important; }}
/* 숫자는 등폭 (터미널 정렬감) */
[data-testid="stMetricValue"], code, .tn-price, .tn-delta, .tn-wl-last,
.tn-wl-chg, .tn-gauge text {{
  font-family: {_MONO}; font-feature-settings: "tnum" 1; }}

/* ── 메트릭 = 보더 카드 ───────────────────────────────────────────────── */
[data-testid="stMetric"] {{
  background: {PANEL};
  border: 1px solid {BORDER};
  border-radius: 8px;
  padding: 15px 17px 13px;
  transition: border-color .18s ease, transform .18s ease;
}}
[data-testid="stMetric"]:hover {{ border-color: #2f3645; transform: translateY(-1px); }}
[data-testid="stMetricLabel"] p {{
  color: {MUTED} !important; font-size: .72rem !important;
  text-transform: uppercase; letter-spacing: .08em; font-weight: 600; }}
[data-testid="stMetricValue"] {{ font-size: 1.7rem !important; color: {TEXT}; }}

/* ── 탭: 언더라인 액센트 ──────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {BORDER}; }}
.stTabs [data-baseweb="tab"] {{
  color: {MUTED}; font-weight: 600; padding: 8px 14px;
  border-radius: 6px 6px 0 0; }}
.stTabs [aria-selected="true"] {{ color: {TEXT} !important; background: {PANEL}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: {BLUE} !important; height: 3px; }}

/* ── 데이터프레임 ────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 8px; }}

/* ── 사이드바 ────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {{ border-right: 1px solid {BORDER}; }}
[data-testid="stSidebar"] h3 {{ font-size: .95rem; color: {TEXT}; }}

/* ── expander / info / alert 톤 ──────────────────────────────────────── */
[data-testid="stExpander"] {{ border: 1px solid {BORDER}; border-radius: 8px; background: {PANEL}; }}

/* ── 스크롤바 ────────────────────────────────────────────────────────── */
::-webkit-scrollbar {{ width: 9px; height: 9px; }}
::-webkit-scrollbar-track {{ background: {BG}; }}
::-webkit-scrollbar-thumb {{ background: #2a2f3d; border-radius: 6px; }}
::-webkit-scrollbar-thumb:hover {{ background: #3a4252; }}

/* ── 컴포넌트: 티커 히어로 ───────────────────────────────────────────── */
.tn-hero {{ display: flex; align-items: center; gap: 18px; margin: 4px 0 20px; }}
.tn-badge {{
  width: 62px; height: 62px; border-radius: 50%; border: 1.5px solid;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 1.1rem; letter-spacing: -.02em; flex: 0 0 auto;
  font-family: {_MONO}; }}
.tn-hero-name {{ font-size: 1.5rem; font-weight: 800; color: {TEXT}; line-height: 1.1; }}
.tn-hero-sym {{ color: {MUTED}; font-size: .82rem; font-family: {_MONO}; margin-top: 1px; }}
.tn-hero-row {{ display: flex; align-items: baseline; gap: 10px; margin-top: 6px; flex-wrap: wrap; }}
.tn-price {{ font-size: 2.1rem; font-weight: 700; color: {TEXT}; line-height: 1; }}
.tn-cur {{ color: {MUTED}; font-size: .85rem; }}
.tn-delta {{ font-size: .92rem; font-weight: 600; padding: 3px 9px; border-radius: 6px; }}
.tn-asof {{ color: {MUTED}; font-size: .72rem; margin-top: 6px; }}

/* ── 컴포넌트: 게이지 ────────────────────────────────────────────────── */
.tn-gauge {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
  padding: 16px 14px 18px; text-align: center; }}
.tn-gauge-verdict {{ font-size: 1.25rem; font-weight: 800; margin-top: -6px; }}
.tn-gauge-sub {{ color: {MUTED}; font-size: .76rem; margin-top: 2px; }}

/* ── 컴포넌트: 워치리스트 ────────────────────────────────────────────── */
.tn-wl {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; overflow: hidden; }}
.tn-wl-head {{ padding: 9px 14px; font-size: .72rem; font-weight: 700; color: {MUTED};
  text-transform: uppercase; letter-spacing: .1em; border-bottom: 1px solid {BORDER}; }}
.tn-wl-row {{ display: grid; grid-template-columns: 1fr auto auto; align-items: center;
  gap: 10px; padding: 10px 14px; border-bottom: 1px solid {BORDER}; transition: background .15s; }}
.tn-wl-row:hover {{ background: {PANEL2}; }}
.tn-wl-row:last-child {{ border-bottom: none; }}
.tn-wl-sym {{ font-weight: 700; color: {TEXT}; font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.tn-wl-last {{ font-family: {_MONO}; color: {TEXT}; font-size: .82rem; white-space: nowrap; }}
.tn-wl-chg {{ font-family: {_MONO}; font-size: .82rem; font-weight: 600; text-align: right; white-space: nowrap; }}
.tn-spark {{ display: block; }}

/* ── 컴포넌트: 섹션 라벨 ─────────────────────────────────────────────── */
.tn-sec {{ border-left: 3px solid {BLUE}; padding-left: 10px; margin: 18px 0 10px; }}
.tn-sec span {{ font-size: .78rem; font-weight: 700; color: {MUTED};
  text-transform: uppercase; letter-spacing: .12em; }}

/* ── segmented_control / pills: 선택 세그먼트 강조 (활성 신호 뚜렷하게) ──── */
[data-testid="stButtonGroup"] button[aria-pressed="true"],
[data-testid="stButtonGroup"] button[aria-checked="true"] {{
  background: {BLUE}26 !important; border-color: {BLUE} !important; color: {TEXT} !important; }}

/* ── 캡션 가독 (흐린 회색 상향) ──────────────────────────────────────── */
[data-testid="stCaptionContainer"] {{ color: {MUTED} !important; }}

/* ── 모바일 반응형 (≤600px) — 커스텀 컴포넌트 축소·재배치 ─────────────── */
@media (max-width: 600px) {{
  .block-container {{ padding-top: 1.4rem; padding-left: .8rem; padding-right: .8rem; }}
  h1 {{ font-size: 1.4rem !important; }}
  .tn-hero {{ gap: 12px; margin: 2px 0 14px; }}
  .tn-badge {{ width: 46px; height: 46px; font-size: .95rem; }}
  .tn-hero-name {{ font-size: 1.2rem; }}
  .tn-price {{ font-size: 1.6rem; }}
  .tn-gauge {{ padding: 12px 10px 14px; }}
  /* 워치리스트: 스파크라인 숨기고 3열로 (좁은 폭 판독) */
  .tn-wl-row {{ grid-template-columns: 1fr auto auto; gap: 8px; }}
  .tn-wl-spark {{ display: none; }}
}}
</style>
"""
