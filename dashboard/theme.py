"""dashboard/theme.py — Terminal Noir 테마 (TradingView/토스증권 영감).

단일 진실원: 팔레트 상수 + **순수 HTML/SVG 컴포넌트 빌더**(테스트가능·streamlit 무관)
+ plotly 테마(순수) + `inject_global_css()`(streamlit lazy import).

`import dashboard.theme` 는 streamlit 을 끌어오지 않는다(charts.py 가 팔레트만 가져가도 순수 유지).
색은 .streamlit/config.toml 과 일치시킨다.
"""
from __future__ import annotations

import math
from urllib.parse import quote as _urlquote

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


def _polar(cx, cy, r, deg):
    """각도(도, 수학표준: 0°=우·90°=위·180°=좌) → SVG 좌표(y 아래로 증가)."""
    a = math.radians(deg)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def _arc(cx, cy, r, a0, a1):
    """a0→a1 원호 path (도). 각도 감소(a1<a0)=화면상 시계방향=위 반원 좌→우."""
    x0, y0 = _polar(cx, cy, r, a0)
    x1, y1 = _polar(cx, cy, r, a1)
    large = 1 if abs(a0 - a1) > 180 else 0
    sweep = 1 if a1 < a0 else 0
    return f"M {x0:.1f} {y0:.1f} A {r} {r} 0 {large} {sweep} {x1:.1f} {y1:.1f}"


def rating_gauge_html(score, verdict="", sub="", *, title="",
                      end_labels=("약세", "강세"), zones=None,
                      boxed: bool = True) -> str:
    """반원 속도계 게이지 (score∈[-1,1]: -1 강력매도 ↔ +1 강력매수).

    5존을 **상단 반원**(좌 약세→우 강세)에 개별 원호로 타일 + 니들(score→각도) + 허브.
    zones/end_labels 로 라벨 체계 교체 가능(가치평가 게이지 공용) — 기본 동작 불변.
    """
    zones = zones or _GAUGE_ZONES
    try:
        score = max(-1.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        score = 0.0
    cx, cy, R, sw = 100, 100, 78, 15
    n = len(zones)
    seg = 180.0 / n                                  # 존당 36°
    arcs = "".join(
        f'<path d="{_arc(cx, cy, R, 180 - i * seg, 180 - (i + 1) * seg)}" fill="none" '
        f'stroke="{c}" stroke-width="{sw}" stroke-linecap="butt"/>'
        for i, (c, _) in enumerate(zones))
    a = 90.0 * (1 - score)                            # score -1→180°, 0→90°, +1→0°
    nx, ny = _polar(cx, cy, R - 20, a)
    vcol = GREEN if score > 0.15 else RED if score < -0.15 else MUTED
    if not verdict:
        verdict = zones[min(n - 1, int((score + 1) / 2 * n))][1]
    ly = cy + 16
    head = (f'<div style="color:{MUTED};font-size:0.78rem;text-align:center;'
            f'margin-bottom:-4px">{title}</div>' if title else '')
    box = ("tn-gauge" if boxed else "")
    return f'''<div class="{box}" style="max-width:220px;margin:0 auto;text-align:center">
  {head}
  <svg viewBox="0 0 200 126" width="100%" preserveAspectRatio="xMidYMid meet">
    {arcs}
    <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="{TEXT}" stroke-width="3" stroke-linecap="round"/>
    <circle cx="{cx}" cy="{cy}" r="6" fill="{TEXT}"/>
    <text x="{cx - R}" y="{ly}" fill="{MUTED}" font-size="10" font-family="{_MONO}" text-anchor="middle">{end_labels[0]}</text>
    <text x="{cx + R}" y="{ly}" fill="{MUTED}" font-size="10" font-family="{_MONO}" text-anchor="middle">{end_labels[1]}</text>
  </svg>
  <div class="tn-gauge-verdict" style="color:{vcol}">{verdict}</div>
  {f'<div class="tn-gauge-sub">{sub}</div>' if sub else ''}
</div>'''


_VAL_ZONES = [
    ("#ef5350", "크게 고평가"), ("#ef9a9a", "고평가"), ("#5d6673", "적정 수준"),
    ("#80cbc4", "저평가"), ("#26a69a", "크게 저평가"),
]


def valuation_gauge_html(score, sub="") -> str:
    """가치평가 게이지 (score∈[-1,1]: -1 크게 고평가 ↔ +1 크게 저평가) — 표시·참고용."""
    return rating_gauge_html(score, sub=sub, title="⚖️ 가치평가",
                             end_labels=("고평가", "저평가"), zones=_VAL_ZONES)


def position_band_html(cells) -> str:
    """내 포지션 컴팩트 밴드 — [(label, value, color|None)] 한 줄 스트립 (st.metric 대체)."""
    if not cells:
        return ""
    items = "".join(
        f'<div style="flex:1;min-width:104px;text-align:center;padding:2px 6px">'
        f'<span style="color:{MUTED};font-size:0.72rem">{lab}</span><br>'
        f'<b style="font-size:1.02rem;color:{col or TEXT};font-family:{_MONO}">{val}</b></div>'
        for lab, val, col in cells)
    return (f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
            f'padding:7px 10px;background:{PANEL};border:1px solid {BORDER};'
            f'border-radius:10px">{items}</div>')


_FNG_ZONES = [(25, "#ef5350", "극공포"), (45, "#ff9800", "공포"),
              (55, MUTED, "중립"), (75, "#66bb6a", "탐욕"), (101, GREEN, "극탐욕")]


def fng_label(score):
    """공포·탐욕 점수(0~100) → (색, 한글 라벨)."""
    for hi, col, lab in _FNG_ZONES:
        if score < hi:
            return col, lab
    return _FNG_ZONES[-1][1], _FNG_ZONES[-1][2]


def _gauge_svg(value, vmin, vmax, zones, big=None, big_col=None, sub=None) -> str:
    """반원 게이지 SVG — zones=[(상한, 색)] 오름차순(마지막 상한=vmax). value→니들 각도.

    중앙 big 텍스트(기본 value)·하단 sub. `_arc`/`_polar`(rating_gauge 공용·상단 반원) 재사용.
    """
    span = (vmax - vmin) or 1.0

    def ang(v):
        return 180.0 - (max(vmin, min(vmax, v)) - vmin) / span * 180.0

    cx, cy, r = 100, 100, 78
    prev = vmin
    parts = []
    for hi, col in zones:
        parts.append(f'<path d="{_arc(cx, cy, r, ang(prev), ang(hi))}" fill="none" '
                     f'stroke="{col}" stroke-width="13" stroke-linecap="butt"/>')
        prev = hi
    ok = isinstance(value, (int, float))
    if ok:
        nx, ny = _polar(cx, cy, r - 18, ang(float(value)))
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
                     f'stroke="{TEXT}" stroke-width="3" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{TEXT}"/>')
    bigtxt = big if big is not None else (f"{value:.0f}" if ok else "—")
    parts.append(f'<text x="{cx}" y="80" text-anchor="middle" fill="{big_col or TEXT}" '
                 f'font-size="27" font-weight="700" font-family="{_MONO}">{bigtxt}</text>')
    if sub:
        parts.append(f'<text x="{cx}" y="118" text-anchor="middle" fill="{MUTED}" '
                     f'font-size="11" font-family="{_MONO}">{sub}</text>')
    return (f'<svg viewBox="0 0 200 126" width="100%" preserveAspectRatio="xMidYMid meet">'
            f'{"".join(parts)}</svg>')


_FNG_GAUGE = [(25, "#ef5350"), (45, "#ff9800"), (55, MUTED), (75, "#66bb6a"), (100, GREEN)]


def fng_gauge_html(score, prev_week=None) -> str:
    """공포·탐욕 지수 반원 게이지 (CNN 풍) — 점수 니들 + 등급 + 전주 추세."""
    try:
        s = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return ""
    col, lab = fng_label(s)
    trend = f' · 전주 {prev_week:.0f} {"▲" if s >= prev_week else "▼"}' if isinstance(prev_week, (int, float)) else ""
    return f'''<div style="padding:10px 12px;background:{PANEL};border:1px solid {BORDER};border-radius:10px;min-height:340px;display:flex;flex-direction:column;">
  <div style="color:{MUTED};font-size:0.82rem;text-align:center">😱 공포·탐욕 지수</div>
  <div style="flex:1;display:flex;flex-direction:column;justify-content:center">
    <div style="max-width:230px;margin:0 auto;width:100%">{_gauge_svg(s, 0, 100, _FNG_GAUGE, big=f"{s:.0f}", big_col=col, sub=lab)}</div>
  </div>
  <div style="color:{MUTED};font-size:0.72rem;text-align:center">극공포 0 · 100 극탐욕{trend}</div>
</div>'''


def _rsi_color(v):
    if not isinstance(v, (int, float)):
        return MUTED
    return RED if v >= 70 else GREEN if v <= 30 else MUTED


def _rsi_zone(v):
    if not isinstance(v, (int, float)):
        return "—"
    return "과매수" if v >= 70 else "과매도" if v <= 30 else "중립"


_RSI_GAUGE = [(30, GREEN), (70, MUTED), (100, RED)]   # 과매도 녹·중립 회·과매수 적


def index_rsi_gauges_html(name, price=None, chg=None, rsi_d=None, rsi_w=None) -> str:
    """지수 카드 — 현재가·당일등락 + 일봉/주봉 RSI 반원 게이지 2개(과매수 적·과매도 녹)."""
    ccol = GREEN if (chg or 0) >= 0 else RED
    pxs = f"{price:,.0f}" if isinstance(price, (int, float)) else "—"
    chgs = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else ""

    def g(lbl, v):
        c = _rsi_color(v)
        big = f"{v:.0f}" if isinstance(v, (int, float)) else "—"
        val = v if isinstance(v, (int, float)) else None
        return (f'<div style="flex:1;text-align:center"><div style="color:{MUTED};font-size:0.72rem">{lbl}</div>'
                f'{_gauge_svg(val, 0, 100, _RSI_GAUGE, big=big, big_col=c, sub=_rsi_zone(v))}</div>')

    return f'''<div style="padding:10px 12px;background:{PANEL};border:1px solid {BORDER};border-radius:10px;min-height:340px;display:flex;flex-direction:column;">
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <b style="color:{TEXT}">{name}</b>
    <span style="font-family:{_MONO};color:{MUTED};font-size:0.82rem">{pxs} <span style="color:{ccol}">{chgs}</span></span></div>
  <div style="flex:1;display:flex;align-items:center">
    <div style="display:flex;gap:6px;width:100%">{g("일봉 RSI", rsi_d)}{g("주봉 RSI", rsi_w)}</div>
  </div>
</div>'''


def macro_card_html(item: dict, link: bool = True) -> str:
    """매크로 자산 카드 1개 — 이모지·라벨 / 가격·단위 / 등락 / 30일 스파크라인 (순수).

    item: {emoji,label,price,chg,pct,unit,spark,ticker}. 상승 초록·하락 빨강(프로젝트 시맨틱).
    link=True 면 카드 전체가 `?tk=<티커>` 앵커 — app.py 가 쿼리파라미터를 소비해 종목분석
    으로 이동(순수 HTML 은 콜백이 없어 앵커가 유일한 클릭 경로 · 별도 버튼 행 불필요).
    """
    pct = item.get("pct")
    chg = item.get("chg")
    up = (pct or 0) >= 0
    col = GREEN if up else RED
    price = item.get("price")
    unit = item.get("unit") or ""
    # 단위가 접두(₩·$)인지 접미(%·$/oz)인지 — 통화기호 단독이면 접두
    pstr = f"{price:,}" if isinstance(price, (int, float)) else "—"
    if unit in ("₩", "$"):
        pstr = f"{unit}{pstr}"
        suffix = ""
    else:
        suffix = f' <span style="color:{MUTED};font-size:.68rem">{unit}</span>' if unit else ""
    chg_s = f"{'+' if up else ''}{chg:,}" if isinstance(chg, (int, float)) else "—"
    pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
    body = (
        f'<div style="color:{MUTED};font-size:.74rem;white-space:nowrap;overflow:hidden;'
        f'text-overflow:ellipsis">{item.get("emoji", "")} {item.get("label", "")}</div>'
        f'<div style="font-family:{_MONO};color:{TEXT};font-size:1.02rem;font-weight:600">'
        f'{pstr}{suffix}</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:4px">'
        f'<span style="font-family:{_MONO};color:{col};font-size:.72rem;white-space:nowrap">'
        f'{"▲" if up else "▼"}{chg_s} ({pct_s})</span>'
        f'{sparkline_svg(item.get("spark"), w=58, h=20)}</div>')
    tk = item.get("ticker")
    if link and tk:
        # target=_self — 새 탭 대신 현재 탭 네비게이션(앱 rerun). 티커는 URL 인코딩(`^`·`=`).
        href = "?tk=" + _urlquote(str(tk), safe="")
        return (f'<a class="tn-macro-card" href="{href}" target="_self" '
                f'title="{item.get("label", "")} 상세 차트·분석으로 이동">{body}</a>')
    return f'<div class="tn-macro-card">{body}</div>'


def macro_cards_html(items: list[dict], cols: int = 4, link: bool = True) -> str:
    """매크로 자산 카드 그리드 — 반응형(≤600px 2열)·카드 클릭 시 종목분석. 항목 없으면 ''."""
    if not items:
        return ""
    cards = "".join(macro_card_html(it, link=link) for it in items)
    return f'''<style>
.tn-macro {{ display:grid; grid-template-columns:repeat({cols}, minmax(0, 1fr)); gap:8px; }}
@media (max-width: 600px) {{ .tn-macro {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} }}
.tn-macro .tn-macro-card {{ padding:9px 11px; background:{PANEL}; border:1px solid {BORDER};
  border-radius:10px; display:flex; flex-direction:column; gap:2px;
  text-decoration:none !important; color:inherit; transition:border-color .12s, transform .12s; }}
a.tn-macro-card:hover {{ border-color:{BLUE}; transform:translateY(-1px); }}
</style><div class="tn-macro">{cards}</div>'''


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


def _money_compact(v, cur: str) -> str:
    """모의 레일용 압축 금액 — ₩는 억/만, $는 천단위 콤마. None → —."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if cur == "₩":
        return f"₩{v / 1e8:.2f}억" if abs(v) >= 1e8 else f"₩{v / 1e4:,.0f}만"
    return f"${v:,.0f}"


def paper_rail_html(rows: list[dict], title="🧪 모의투자") -> str:
    """사이드바 모의 계좌 레일 (워치리스트와 동일 tn-wl 스타일 — 순수 빌더).

    rows: views.paper_glance 출력 [{label, currency, nav, cum_ret, day_ret, n_days}].
    3열: 계좌 · NAV(압축) · 누적%(색). 전일·기록일수는 title 툴팁.
    """
    body = []
    for r in rows or []:
        cum = r.get("cum_ret")
        up = (cum or 0) >= 0
        col = GREEN if up else RED
        chg = f"{'+' if up else ''}{_num(cum)}%" if cum is not None else "—"
        day = r.get("day_ret")
        tip = (f' title="전일 {day:+.2f}% · 기록 {r.get("n_days", 0)}일"'
               if day is not None else "")
        body.append(f'''<div class="tn-wl-row">
  <span class="tn-wl-sym"{tip}>{r.get("label", "?")}</span>
  <span class="tn-wl-last">{_money_compact(r.get("nav"), r.get("currency", ""))}</span>
  <span class="tn-wl-chg" style="color:{col}">{chg}</span>
</div>''')
    return f'<div class="tn-wl"><div class="tn-wl-head">{title}</div>{"".join(body)}</div>'


def section_label_html(text, accent=BLUE) -> str:
    return f'<div class="tn-sec" style="border-color:{accent}"><span>{text}</span></div>'


# ── 경제 일정 달력 (월간 그리드) ─────────────────────────────────────────────

_ECAL_IMP_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_ECAL_MAX_CHIPS = 4     # 셀당 표시 이벤트 수 (초과분 +N)
_ECAL_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def econ_calendar_html(events: list, start=None, weeks: int = 3) -> str:
    """경제 일정 → 달력 그리드 HTML (순수 빌더 — 테스트 가능).

    events: econ_calendar.upcoming_events 형식 [{when: datetime|None, title, marker, importance}].
    start 가 속한 주 월요일부터 weeks 주. 오늘 = 액센트 테두리·주말 흐림·지난날 반투명.
    when 없는 이벤트는 달력에 못 놓으므로 제외(목록 expander 가 보완).
    """
    import html as _h
    from datetime import date as _date, timedelta as _td

    today = start or _date.today()
    if hasattr(today, "date"):
        today = today.date()
    monday = today - _td(days=today.weekday())

    by_day: dict = {}
    for ev in events or []:
        w = ev.get("when")
        if w is None:
            continue
        d = w.date() if hasattr(w, "date") else w
        by_day.setdefault(d, []).append(ev)

    cells = []
    for i in range(max(1, weeks) * 7):
        d = monday + _td(days=i)
        evs = sorted(by_day.get(d, []),
                     key=lambda e: (_ECAL_IMP_ORDER.get(e.get("importance"), 9),
                                    str(e.get("when") or "")))
        chips = []
        for ev in evs[:_ECAL_MAX_CHIPS]:
            t = _h.escape(str(ev.get("title") or ""))
            hhmm = ""
            try:
                hhmm = ev["when"].strftime("%H:%M") + " "
            except Exception:
                pass
            chips.append(f'<div class="ec-ev" title="{hhmm}{t}">'
                         f'{ev.get("marker", "⚪")} {t}</div>')
        if len(evs) > _ECAL_MAX_CHIPS:
            chips.append(f'<div class="ec-more">+{len(evs) - _ECAL_MAX_CHIPS}건 더</div>')
        klass = "ec-cell"
        if d == today:
            klass += " ec-today"
        if d.weekday() >= 5:
            klass += " ec-dim"
        if d < today:
            klass += " ec-past"
        badge = f'<span class="ec-mon">{d.month}/</span>' if (d.day == 1 or i == 0) else ""
        cells.append(f'<div class="{klass}"><div class="ec-date">{badge}{d.day}</div>'
                     f'{"".join(chips)}</div>')

    heads = "".join(f'<div class="ec-head">{h}</div>' for h in _ECAL_WEEKDAYS)
    style = (
        "<style>"
        ".ec-cal{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin:4px 0 8px}"
        f".ec-head{{color:{MUTED};font-size:.72rem;text-align:center;padding:2px 0}}"
        f".ec-cell{{background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        "min-height:78px;padding:4px 6px;overflow:hidden}"
        f".ec-today{{border-color:{BLUE};box-shadow:inset 0 0 0 1px {BLUE}}}"
        f".ec-dim .ec-date{{color:{MUTED}}}"
        ".ec-past{opacity:.55}"
        f".ec-date{{font-family:{_MONO};font-size:.72rem;color:{TEXT};margin-bottom:2px}}"
        f".ec-mon{{color:{AMBER};font-weight:700}}"
        f".ec-ev{{font-size:.68rem;color:{TEXT};white-space:nowrap;overflow:hidden;"
        "text-overflow:ellipsis;line-height:1.55;cursor:default}"
        f".ec-more{{font-size:.62rem;color:{MUTED}}}"
        "@media (max-width:600px){.ec-cell{min-height:56px;padding:3px 4px}"
        ".ec-ev{font-size:.6rem}}"
        "</style>")
    return style + f'<div class="ec-cal">{heads}{"".join(cells)}</div>'


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

/* ── 렌더링 진행감 — 스켈레톤 shimmer + stale 요소 숨쉬기 (멈춘 화면 느낌 제거) ── */
[data-testid="stSkeleton"] {{
  background: linear-gradient(90deg, #131722 25%, #1e2536 38%, #131722 55%) !important;
  background-size: 400% 100% !important;
  animation: tn-shimmer 1.3s ease-in-out infinite;
  border-radius: 10px;
}}
@keyframes tn-shimmer {{
  0% {{ background-position: 100% 50%; }}
  100% {{ background-position: 0% 50%; }}
}}
/* rerun 중 이전 화면(stale) — 부드러운 숨쉬기 펄스로 '처리 중' 신호 */
[data-stale="true"] {{
  animation: tn-breathe 1.5s ease-in-out infinite !important;
}}
@keyframes tn-breathe {{
  0%, 100% {{ opacity: 0.45; }}
  50% {{ opacity: 0.8; }}
}}
.stSpinner > div {{ border-top-color: #2962ff !important; }}
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
  /* 신규 카드·스트립 — 모바일 축소 (인라인 스타일은 !important 로 우선) */
  div[style*="min-height:340px"] {{ min-height: 240px !important; }}
  div[style*="min-width:150px"] {{ min-width: 46% !important; }}
  div[style*="min-width:104px"] {{ min-width: 44% !important; }}
  .tn-tape {{ font-size: 0.7rem; }}

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


def orderbook_ladder_html(bids, asks, *, prev_close=None, price=None,
                          day=None, week52=None, depth: int = 10) -> str:
    """실시간 호가 사다리 (KR HTS 풍·순수 HTML) — theme.render 로 출력.

    상단 = 매도호가(최우선이 아래·잔량 바 좌측 파랑), 하단 = 매수호가(잔량 바 우측 빨강).
    등락% = 전일 종가 기준 (KR 관례: 상승 빨강·하락 파랑). 우측 패널 = 당일 시작/최고/최저·
    거래량·52주 고저. 하단 = 매도/매수 총잔량 비율 바. bids/asks: [[price, qty], ...] 최우선 우선.
    """
    bids = [(float(p), float(q)) for p, q in (bids or [])[:depth] if p]
    asks = [(float(p), float(q)) for p, q in (asks or [])[:depth] if p]
    if not bids and not asks:
        return "<div style='color:#9198a6'>호가 없음</div>"
    max_q = max([q for _, q in bids + asks] or [1.0]) or 1.0

    def _pct(px):
        if not prev_close:
            return ""
        v = (px / prev_close - 1) * 100
        color = "#f04452" if v > 0 else ("#3182f6" if v < 0 else MUTED)
        return f"<span style='color:{color};font-size:11px'> {v:+.2f}%</span>"

    def _row(px, q, side):
        w = max(2, int(q / max_q * 100))
        bar_color = "#3182f6" if side == "ask" else "#f04452"
        qty = f"{q:,.0f}"
        px_cell = (f"<td style='width:38%;text-align:center;padding:1px 4px;"
                   f"font-family:{_MONO};font-size:12px'>{px:,.0f}{_pct(px)}</td>")
        # 바 = 배경 absolute 레이어, 텍스트 = 전폭 오버레이 → 바가 좁아도 숫자는 절대 안 꺾임
        anchor = "right" if side == "ask" else "left"
        border = "left" if side == "ask" else "right"
        cell = (f"<td style='width:31%'><div style='position:relative;min-height:16px'>"
                f"<div style='position:absolute;top:1px;bottom:1px;{anchor}:0;width:{w}%;"
                f"background:{bar_color}26;border-{border}:2px solid {bar_color}'></div>"
                f"<div style='position:relative;text-align:{anchor};padding:1px 6px;"
                f"font-family:{_MONO};font-size:10.5px;color:{bar_color};"
                f"white-space:nowrap'>{qty}</div></div></td>")
        if side == "ask":
            return f"<tr>{cell}{px_cell}<td style='width:31%'></td></tr>"
        return f"<tr><td style='width:31%'></td>{px_cell}{cell}</tr>"

    rows = [_row(p, q, "ask") for p, q in sorted(asks, key=lambda x: -x[0])]
    if price:
        rows.append(f"<tr><td></td><td style='text-align:center;border:1px solid #e5e8ee55;"
                    f"border-radius:6px;padding:2px;font-family:{_MONO};font-weight:700;"
                    f"font-size:13px'>{float(price):,.0f}{_pct(float(price))}</td><td></td></tr>")
    rows += [_row(p, q, "bid") for p, q in bids]

    stat_rows = []
    def _stat(k, v, color=None):
        vv = f"{v:,.0f}" if isinstance(v, (int, float)) else (v or "—")
        c = f"color:{color}" if color else ""
        stat_rows.append(f"<div style='display:flex;justify-content:space-between;"
                         f"font-size:12px;padding:2px 0'><span style='color:{MUTED}'>{k}</span>"
                         f"<span style='font-family:{_MONO};{c}'>{vv}</span></div>")
    if week52:
        _stat("52주 최고", week52.get("high")); _stat("52주 최저", week52.get("low"))
    if day:
        _stat("시작", day.get("open"))
        _stat("최고", day.get("high"), "#f04452"); _stat("최저", day.get("low"), "#3182f6")
        if day.get("volume"):
            _stat("거래량", day["volume"])
    stats = (f"<div style='min-width:170px;border-left:1px solid {GRID};padding-left:12px'>"
             + "".join(stat_rows) + "</div>") if stat_rows else ""

    tb, ta = sum(q for _, q in bids), sum(q for _, q in asks)
    tot = (tb + ta) or 1.0
    totals = (f"<div style='display:flex;align-items:center;gap:8px;margin-top:6px;font-size:12px'>"
              f"<span style='color:#3182f6'>판매대기 {ta:,.0f}</span>"
              f"<div style='flex:1;height:6px;border-radius:3px;overflow:hidden;display:flex'>"
              f"<div style='width:{ta / tot * 100:.1f}%;background:#3182f6'></div>"
              f"<div style='width:{tb / tot * 100:.1f}%;background:#f04452'></div></div>"
              f"<span style='color:#f04452'>{tb:,.0f} 구매대기</span></div>")

    return (f"<div style='display:flex;gap:14px'>"
            f"<div style='flex:1'><div style='max-height:340px;overflow-y:auto'>"
            f"<table style='width:100%;border-collapse:collapse'>"
            + "".join(rows) + "</table></div>" + totals + "</div>" + stats + "</div>")


def market_tape_html(items: list[dict]) -> str:
    """하단 고정 시장 마퀴 띠 — CSS 무한 스크롤(내용 2벌·hover 정지). 항목 없으면 ''.

    items: [{label, value, chg, pct}] — 상승 초록/하락 빨강(프로젝트 시맨틱).
    본문 가림 방지 padding-bottom 주입 포함. 순수 HTML(theme.render 로 출력).
    """
    if not items:
        return ""
    spans = []
    for it in items:
        pct = it.get("pct")
        chg = it.get("chg")
        color = GREEN if (pct or 0) >= 0 else RED
        arrow = "▲" if (pct or 0) >= 0 else "▼"
        spans.append(
            f"<span style='margin:0 18px;white-space:nowrap'>"
            f"<b style='color:#d1d4dc'>{it['label']}</b> "
            f"<span style='font-family:{_MONO}'>{it['value']:,}</span> "
            f"<span style='color:{color};font-family:{_MONO};font-size:11px'>"
            f"{arrow}{abs(chg):,} ({pct:+.2f}%)</span></span>")
    seq = "".join(spans)
    return f"""
<style>
@keyframes tn-tape-scroll {{ 0% {{ transform: translateX(0); }} 100% {{ transform: translateX(-50%); }} }}
.tn-tape {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 999;
  background: {PANEL}; border-top: 1px solid {GRID}; height: 30px;
  overflow: hidden; display: flex; align-items: center; font-size: 12.5px; }}
.tn-tape-inner {{ display: inline-flex; white-space: nowrap;
  animation: tn-tape-scroll 60s linear infinite; }}
.tn-tape:hover .tn-tape-inner {{ animation-play-state: paused; }}
.stMainBlockContainer {{ padding-bottom: 56px !important; }}
</style>
<div class="tn-tape"><div class="tn-tape-inner">{seq}{seq}</div></div>
"""


# ── ETF 점수 게이지 (동종그룹 1~100 — 표시·참고용) ─────────────────────────────
_ETF_SCORE_ZONES = [(20, RED), (40, "#f97316"), (60, MUTED), (80, "#86c26a"), (100, GREEN)]


def etf_score_label(s):
    if s >= 80:
        return "그룹 최상위"
    if s >= 60:
        return "그룹 상위"
    if s >= 40:
        return "그룹 중위"
    if s >= 20:
        return "그룹 하위"
    return "그룹 최하위"


def etf_score_html(score, group_name: str = "", low_confidence: bool = False) -> str:
    """ETF 동종그룹 점수(1~100) 반원 게이지 — score None → 데이터 부족 안내."""
    if score is None:
        return (f'<div style="padding:10px 12px;background:{PANEL};border:1px solid {BORDER};'
                f'border-radius:10px;text-align:center;color:{MUTED}">'
                f'점수 — <b>데이터 부족</b><br><span style="font-size:0.75rem">'
                f'이력·지표가 모자라 산출 생략 (정직 표시)</span></div>')
    s = max(1.0, min(100.0, float(score)))
    lab = etf_score_label(s)
    conf = (f' · <span style="color:{RED}">표본 부족</span>' if low_confidence else "")
    return f'''<div style="padding:8px 12px;background:{PANEL};border:1px solid {BORDER};border-radius:10px">
  <div style="color:{MUTED};font-size:0.82rem;text-align:center">🏆 ETF 점수 — {group_name or "동종그룹"}</div>
  <div style="max-width:210px;margin:0 auto">{_gauge_svg(s, 0, 100, _ETF_SCORE_ZONES, big=f"{s:.0f}", sub=lab)}</div>
  <div style="color:{MUTED};font-size:0.72rem;text-align:center;margin-top:-2px">동종그룹 백분위 가중합 1~100 · 표시·참고용{conf}</div>
</div>'''


# ── 기업 판단 요약 카드 (순수) ─────────────────────────────────────────────────
_VERDICT_STYLE = {"양호": (GREEN, "🟢"), "선별 관찰": (AMBER, "🟡"),
                  "주의 우선": (RED, "🔴"), "데이터 확인 필요": (MUTED, "⚪")}


def analysis_card_html(verdict: str, positives: list, risks: list,
                       checks: list | None = None) -> str:
    """기업 판단 요약 카드 — verdict 색 액센트 + 강점 ✓/주의 ⚠ 칩 리스트 + 다음 확인 풋터.

    표시·참고용(매매신호 아님). 순수 HTML — 반응형(flex-wrap·컬럼 min-width).
    """
    col, icon = _VERDICT_STYLE.get(verdict, (MUTED, "⚪"))

    def _items(items, mark, mcol, empty):
        if not items:
            return f'<div style="color:{MUTED};font-size:0.82rem;padding:2px 0">{empty}</div>'
        return "".join(
            f'<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0">'
            f'<span style="color:{mcol};font-size:0.8rem">{mark}</span>'
            f'<span style="font-size:0.9rem;color:{TEXT}">{x}</span></div>'
            for x in items)

    chips = "".join(
        f'<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 10px;'
        f'border:1px solid {BORDER};border-radius:999px;color:{MUTED};'
        f'font-size:0.74rem;background:{PANEL2}">☑ {c}</span>'
        for c in (checks or []))
    footer = (f'<div style="margin-top:10px;padding-top:9px;border-top:1px solid {BORDER}">'
              f'<span style="color:{MUTED};font-size:0.72rem;margin-right:6px">다음 확인</span>'
              f'{chips}</div>' if chips else "")
    return f'''<div style="background:{PANEL};border:1px solid {BORDER};border-left:4px solid {col};
  border-radius:12px;padding:14px 16px">
  <div style="display:flex;gap:22px;flex-wrap:wrap">
    <div style="flex:0 0 170px;min-width:150px;display:flex;flex-direction:column;justify-content:center">
      <div style="color:{MUTED};font-size:0.72rem;letter-spacing:0.06em">종합 판단</div>
      <div style="font-size:1.35rem;font-weight:700;color:{col};margin-top:2px">{icon} {verdict}</div>
      <div style="color:{MUTED};font-size:0.7rem;margin-top:4px">표시·참고용 · 매매신호 아님</div>
    </div>
    <div style="flex:1;min-width:220px">
      <div style="color:{GREEN};font-size:0.78rem;font-weight:600;margin-bottom:4px">강점</div>
      {_items(positives, "✔", GREEN, "특이 강점 없음")}
    </div>
    <div style="flex:1;min-width:220px">
      <div style="color:{RED};font-size:0.78rem;font-weight:600;margin-bottom:4px">주의점</div>
      {_items(risks, "⚠", RED, "특이 리스크 없음")}
    </div>
  </div>
  {footer}
</div>'''


# ── 🌡️ 시장 온도계 게이지 + 밸류 스트립 (홈 시장 지표 — 순수) ──────────────────
_TEMP_ZONES = [
    (RED, "과열 — 신중"), ("#ff9800", "다소 과열"), (MUTED, "중립"),
    ("#80cbc4", "분할매수 우호"), (GREEN, "공포·기회 구간"),
]


def market_temp_html(score, sub: str = "", phase_line: str = "",
                     spark: list | None = None) -> str:
    """시장 온도계 카드 — 역발상 종합(−1 과열 ↔ +1 기회) + 이력 스파크. 표시·참고용."""
    if score is None:
        return (f'<div style="padding:10px 12px;background:{PANEL};border:1px solid '
                f'{BORDER};border-radius:10px;text-align:center;color:{MUTED}">'
                f'🌡️ 시장 온도계 — 재료 부족</div>')
    inner = rating_gauge_html(score, sub=sub, title="🌡️ 시장 온도계 (역발상)",
                              end_labels=("과열", "기회"), zones=_TEMP_ZONES,
                              boxed=False)                      # 카드가 박스 — 이중 박스 방지
    sp = ""
    if spark and len(spark) >= 2:
        arrow = "↑ 데워지는 중" if spark[-1] >= spark[0] else "↓ 식는 중"
        sp = (f'<div style="text-align:center;margin-top:2px">{sparkline_svg(spark)}'
              f'<span style="color:{MUTED};font-size:0.68rem;margin-left:6px">'
              f'{len(spark)}일 · {arrow}</span></div>')
    tail = sp + (f'<div style="color:{MUTED};font-size:0.7rem;text-align:center;'
                 f'margin-top:4px">{phase_line}</div>' if phase_line else "")
    return (f'<div style="padding:10px 12px;background:{PANEL};border:1px solid {BORDER};'
            f'border-radius:10px;min-height:340px;display:flex;flex-direction:column;">'
            f'<div style="flex:1;display:flex;flex-direction:column;'
            f'justify-content:center">{inner}</div>{tail}</div>')


def valuation_strip_html(v: dict) -> str:
    """S&P500 밸류 스트립 — PER(역사 백분위 칩)·fPER·EPS 성장·PEG 단일 패널 (순수)."""
    if not v:
        return ""
    per = v.get("per_reported") or v.get("per")
    pct, pct20 = v.get("per_pctile_all"), v.get("per_pctile_20y")
    g = v.get("eps_growth_pct")
    peg = v.get("peg")

    def cell(label, val, extra="", col=TEXT):
        return (f'<div style="flex:1;min-width:150px;padding:4px 10px">'
                f'<div style="color:{MUTED};font-size:0.72rem">{label}</div>'
                f'<div style="font-size:1.35rem;font-weight:700;color:{col};'
                f'font-family:{_MONO}">{val}</div>{extra}</div>')

    chip = ""
    if pct is not None:
        c = RED if pct >= 90 else AMBER if pct >= 70 else MUTED
        chip = (f'<span style="display:inline-block;margin-top:3px;padding:2px 9px;'
                f'border:1px solid {c}44;border-radius:999px;color:{c};font-size:0.7rem;'
                f'background:{c}14">1871~ {pct:.0f}%ile · 20y {pct20:.0f}%ile</span>')
    g_col = GREEN if (g or 0) >= 0 else RED
    peg_col = GREEN if (peg or 9) < 1 else (AMBER if (peg or 9) < 2 else RED)
    return (f'<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;'
            f'background:{PANEL};border:1px solid {BORDER};border-radius:10px;'
            f'padding:9px 8px">'
            + cell("S&P500 PER (보고이익)", f"{per:.1f}" if per else "—", chip)
            + cell("fPER (컨센서스)", f"{v.get('fper'):.1f}" if v.get("fper") else "—")
            + cell("EPS 성장률 (1y 예상)", f"{g:+.1f}%" if g is not None else "—", "", g_col)
            + cell("PEG (교과서식)", f"{peg:.2f}" if peg else "—", "", peg_col)
            + '</div>')
