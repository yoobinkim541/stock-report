"""dashboard/theme.py — 토스증권 톤의 증권 앱 표면.

단일 진실원: 팔레트 상수 + **순수 HTML/SVG 컴포넌트 빌더**(테스트가능·streamlit 무관)
+ plotly 테마(순수) + `inject_global_css()`(streamlit lazy import).

등락은 토스증권 관례(상승 빨강·하락 파랑). 판단 카드의 양호/주의는 초록/빨강을 유지한다.
표면은 토스 그레이(#191f28)·Toss Blue(#3182f6)·16px 라운드. 색은 .streamlit/config.toml 과 일치.
`import dashboard.theme` 는 streamlit 을 끌어오지 않는다.
"""
from __future__ import annotations

import math
from html import escape as _html_esc
from urllib.parse import quote as _urlquote

# ── 팔레트 ────────────────────────────────────────────────────────────────────
# 표면색(BG/PANEL/TEXT/…)은 **CSS 변수 참조 문자열**이다 — HTML/SVG 빌더가 이 값을
# f-string 으로 박으면 `var(--tn-panel)` 이 되어 라이트/다크가 브라우저에서 자동 전환된다.
# 실제 hex 값은 `inject_global_css(light=…)` 가 모드별로 `.stApp` 에 주입한다(_SURF).
# ⚠️ 표면 상수를 plotly 색으로 쓰면 안 됨(plotly 는 hex 필요) — 차트는 AXIS_*/HOVER_* 사용.
BG = "var(--tn-bg)"
PANEL = "var(--tn-panel)"
PANEL2 = "var(--tn-panel2)"
BORDER = "var(--tn-border)"
GRID = "var(--tn-grid)"
TEXT = "var(--tn-text)"
MUTED = "var(--tn-muted)"

# 시맨틱 색. GREEN/RED 는 판단(양호/주의). UP/DOWN 은 가격 등락(토스: 빨강 상승·파랑 하락).
GREEN = "#16a34a"
RED = "#f04452"
UP = "#f04452"
DOWN = "#3182f6"
BLUE = "#3182f6"
AMBER = "#f59e0b"
VIOLET = "#8b95a1"

# 표면 실제값 (inject_global_css 가 CSS 변수로 주입) — 토스증권 다크(#17171c 바탕·#202027 카드)와
# 라이트(#f2f4f6 바탕·흰 카드). 카드는 테두리 없이 바탕과의 명도 차이로만 구분한다.
# chip = 세그먼트 컨트롤에서 선택된 칸(트랙=panel2 위에 떠 보이는 면).
_SURF = {
    "dark":  {"bg": "#17171c", "panel": "#202027", "panel2": "#2c2c35", "chip": "#45454f",
              "border": "#2c2c35", "grid": "#26262e", "text": "#f2f2f5", "muted": "#9e9ea7"},
    "light": {"bg": "#f2f4f6", "panel": "#ffffff", "panel2": "#eceef1", "chip": "#ffffff",
              "border": "#eaedf0", "grid": "#f2f4f6", "text": "#191f28", "muted": "#6b7684"},
}

# plotly 축·주석 — **테마 중립 그레이**(hex). 투명 paper 로 배경은 페이지를 따르고,
# 그리드·눈금·라벨은 라이트/다크 양쪽서 읽히는 반투명/중간 그레이로 고정(모드 전달 불필요).
AXIS_GRID = "rgba(128,135,150,0.18)"
AXIS_LINE = "rgba(128,135,150,0.38)"
AXIS_TEXT = "#8b93a7"                 # 눈금·주석 라벨 (중립)
AXIS_STRONG = "#6b7385"               # 현재가선 등 강조 라벨
HOVER_BG = "rgba(24,28,38,0.94)"      # 툴팁 — 자체 배경(다크 툴팁은 라이트서도 OK)
HOVER_BORDER = "rgba(128,135,150,0.4)"

# 숫자 글꼴 — 토스처럼 본문과 같은 Pretendard 에 등폭 숫자(tnum)만 켠다.
_NUM_FONT = "'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif"


# ── plotly 테마 (순수·plotly lazy) ───────────────────────────────────────────
def apply_plotly_theme(fig):
    """모든 차트에 적용하는 TradingView 풍 레이아웃 (테마 중립 — 투명 paper·중립 그레이 축).

    배경 투명 → 페이지 라이트/다크를 그대로 따름. 그리드·눈금은 AXIS_* 중립 그레이라
    양쪽서 읽힘(모드 전달 불필요·차트는 순수 유지)."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_NUM_FONT, size=12, color=AXIS_TEXT),
        colorway=[BLUE, GREEN, RED, AMBER, VIOLET, "#00bcd4", "#ff7043", "#66bb6a"],
        hovermode="x unified",
        hoverlabel=dict(bgcolor=HOVER_BG, bordercolor=HOVER_BORDER, font_family=_NUM_FONT,
                        font_size=12, font_color="#e8eaf0"),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=AXIS_TEXT, size=11)),
    )
    fig.update_xaxes(gridcolor=AXIS_GRID, zerolinecolor=AXIS_GRID, linecolor=AXIS_LINE,
                     tickfont=dict(color=AXIS_TEXT, family=_NUM_FONT), title_font=dict(color=AXIS_TEXT))
    fig.update_yaxes(gridcolor=AXIS_GRID, zerolinecolor=AXIS_GRID, linecolor=AXIS_LINE,
                     tickfont=dict(color=AXIS_TEXT, family=_NUM_FONT), title_font=dict(color=AXIS_TEXT))
    return fig


# ── 순수 HTML/SVG 빌더 ───────────────────────────────────────────────────────
def _num(x, dec=2):
    try:
        return f"{float(x):,.{dec}f}"
    except (TypeError, ValueError):
        return "—"


_WON = {"KRW", "₩", "원"}
_DOLLAR = {"USD", "$"}


def _hero_money(value, currency: str = "USD", *, signed: bool = False) -> str:
    """히어로 가격·등락. 국내 정수 가격은 ₩, 달러는 $, 그 외 단위는 뒤에 붙인다."""
    if not isinstance(value, (int, float)):
        return "—"
    code = str(currency or "USD")
    number = float(value)
    sign = ""
    if signed:
        sign = "+" if number >= 0 else "-"
        number = abs(number)
    if code in _WON or code == "₩":
        dec = 0 if number.is_integer() else 2
        return f"{sign}₩{number:,.{dec}f}"
    if code in _DOLLAR:
        return f"{sign}${number:,.2f}"
    if code == "%":
        return f"{sign}{number:,.2f}%"
    body = f"{number:,.2f}"
    return f"{sign}{body} {code}".strip() if code else f"{sign}{body}"


def ticker_hero_html(symbol, name="", price=None, change=None, change_pct=None,
                     asof="", currency="USD") -> str:
    """원형 심볼 뱃지 + 큰 가격 + 색으로만 구분한 등락 (토스증권 종목 헤더)."""
    pct = change_pct or 0
    up = pct >= 0
    # 반올림해 0.00% 인 등락은 보합 — "▼ -$0.00 0.00%" 처럼 방향을 지어내지 않는다
    flat = change is not None and round(pct, 2) == 0 and round(change or 0, 2) == 0
    col = "#8b95a1" if flat else (UP if up else DOWN)
    arrow = "▲" if up else "▼"
    badge = (symbol or "?").replace(".KS", "")[:4]
    code = str(currency or "USD")
    if str(symbol or "").upper().endswith((".KS", ".KQ")) and code in {"USD", "$", ""}:
        code = "KRW"
    has = isinstance(price, (int, float))
    price_html = _hero_money(price, code) if has else "—"
    if has and change is not None:
        delta = ("보합 0.00%" if flat else
                 f"{arrow} {_hero_money(change, code, signed=True)} ({'+' if up else '-'}{_num(abs(pct))}%)")
    else:
        delta = "—"
    asof_html = f'<div class="tn-asof">{asof}</div>' if asof else ""
    cur_html = "" if code in _WON or code in _DOLLAR or code in {"₩", "%"} else (
        f'<span class="tn-cur">{code}</span>' if code else "")
    row = [f'<span class="tn-price">{price_html}</span>']
    if cur_html:
        row.append(cur_html)
    row.append(f'<span class="tn-delta" style="color:{col}">{delta}</span>')
    # 한 줄로 붙인다. 빈 {cur_html} 줄 + 4칸 이상 들여쓰기는 st.markdown 이
    # 코드블록으로 그려 등락·기준시각 HTML 이 그대로 보인다.
    return (
        f'<div class="tn-hero">'
        f'<div class="tn-badge" style="color:{col};background:{col}1f">{badge}</div>'
        f'<div class="tn-hero-main">'
        f'<div class="tn-hero-name">{name or symbol}</div>'
        f'<div class="tn-hero-sym">{symbol}</div>'
        f'<div class="tn-hero-row">{"".join(row)}</div>'
        f'{asof_html}'
        f'</div></div>'
    )


_GAUGE_ZONES = [
    (UP, "강력매도"), ("#fb8a93", "매도"), ("#8b95a1", "중립"),
    ("#7aa7f7", "매수"), (DOWN, "강력매수"),
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
    vcol = DOWN if score > 0.15 else UP if score < -0.15 else MUTED
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
    <text x="{cx - R}" y="{ly}" fill="{MUTED}" font-size="10" font-family="{_NUM_FONT}" text-anchor="middle">{end_labels[0]}</text>
    <text x="{cx + R}" y="{ly}" fill="{MUTED}" font-size="10" font-family="{_NUM_FONT}" text-anchor="middle">{end_labels[1]}</text>
  </svg>
  <div class="tn-gauge-verdict" style="color:{vcol}">{verdict}</div>
  {f'<div class="tn-gauge-sub">{sub}</div>' if sub else ''}
</div>'''


_VAL_ZONES = [
    (UP, "크게 고평가"), ("#fb8a93", "고평가"), ("#8b95a1", "적정 수준"),
    ("#7aa7f7", "저평가"), (DOWN, "크게 저평가"),
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
        f'<b style="font-size:1.02rem;color:{col or TEXT};font-family:{_NUM_FONT}">{val}</b></div>'
        for lab, val, col in cells)
    return (f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
            f'padding:9px 14px;background:{PANEL};border:none;'
            f'border-radius:16px">{items}</div>')


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
                 f'font-size="27" font-weight="700" font-family="{_NUM_FONT}">{bigtxt}</text>')
    if sub:
        parts.append(f'<text x="{cx}" y="118" text-anchor="middle" fill="{MUTED}" '
                     f'font-size="11" font-family="{_NUM_FONT}">{sub}</text>')
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
    return f'''<div style="padding:12px 14px;background:{PANEL};border:none;border-radius:20px;min-height:340px;display:flex;flex-direction:column;">
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
    ccol = UP if (chg or 0) >= 0 else DOWN
    pxs = f"{price:,.0f}" if isinstance(price, (int, float)) else "—"
    chgs = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else ""

    def g(lbl, v):
        c = _rsi_color(v)
        big = f"{v:.0f}" if isinstance(v, (int, float)) else "—"
        val = v if isinstance(v, (int, float)) else None
        return (f'<div style="flex:1;text-align:center"><div style="color:{MUTED};font-size:0.72rem">{lbl}</div>'
                f'{_gauge_svg(val, 0, 100, _RSI_GAUGE, big=big, big_col=c, sub=_rsi_zone(v))}</div>')

    return f'''<div style="padding:12px 14px;background:{PANEL};border:none;border-radius:20px;min-height:340px;display:flex;flex-direction:column;">
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <b style="color:{TEXT}">{name}</b>
    <span style="font-family:{_NUM_FONT};color:{MUTED};font-size:0.82rem">{pxs} <span style="color:{ccol}">{chgs}</span></span></div>
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
    col = UP if up else DOWN
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
        f'<div style="font-family:{_NUM_FONT};color:{TEXT};font-size:1.02rem;font-weight:600">'
        f'{pstr}{suffix}</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:4px">'
        f'<span style="font-family:{_NUM_FONT};color:{col};font-size:.72rem;white-space:nowrap">'
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
.tn-macro .tn-macro-card {{ padding:12px 14px; background:{PANEL}; border:none;
  border-radius:16px; display:flex; flex-direction:column; gap:2px;
  text-decoration:none !important; color:inherit; transition:background .12s; }}
a.tn-macro-card:hover {{ background:{PANEL2}; }}
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
    col = UP if vals[-1] >= vals[0] else DOWN
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" class="tn-spark">'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _fmt_last(last, money: str = "", decimals: int = 2) -> str:
    text = _num(last, 0 if decimals <= 0 else decimals)
    if text == "—" or not money:
        return text
    return f"{money}{text}"


def watchlist_row_html(symbol, last=None, chg_pct=None, spark=None, name="",
                       link: bool = True, money: str = "", decimals: int = 2) -> str:
    up = (chg_pct or 0) >= 0
    col = UP if up else DOWN
    chg = f"{'+' if up else ''}{_num(chg_pct)}%" if chg_pct is not None else "—"
    # 회사명은 title 툴팁으로(좁은 4열 그리드 유지 — 호버 시 노출)
    safe_symbol = _html_esc(str(symbol))
    safe_name = _html_esc(str(name)) if name and name != symbol else ""
    tip = f' title="{safe_name}"' if safe_name else ""
    # 사이드바(≈340px)에선 스파크라인 생략 → 3열(종목·값·등락%)로 등락%열 잘림 방지
    inner = f'''<span class="tn-wl-sym"{tip}>{safe_symbol}</span>
  <span class="tn-wl-last">{_fmt_last(last, money, decimals)}</span>
  <span class="tn-wl-chg" style="color:{col}">{chg}</span>'''
    if link and symbol and symbol != "?":
        href = "?tk=" + _urlquote(str(symbol), safe="")
        label = safe_name or safe_symbol
        return (f'<a class="tn-wl-row" href="{href}" target="_self" '
                f'aria-label="{label} 종목 분석">{inner}</a>')
    return f'<div class="tn-wl-row">{inner}</div>'


def watchlist_html(rows: list[dict], title="워치리스트", link: bool = True,
                   money: str = "", decimals: int = 2) -> str:
    body = "".join(watchlist_row_html(r.get("symbol", "?"), r.get("last"),
                                      r.get("chg_pct"), r.get("spark"), r.get("name", ""),
                                      link=link, money=money, decimals=decimals)
                   for r in rows)
    return f'<div class="tn-wl"><div class="tn-wl-head">{title}</div>{body}</div>'


_AVATAR_COLORS = ("#3182f6", "#f04452", "#00a878", "#ff8a00", "#7c5cff", "#0ea5e9", "#e5508a", "#4e5968")


def _avatar_html(ticker, name="") -> str:
    """종목 아바타 — 한글 종목명은 첫 글자, 그 외는 티커 첫 글자. 색은 티커로 고정."""
    head = str(name or "").strip()[:1]
    if not ("가" <= head <= "힣"):
        head = (str(ticker or "").strip()[:1] or head or "?").upper()
    color = _AVATAR_COLORS[sum(map(ord, str(ticker or name or "?"))) % len(_AVATAR_COLORS)]
    return (f'<span class="tn-sl-ava" style="background:{color}" aria-hidden="true">'
            f'{_html_esc(head)}</span>')


def stock_list_row_html(row: dict, link: bool = True) -> str:
    """토스식 종목 행 — 아바타 / 종목명·티커·태그·메모 / 값·등락 (순수).

    row: {ticker, name, tags: [str], memo, value, delta, direction(+1 상승·-1 하락·0 중립), title}
    link=True 면 행 전체가 `?tk=` 앵커 — app.py 가 소비해 종목 분석으로 이동.
    """
    ticker = str(row.get("ticker") or "").strip()
    name = str(row.get("name") or "").strip() or ticker or "?"
    direction = row.get("direction") or 0
    col = UP if direction > 0 else DOWN if direction < 0 else MUTED
    tags = "".join(f'<span class="tn-sl-chip">{_html_esc(str(t))}</span>'
                   for t in row.get("tags") or [] if str(t).strip())
    memo = str(row.get("memo") or "").strip()
    memo_html = f'<span class="tn-sl-memo">{_html_esc(memo)}</span>' if memo else ""
    tk_html = f'<span class="tn-sl-tk">{_html_esc(ticker)}</span>' if ticker and ticker != name else ""
    inner = (
        f'{_avatar_html(ticker, name)}'
        f'<span class="tn-sl-main"><span class="tn-sl-name">{_html_esc(name)}</span>'
        f'<span class="tn-sl-sub">{tk_html}{tags}{memo_html}</span></span>'
        f'<span class="tn-sl-side"><span class="tn-sl-val">{_html_esc(str(row.get("value") or "—"))}</span>'
        f'<span class="tn-sl-delta" style="color:{col}">{_html_esc(str(row.get("delta") or ""))}</span></span>')
    title = str(row.get("title") or "").strip()
    tip = f' title="{_html_esc(title)}"' if title else ""
    if link and ticker and ticker not in ("?", "—"):
        href = "?tk=" + _urlquote(ticker, safe="")
        return (f'<a class="tn-sl-row" href="{href}" target="_self" '
                f'aria-label="{_html_esc(name)} 종목 분석"{tip}>{inner}</a>')
    return f'<div class="tn-sl-row"{tip}>{inner}</div>'


def stock_list_html(rows: list[dict], *, title: str = "", meta: str = "",
                    empty: str = "해당 종목이 없습니다.", link: bool = True, scroll: bool = False) -> str:
    """토스식 종목 리스트 카드 — 머리줄(제목·보조) + 행들. 행이 없으면 empty 문구.
    scroll=True 면 6행이 넘을 때 본문만 카드 안에서 스크롤한다 (머리줄 고정, 행은 숨기지 않음)."""
    head = ""
    if title or meta:
        head = (f'<div class="tn-sl-head"><b>{_html_esc(title)}</b>'
                f'<span>{_html_esc(meta)}</span></div>')
    body = ("".join(stock_list_row_html(r, link=link) for r in rows or [])
            or f'<div class="tn-sl-empty">{_html_esc(empty)}</div>')
    if scroll and len(rows or []) > 6:
        body = f'<div class="tn-sl-body">{body}</div>'
    return f'<div class="tn-sl">{head}{body}</div>'


def section_head_html(title: str, sub: str = "") -> str:
    """페이지 안 큰 섹션 제목 — 굵은 제목 + 회색 보조줄."""
    sub_html = f'<span class="tn-h-sub">{_html_esc(sub)}</span>' if sub else ""
    return f'<div class="tn-h"><span class="tn-h-title">{_html_esc(title)}</span>{sub_html}</div>'


def holding_watch_rows(holdings: list[dict]) -> tuple[list[dict], list[dict]]:
    """보유 스냅샷을 미국/국내 워치 행으로 나눈다.

    국내 코드(005930·0167A0·A005930)는 클릭 링크가 먹도록 .KS/.KQ 로 정규화한다.
    평가액은 last, 수익률은 chg_pct. 원본 dict 는 바꾸지 않는다.
    """
    us: list[dict] = []
    kr: list[dict] = []
    ranked = sorted(
        (h for h in holdings or [] if isinstance(h, dict) and h.get("ticker")),
        key=lambda h: h.get("value", 0) or 0,
        reverse=True,
    )
    for h in ranked:
        raw = str(h.get("ticker") or "").strip()
        if not raw:
            continue
        try:
            import ticker_names
            norm = ticker_names.normalize_input(raw) or raw
        except Exception:
            norm = raw
        row = {
            "symbol": norm,
            "name": h.get("name"),
            "last": h.get("value"),
            "chg_pct": h.get("ret"),
        }
        if norm.upper().endswith((".KS", ".KQ")):
            kr.append(row)
        else:
            us.append(row)
    return us, kr


def sidebar_holding_rails(usd_holdings, kr_holdings=None) -> tuple[list[dict], list[dict]]:
    """미국북·국내북을 사이드바 레일로 합친다. 국내북 평가액이 있으면 그걸 쓴다."""
    us, kr_misc = holding_watch_rows(usd_holdings)
    _, kr = holding_watch_rows(kr_holdings or [])
    seen = {r["symbol"] for r in kr}
    kr.extend(r for r in kr_misc if r["symbol"] not in seen)
    return us, kr


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


def paper_rail_html(rows: list[dict], title="🧪 모의투자 · 평가액 · 현 세대 수익률") -> str:
    """사이드바 모의 계좌 레일 (워치리스트와 동일 tn-wl 스타일 — 순수 빌더).

    rows: views.paper_glance 출력 [{label, currency, nav, cum_ret, current_cum_ret, day_ret, n_days,
    awaiting_generation, balance_failure}].
    3열: 계좌 · NAV(압축) · 수익률(색). NAV 는 현 세대 계좌라 수익률도 같은 세대 값을 보여준다
    (세대 리셋 후 $100,000 옆에 전체 세대 누적 -60% 가 붙어 모순돼 보이던 문제).
    세대 마감 직후 새 세대 첫 기록 전(awaiting_generation)에는 직전 세대 마감 NAV 와
    "새 세대 대기" 를 보여준다 — 현 세대 수익률이 없으므로 전체 누적으로 대체하지 않는다.
    마지막 유효 기록 이후 잔고 조회가 실패하고 있으면(balance_failure) NAV 는 마지막 유효값
    그대로 두고 툴팁에 실패 시각을 붙인다. 전체 세대 누적·전일·기록일수는 title 툴팁.
    """
    body = []
    for r in rows or []:
        life = r.get("cum_ret")
        gen = r.get("generation")
        if r.get("awaiting_generation"):
            col, chg = MUTED, "새 세대 대기"
            closed = r.get("closed_generation")
            tip_bits = [f"{f'세대 {closed} ' if closed else ''}마감 순자산"
                        + (f" ({r['closed_nav_date']})" if r.get("closed_nav_date") else ""),
                        "새 세대 첫 기록 대기"]
            if life is not None:
                tip_bits.append(f"{f'전체 {gen}세대' if gen else '전체'} 누적 {life:+.2f}%")
        else:
            cur_cum = r.get("current_cum_ret")
            shown = cur_cum if cur_cum is not None else life
            up = (shown or 0) >= 0
            col = UP if up else DOWN
            chg = f"{'+' if up else ''}{_num(shown)}%" if shown is not None else "—"
            day = r.get("day_ret")
            tip_bits = []
            if day is not None:
                tip_bits.append(f"전일 {day:+.2f}%")
            if shown is not None:
                tip_bits.append(f"현 세대 {shown:+.2f}%")
            if life is not None and life != shown:
                tip_bits.append(f"{f'전체 {gen}세대' if gen else '전체'} 누적 {life:+.2f}%")
            tip_bits.append(f"기록 {r.get('n_days', 0)}일")
        failure = r.get("balance_failure") or {}
        if failure:
            when = str(failure.get("date") or "")[:10]
            tip_bits.append(f"잔고 조회 실패{f' {when}' if when else ''} — 마지막 유효 기록 기준")
        tip = f' title="{" · ".join(tip_bits)}"'
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
        f".ec-cell{{background:{PANEL};border:none;border-radius:10px;"
        "min-height:78px;padding:4px 6px;overflow:hidden}"
        f".ec-today{{border-color:{BLUE};box-shadow:inset 0 0 0 1px {BLUE}}}"
        f".ec-dim .ec-date{{color:{MUTED}}}"
        ".ec-past{opacity:.55}"
        f".ec-date{{font-family:{_NUM_FONT};font-size:.72rem;color:{TEXT};margin-bottom:2px}}"
        f".ec-mon{{color:{AMBER};font-weight:700}}"
        f".ec-ev{{font-size:.68rem;color:{TEXT};white-space:nowrap;overflow:hidden;"
        "text-overflow:ellipsis;line-height:1.55;cursor:default}"
        f".ec-more{{font-size:.62rem;color:{MUTED}}}"
        "@media (max-width:600px){.ec-cell{min-height:56px;padding:3px 4px}"
        ".ec-ev{font-size:.6rem}}"
        "</style>")
    return style + f'<div class="ec-cal">{heads}{"".join(cells)}</div>'


# ── 전역 CSS 주입 (streamlit lazy) ───────────────────────────────────────────
def _surface_vars(light: bool) -> str:
    """모드별 표면 CSS 변수 정의 (.stApp 에 주입 — 모든 var(--tn-*) 소비자 자동 전환)."""
    s = _SURF["light"] if light else _SURF["dark"]
    return ("  --tn-bg:%(bg)s; --tn-panel:%(panel)s; --tn-panel2:%(panel2)s;"
            " --tn-chip:%(chip)s; --tn-border:%(border)s; --tn-grid:%(grid)s;"
            " --tn-text:%(text)s; --tn-muted:%(muted)s;" % s)


# 라이트 모드 — 네이티브 Streamlit 표면(config.toml=dark 로 칠해진 것)을 덮는 오버라이드.
# Streamlit 1.58 은 CSS 변수 미노출(Emotion 해시 클래스)이라 testid 선택자 + !important 로
# 주요 표면만 정확히 재도색한다. 텍스트는 !important 없이(인라인 시맨틱 색 보존) 특이도로.
_LIGHT_OVERRIDE = """
<style>
.stApp { background: #f2f4f6 !important; }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stSidebar"] { background: #ffffff !important; }
[data-testid="stMetric"], [data-testid="stExpander"] { background: #ffffff !important; }
[data-testid="stMetricValue"] { color: #191f28 !important; }
[data-testid="stDataFrame"], [data-testid="stTable"] { background: #ffffff; }
/* 드롭다운 글자 — baseweb 가 다크 config 색을 인라인으로 박는다 */
[data-baseweb="select"] div, [data-baseweb="select"] input,
[data-baseweb="select"] svg, [data-baseweb="input"] input,
[data-baseweb="popover"] [role="option"] {
  color: #191f28 !important; -webkit-text-fill-color: #191f28 !important; }
[data-baseweb="popover"] [role="listbox"], [data-baseweb="popover"] [data-testid="stPopoverBody"],
[data-baseweb="menu"] { background: #ffffff !important; }
[data-testid="stWidgetLabel"] p, .stRadio label, .stCheckbox label { color: #4e5968 !important; }
/* 본문 텍스트 — !important 없이(인라인 시맨틱 색 보존) 특이도로 다크 기본색만 교체 */
.stApp p, .stApp li, .stApp label, .stMarkdown, [data-testid="stMarkdownContainer"],
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, [data-testid="stMetricLabel"] p { color: #191f28; }
[data-testid="stCaptionContainer"], .stApp small { color: #6b7684; }
.stCode, pre, pre code { background: #f2f4f6 !important; color: #191f28 !important; }
[data-testid="stButtonGroup"] [data-testid$="Active"] { box-shadow: 0 1px 3px rgba(0,23,51,.08); }
/* 라디오·토글·체크박스 꺼짐 — 다크 config 회색(#2c2c35)이 라이트 배경에 검은 점·검은 막대로 보였다 */
[data-testid="stRadio"] label:has(input:not(:checked)) > div:first-child { background: #d1d6db !important; }
[data-testid="stRadio"] label:has(input:not(:checked)) > div:first-child > div { background: #ffffff !important; }
[data-testid="stCheckbox"] label:has(input:not(:checked)) > div:first-child { background: #d1d6db !important; }
[data-testid="stCheckbox"] label > div:first-child > div { background: #ffffff !important; }
/* 숫자 입력 — 컨테이너 테두리·± 버튼이 다크 회색으로 남던 것 */
[data-testid="stNumberInputContainer"] { border-color: transparent !important; background: #eceef1 !important; }
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] {
  background: #e5e8eb !important; color: #4e5968 !important; }
/* 플롯리 모드바 — 다크 템플릿의 검은 반투명 띠 */
.modebar-group { background: rgba(255,255,255,.9) !important; }
.modebar-btn path { fill: #8b95a1 !important; }
.modebar-btn.active path, .modebar-btn:hover path { fill: #3182f6 !important; }
::-webkit-scrollbar-thumb { background: #d1d6db; }
::-webkit-scrollbar-thumb:hover { background: #b0b8c1; }
</style>
"""


def inject_global_css(light: bool | None = None):
    """전역 테마 CSS 주입 — 표면 CSS 변수(모드별) + 공통 규칙(+라이트 오버라이드).

    light 미지정 시 세션 상태(theme.is_light)를 읽어 자동 결정."""
    import streamlit as st
    if light is None:
        light = is_light()
    st.markdown(_FONT_LINKS + f"<style>.stApp {{\n{_surface_vars(light)}\n}}</style>" + _CSS,
                unsafe_allow_html=True)
    if light:
        st.markdown(_LIGHT_OVERRIDE, unsafe_allow_html=True)


# 토스페이스 — 토스가 공개한 이모지 글꼴. 이모지 코드포인트에만 unicode-range 가 걸려 있어
# 본문 글꼴(Pretendard) 뒤에 두면 이모지만 토스 모양으로 바뀐다.
_FONT_LINKS = ('<style>@import url("https://cdn.jsdelivr.net/gh/toss/tossface@v1.6.1/dist/tossface.css");'
               '</style>')


def is_light() -> bool:
    """현재 라이트 모드 여부 — 세션 상태(기본 다크). streamlit 없으면 False."""
    try:
        import streamlit as st
        return bool(st.session_state.get("tn_light", False))
    except Exception:
        return False


def _markdown_safe_html(html: str) -> str:
    """빈 줄 다음 4칸 들여쓰기를 st.markdown 이 코드블록으로 삼지 않게 한다."""
    return "\n".join(line.strip() for line in (html or "").splitlines() if line.strip())


def render(html: str):
    """순수 빌더 HTML 을 화면에 렌더. Markdown 코드블록으로 쪼개지지 않게 한다."""
    import streamlit as st
    text = _markdown_safe_html(html)
    # st.html 은 DOMPurify 라 <style>·SVG 를 자를 수 있다. 색은 .stApp CSS 변수라
    # 기존 markdown 경로를 유지하고, 빈 줄·들여쓰기만 제거한다.
    st.markdown(text, unsafe_allow_html=True)


_CHIP = "var(--tn-chip)"

_CSS = f"""
<style>
html, body, .stApp {{ overscroll-behavior: none; }}
.stApp {{ background: {BG}; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.8rem; padding-bottom: 3.6rem; max-width: 1240px; }}

/* ── 글꼴: 본문 Pretendard, 이모지는 토스페이스 ── */
html, body, [class*="st-"], .stApp, p, span, div, label, input, button, textarea {{
  font-family: 'Pretendard', 'Tossface', -apple-system, BlinkMacSystemFont, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
[data-testid="stIconMaterial"], .material-symbols-rounded, .material-symbols-outlined,
[class*="material-symbols"], span[translate="no"] {{
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
}}

[data-testid="stSkeleton"] {{
  background: linear-gradient(90deg, {PANEL} 25%, {PANEL2} 38%, {PANEL} 55%) !important;
  background-size: 400% 100% !important;
  animation: tn-shimmer 1.3s ease-in-out infinite;
  border-radius: 20px;
}}
@keyframes tn-shimmer {{
  0% {{ background-position: 100% 50%; }}
  100% {{ background-position: 0% 50%; }}
}}
/* rerun 중 이전 화면을 펄스하지 않는다 — 투명도 애니메이션이 화면 떨림으로 보인다 */
[data-stale="true"] {{ animation: none !important; opacity: 1 !important; }}
.stSpinner > div {{ border-top-color: {BLUE} !important; }}

/* ── 타이포: 굵고 촘촘한 제목, 숫자는 등폭 숫자 ── */
h1, h2, h3, h4, h5 {{ letter-spacing: -0.035em; font-weight: 700; color: {TEXT}; }}
h1 {{ font-size: 1.75rem !important; font-weight: 800 !important; padding-bottom: .4rem !important; }}
h2 {{ font-size: 1.4rem !important; }}
h3 {{ font-size: 1.22rem !important; padding-top: 1.1rem !important; }}
h4 {{ font-size: 1.1rem !important; padding-top: .9rem !important; }}
h5 {{ font-size: 1rem !important; padding-top: .6rem !important; }}
[data-testid="stMetricValue"], code, .tn-price, .tn-delta, .tn-wl-last, .tn-wl-chg,
.tn-gauge text, .tn-sl-val, .tn-sl-delta, [data-testid="stDataFrame"] {{
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }}
.tn-price, .tn-delta, .tn-wl-last, .tn-wl-chg {{ font-family: {_NUM_FONT}; }}
[data-testid="stCaptionContainer"] {{ color: {MUTED} !important; font-size: .8rem; line-height: 1.5; }}
/* 해시·URL 같은 끊김 없는 긴 토큰이 컬럼 밖으로 넘쳐 옆 컬럼과 겹치지 않게 */
[data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] {{ overflow-wrap: anywhere; }}
[data-testid="stMarkdownContainer"] a, [data-testid="stCaptionContainer"] a {{ text-decoration: none; font-weight: 500; }}
[data-testid="stMarkdownContainer"] a:hover, [data-testid="stCaptionContainer"] a:hover {{ text-decoration: underline; }}
[data-testid="stMarkdownContainer"] :not(pre) > code, [data-testid="stCaptionContainer"] :not(pre) > code {{
  background: {PANEL2}; color: {MUTED}; border-radius: 6px; padding: 1px 6px; font-size: .78em;
  font-family: {_NUM_FONT}; font-weight: 600; }}

/* ── 카드: 테두리 없이 바탕 명도 차이 + 큰 라운드 ── */
[data-testid="stMetric"] {{
  background: {PANEL}; border: none; border-radius: 20px;
  padding: 18px 20px 16px; box-shadow: none; }}
[data-testid="stMetricLabel"] p {{
  color: {MUTED} !important; font-size: .8rem !important;
  text-transform: none; letter-spacing: -0.01em; font-weight: 500; }}
[data-testid="stMetricValue"] {{ font-size: 1.5rem !important; font-weight: 700; color: {TEXT};
  letter-spacing: -0.035em; }}
[data-testid="stMetricDelta"] {{ font-weight: 600; }}
[data-testid="stExpander"] {{ border: none; border-radius: 20px; background: {PANEL}; }}
[data-testid="stExpander"] details {{ border: none !important; border-radius: 20px; }}
[data-testid="stExpander"] summary {{ padding: 14px 20px; font-weight: 600; border-radius: 20px; }}
[data-testid="stExpander"] summary:hover {{ color: {TEXT}; background: {PANEL2}; }}
[class*="st-key-tn-card"] {{ background: {PANEL}; border-color: transparent !important; border-radius: 20px !important; }}
[data-testid="stDataFrame"], [data-testid="stTable"] {{ border: none; border-radius: 16px; overflow: hidden; }}
[data-testid="stAlertContainer"] {{ border-radius: 16px; border: none; }}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {{ background: rgba(49,130,246,.12); }}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {{ background: rgba(255,149,0,.13); }}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {{ background: rgba(240,68,82,.13); }}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {{ background: rgba(0,180,110,.13); }}
hr {{ border-color: {BORDER} !important; margin: 1.6rem 0 !important; }}

/* ── 탭·세그먼트: 회색 트랙 위 떠 있는 칸 ── */
.stTabs [data-baseweb="tab-list"] {{
  gap: 2px; border-bottom: none; background: {PANEL2};
  border-radius: 14px; padding: 4px; width: fit-content; max-width: 100%; }}
.stTabs [data-baseweb="tab"] {{
  color: {MUTED}; font-weight: 600; padding: 8px 14px; border-radius: 10px; }}
.stTabs [aria-selected="true"] {{ color: {TEXT} !important; background: {_CHIP}; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
[data-testid="stButtonGroup"] div:has(> [data-testid^="stBaseButton-segmented_control"]) {{
  background: {PANEL2}; border-radius: 14px; padding: 4px; gap: 2px; width: fit-content; max-width: 100%; }}
[data-testid="stButtonGroup"] [data-testid^="stBaseButton-segmented_control"] {{
  border: none !important; border-radius: 10px !important; background: transparent !important;
  color: {MUTED} !important; font-weight: 600; min-height: 34px; padding: 4px 11px; }}
[data-testid="stButtonGroup"] [data-testid="stBaseButton-segmented_controlActive"] {{
  background: {_CHIP} !important; color: {TEXT} !important; font-weight: 700; }}
[data-testid="stButtonGroup"] [data-testid^="stBaseButton-pills"] {{
  border: none !important; border-radius: 999px !important; background: {PANEL2} !important;
  color: {TEXT} !important; font-weight: 600; }}
[data-testid="stButtonGroup"] [data-testid="stBaseButton-pillsActive"] {{
  background: {BLUE}24 !important; color: {BLUE} !important; }}

/* ── 버튼: 주요=토스 블루, 보조=회색 면 ── */
.stButton button, [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button,
[data-testid="stPopover"] > div > button {{
  border-radius: 14px !important; font-weight: 600; min-height: 42px;
  transition: background .12s, transform .06s; }}
[data-testid="stPopover"] > div > button p {{ white-space: nowrap; }}
[data-testid="stBaseButton-secondary"], [data-testid="stPopover"] > div > button,
[data-testid="stDownloadButton"] button {{
  background: {PANEL2} !important; border: none !important; color: {TEXT} !important; }}
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stPopover"] > div > button:hover,
[data-testid="stDownloadButton"] button:hover {{ background: {_CHIP} !important; }}
[data-testid="stBaseButton-primary"] {{ background: {BLUE} !important; border: none !important; color: #fff !important; }}
[data-testid="stBaseButton-primary"]:hover {{ background: #1b64da !important; }}
.stButton button:active, [data-testid="stBaseButton-primary"]:active {{ transform: scale(.98); }}
[data-testid="stBaseButton-tertiary"], [data-testid="stBaseButton-borderless"] {{ color: {BLUE} !important; }}

/* ── 입력: 테두리 없는 회색 면, 포커스 때만 파랑 ── */
[data-baseweb="input"], [data-baseweb="input"] > div, [data-baseweb="select"] > div, [data-baseweb="textarea"],
[data-baseweb="base-input"], [data-testid="stChatInput"] > div {{
  border-radius: 14px !important; background: {PANEL2} !important; border-color: transparent !important; }}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="base-input"] input {{
  background: transparent !important; }}
[data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within > div,
[data-baseweb="textarea"]:focus-within {{ border-color: {BLUE} !important; }}
[data-testid="stWidgetLabel"] p {{ font-weight: 500; }}

/* ── 사이드바: 바탕과 같은 면, 선택 메뉴는 회색 칸 ── */
[data-testid="stSidebar"] {{ border-right: 1px solid {BORDER}; background: {BG}; }}
[data-testid="stSidebar"] h3 {{ font-size: .95rem !important; color: {TEXT}; letter-spacing: -0.02em; padding-top: 0 !important; }}
[data-testid="stSidebarNav"] ul {{ padding: 4px 8px 8px; }}
[data-testid="stSidebarNav"] a {{
  border-radius: 12px; margin: 1px 0; font-weight: 600; letter-spacing: -0.02em; }}
[data-testid="stSidebarNav"] a:hover {{ background: {PANEL2}; }}
[data-testid="stSidebarNav"] a span {{ color: {MUTED}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{ background: {PANEL2}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: {TEXT}; font-weight: 700; }}

::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: {BORDER}; border-radius: 8px; }}

.tn-hero {{ display: flex; align-items: center; gap: 16px; margin: 2px 0 18px; }}
.tn-badge {{
  width: 56px; height: 56px; border-radius: 50%; border: none !important;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 1rem; letter-spacing: -.03em; flex: 0 0 auto; }}
.tn-hero-name {{ font-size: 1.3rem; font-weight: 700; color: {TEXT}; line-height: 1.2; letter-spacing: -0.035em; }}
.tn-hero-sym {{ color: {MUTED}; font-size: .8rem; margin-top: 2px; }}
.tn-hero-row {{ display: flex; align-items: baseline; gap: 10px; margin-top: 6px; flex-wrap: wrap; }}
.tn-price {{ font-size: 2.1rem; font-weight: 800; color: {TEXT}; line-height: 1; letter-spacing: -0.045em; }}
.tn-cur {{ color: {MUTED}; font-size: .85rem; }}
.tn-delta {{ font-size: .95rem; font-weight: 700; padding: 0; background: none !important; }}
.tn-asof {{ color: {MUTED}; font-size: .74rem; margin-top: 8px; }}

.tn-gauge {{ background: {PANEL}; border: none; border-radius: 20px;
  padding: 18px 14px 18px; text-align: center; }}
.tn-gauge-verdict {{ font-size: 1.2rem; font-weight: 700; margin-top: -6px; letter-spacing: -0.03em; }}
.tn-gauge-sub {{ color: {MUTED}; font-size: .76rem; margin-top: 2px; }}

.tn-wl {{ background: {PANEL}; border: none; border-radius: 20px; overflow: hidden; }}
.tn-wl-head {{ padding: 14px 16px 6px; font-size: .78rem; font-weight: 600; color: {MUTED};
  letter-spacing: -0.01em; }}
.tn-wl-row {{ display: grid; grid-template-columns: 1fr auto auto; align-items: center;
  gap: 10px; padding: 10px 16px; border-top: none; }}
a.tn-wl-row {{ color: inherit; text-decoration: none; cursor: pointer; }}
a.tn-wl-row:focus-visible {{ outline: 2px solid {BLUE}; outline-offset: -2px; }}
.tn-wl-row:hover {{ background: {PANEL2}; }}
.tn-wl-sym {{ font-weight: 600; color: {TEXT}; font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.tn-wl-last {{ font-family: {_NUM_FONT}; color: {TEXT}; font-size: .86rem; font-weight: 600; white-space: nowrap; }}
.tn-wl-chg {{ font-family: {_NUM_FONT}; font-size: .84rem; font-weight: 600; text-align: right; white-space: nowrap; }}
.tn-spark {{ display: block; }}

.tn-sec {{ border-left: none; padding-left: 0; margin: 22px 0 10px; }}
.tn-sec span {{ font-size: 1rem; font-weight: 700; color: {TEXT}; letter-spacing: -0.03em; }}

.tn-sl {{ background: {PANEL}; border: none; border-radius: 20px; overflow: hidden;
  margin-bottom: 14px; padding-bottom: 6px; }}
.tn-sl-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  padding: 18px 20px 8px; }}
.tn-sl-head b {{ font-size: 1.05rem; font-weight: 700; color: {TEXT}; letter-spacing: -0.03em; }}
.tn-sl-head span {{ font-size: .78rem; color: {MUTED}; text-align: right; }}
.tn-sl-row {{ display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: center;
  gap: 14px; padding: 11px 20px; }}
a.tn-sl-row {{ color: inherit; text-decoration: none !important; cursor: pointer; transition: background .12s; }}
a.tn-sl-row:hover {{ background: {PANEL2}; }}
a.tn-sl-row:focus-visible {{ outline: 2px solid {BLUE}; outline-offset: -2px; }}
.tn-sl-ava {{ width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; color: #fff; font-weight: 700; font-size: 1rem; }}
.tn-sl-main {{ display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
.tn-sl-name {{ font-size: 1rem; font-weight: 600; color: {TEXT}; letter-spacing: -0.02em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.tn-sl-sub {{ display: flex; align-items: center; gap: 6px; min-width: 0; font-size: .78rem; color: {MUTED};
  white-space: nowrap; overflow: hidden; }}
.tn-sl-tk {{ font-weight: 600; }}
.tn-sl-chip {{ flex: none; padding: 2px 8px; border-radius: 8px; background: {PANEL2};
  border: none; font-size: .7rem; font-weight: 600; }}
.tn-sl-memo {{ overflow: hidden; text-overflow: ellipsis; }}
.tn-sl-side {{ display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }}
.tn-sl-val {{ font-size: 1rem; font-weight: 700; color: {TEXT}; font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em; white-space: nowrap; }}
.tn-sl-delta {{ font-size: .84rem; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.tn-sl-empty {{ padding: 22px 20px; color: {MUTED}; font-size: .86rem; text-align: center; }}
.tn-sl-body {{ max-height: 420px; overflow-y: auto; }}
.tn-h {{ display: flex; flex-direction: column; gap: 3px; margin: 30px 0 12px; }}
.tn-h-title {{ font-size: 1.3rem; font-weight: 700; color: {TEXT}; letter-spacing: -0.035em; }}
.tn-h-sub {{ font-size: .8rem; color: {MUTED}; }}

.tn-etf-rets {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 4px 0 12px; }}
.tn-etf-chip {{ flex: 1 1 92px; background: {PANEL}; border: none;
  border-radius: 16px; padding: 12px 14px; }}
.tn-etf-chip span {{ display: block; color: {MUTED}; font-size: .72rem; font-weight: 600; }}
.tn-etf-chip b {{ font-size: 1.05rem; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }}
.tn-hold {{ background: {PANEL}; border: none; border-radius: 20px; padding: 6px 16px 10px; }}
.tn-hold-head {{ padding: 10px 0 4px; color: {MUTED}; font-size: .78rem; font-weight: 600; }}
.tn-hold-row {{ display: grid; grid-template-columns: minmax(0,1.1fr) 64px minmax(72px,1fr);
  gap: 8px; align-items: center; padding: 7px 0; border-top: none; }}
.tn-hold-name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; color: {TEXT}; }}
.tn-hold-pct {{ text-align: right; font-variant-numeric: tabular-nums; color: {TEXT}; font-size: .82rem; }}
.tn-hold-track {{ height: 6px; border-radius: 99px; background: {PANEL2}; overflow: hidden; }}
.tn-hold-bar {{ height: 100%; border-radius: 99px; background: {BLUE}; }}

footer {{ display: none !important; }}
[data-testid="stAppDeployButton"], .stDeployButton {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stElementContainer"]:has(iframe[height="1"]) {{
  height: 0 !important; min-height: 0 !important; margin: 0 !important;
  padding: 0 !important; overflow: hidden !important; }}

@media (max-width: 600px) {{
  div[style*="min-height:340px"] {{ min-height: 240px !important; }}
  div[style*="min-width:150px"] {{ min-width: 46% !important; }}
  div[style*="min-width:104px"] {{ min-width: 44% !important; }}
  .tn-tape {{ font-size: 0.7rem; }}
  .block-container {{ padding-top: 1.1rem; padding-left: .7rem; padding-right: .7rem;
                      padding-bottom: 1.6rem; }}
  h1 {{ font-size: 1.45rem !important; }}
  h2, h3 {{ font-size: 1.1rem !important; }}
  .tn-hero {{ gap: 12px; margin: 2px 0 12px; }}
  .tn-badge {{ width: 46px; height: 46px; font-size: .95rem; }}
  .tn-hero-name {{ font-size: 1.15rem; }}
  .tn-price {{ font-size: 1.65rem; }}
  .tn-gauge {{ padding: 12px 10px 14px; }}
  .tn-wl-row {{ grid-template-columns: 1fr auto auto; gap: 8px; }}
  .tn-wl-spark {{ display: none; }}
  [data-testid="stMetric"] {{ padding: 11px 14px 10px !important; border-radius: 16px; }}
  [data-testid="stMetricValue"] {{ font-size: 1.25rem !important; }}
  [data-testid="stMetricLabel"] p {{ font-size: .68rem !important; }}
  [data-testid="stMetricDelta"] {{ font-size: .78rem !important; }}
  [data-testid="stVerticalBlock"] {{ gap: .6rem !important; }}
  [data-testid="stHorizontalBlock"] {{ gap: .5rem !important; }}
  .stTabs [data-baseweb="tab"] {{ padding: 7px 10px; font-size: .84rem; }}
  .tn-etf-rets {{ gap: 6px; }}
  .tn-hold-row {{ grid-template-columns: minmax(0,1fr) 58px; }}
  .tn-hold-track {{ display: none; }}
  .tn-sl-row {{ grid-template-columns: 36px minmax(0, 1fr) auto; gap: 10px; padding: 10px 14px; }}
  .tn-sl-ava {{ width: 36px; height: 36px; font-size: .9rem; }}
  .tn-sl-head {{ padding: 14px 14px 6px; }}
  .tn-sl-memo {{ display: none; }}
}}

@media (max-width: 430px) {{
  [data-testid="stMetricValue"] {{ font-size: 1.12rem !important; }}
  [data-testid="stMetric"] {{ padding: 9px 11px 8px !important; }}
  .tn-price {{ font-size: 1.45rem; }}
  .tn-badge {{ width: 40px; height: 40px; font-size: .85rem; }}
  .block-container {{ padding-left: .55rem; padding-right: .55rem; }}
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
                   f"font-family:{_NUM_FONT};font-size:12px'>{px:,.0f}{_pct(px)}</td>")
        # 바 = 배경 absolute 레이어, 텍스트 = 전폭 오버레이 → 바가 좁아도 숫자는 절대 안 꺾임
        anchor = "right" if side == "ask" else "left"
        border = "left" if side == "ask" else "right"
        cell = (f"<td style='width:31%'><div style='position:relative;min-height:16px'>"
                f"<div style='position:absolute;top:1px;bottom:1px;{anchor}:0;width:{w}%;"
                f"background:{bar_color}26;border-{border}:2px solid {bar_color}'></div>"
                f"<div style='position:relative;text-align:{anchor};padding:1px 6px;"
                f"font-family:{_NUM_FONT};font-size:10.5px;color:{bar_color};"
                f"white-space:nowrap'>{qty}</div></div></td>")
        if side == "ask":
            return f"<tr>{cell}{px_cell}<td style='width:31%'></td></tr>"
        return f"<tr><td style='width:31%'></td>{px_cell}{cell}</tr>"

    rows = [_row(p, q, "ask") for p, q in sorted(asks, key=lambda x: -x[0])]
    if price:
        rows.append(f"<tr><td></td><td style='text-align:center;border:1px solid #e5e8ee55;"
                    f"border-radius:6px;padding:2px;font-family:{_NUM_FONT};font-weight:700;"
                    f"font-size:13px'>{float(price):,.0f}{_pct(float(price))}</td><td></td></tr>")
    rows += [_row(p, q, "bid") for p, q in bids]

    stat_rows = []
    def _stat(k, v, color=None):
        vv = f"{v:,.0f}" if isinstance(v, (int, float)) else (v or "—")
        c = f"color:{color}" if color else ""
        stat_rows.append(f"<div style='display:flex;justify-content:space-between;"
                         f"font-size:12px;padding:2px 0'><span style='color:{MUTED}'>{k}</span>"
                         f"<span style='font-family:{_NUM_FONT};{c}'>{vv}</span></div>")
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
        color = UP if (pct or 0) >= 0 else DOWN
        arrow = "▲" if (pct or 0) >= 0 else "▼"
        spans.append(
            f"<span style='margin:0 18px;white-space:nowrap'>"
            f"<b style='color:{TEXT}'>{it['label']}</b> "
            f"<span style='font-family:{_NUM_FONT}'>{it['value']:,}</span> "
            f"<span style='color:{color};font-family:{_NUM_FONT};font-size:11px'>"
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


def etf_return_strip_html(chips: list) -> str:
    """기간 수익률 칩 — [(라벨, 값%|None)]. 없음이면 ''. 상승 초록·하락 빨강."""
    rows = []
    for lab, val in chips or []:
        if val is None:
            num, col = "—", MUTED
        else:
            num, col = f"{float(val):+.1f}%", (UP if float(val) >= 0 else DOWN)
        rows.append(
            f'<div class="tn-etf-chip"><span>{lab}</span>'
            f'<b style="color:{col}">{num}</b></div>')
    if not rows:
        return ""
    return f'<div class="tn-etf-rets">{"".join(rows)}</div>'


def etf_holdings_html(rows: list, *, title: str = "구성 비중") -> str:
    """구성종목·섹터 비중 바. 가장 큰 비중이 트랙을 채운다. 없으면 ''."""
    items = []
    for row in rows or []:
        name = str((row or {}).get("name") or (row or {}).get("symbol") or "").strip()
        pct = (row or {}).get("pct")
        if not name or not isinstance(pct, (int, float)):
            continue
        items.append((name, float(pct)))
    if not items:
        return ""
    peak = max(abs(p) for _, p in items) or 1.0
    body = []
    for name, pct in items[:12]:
        width = max(4, min(100, abs(pct) / peak * 100))
        body.append(
            f'<div class="tn-hold-row"><span class="tn-hold-name">{name}</span>'
            f'<span class="tn-hold-pct">{pct:.2f}%</span>'
            f'<div class="tn-hold-track"><div class="tn-hold-bar" style="width:{width:.1f}%"></div></div>'
            f'</div>')
    return (f'<div class="tn-hold"><div class="tn-hold-head">{title}</div>{"".join(body)}</div>')


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
        return (f'<div style="padding:12px 14px;background:{PANEL};border:none;'
                f'border-radius:20px;text-align:center;color:{MUTED}">'
                f'점수 — <b>데이터 부족</b><br><span style="font-size:0.75rem">'
                f'이력·지표가 모자라 산출 생략 (정직 표시)</span></div>')
    s = max(1.0, min(100.0, float(score)))
    lab = etf_score_label(s)
    conf = (f' · <span style="color:{RED}">표본 부족</span>' if low_confidence else "")
    return f'''<div style="padding:10px 14px;background:{PANEL};border:none;border-radius:20px">
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
    return f'''<div style="background:{PANEL};border:none;border-left:4px solid {col};
  border-radius:20px;padding:16px 18px">
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
        return (f'<div style="padding:12px 14px;background:{PANEL};border:none;'
                f'border-radius:20px;text-align:center;color:{MUTED}">'
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
    return (f'<div style="padding:12px 14px;background:{PANEL};border:none;'
            f'border-radius:20px;min-height:340px;display:flex;flex-direction:column;">'
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
                f'font-family:{_NUM_FONT}">{val}</div>{extra}</div>')

    chip = ""
    if pct is not None:
        c = RED if pct >= 90 else AMBER if pct >= 70 else MUTED
        chip = (f'<span style="display:inline-block;margin-top:3px;padding:2px 9px;'
                f'border:1px solid {c}44;border-radius:999px;color:{c};font-size:0.7rem;'
                f'background:{c}14">1871~ {pct:.0f}백분위 · 20년 {pct20:.0f}백분위</span>')
    g_col = GREEN if (g or 0) >= 0 else RED
    peg_col = GREEN if (peg or 9) < 1 else (AMBER if (peg or 9) < 2 else RED)
    return (f'<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;'
            f'background:{PANEL};border:none;border-radius:16px;'
            f'padding:9px 8px">'
            + cell("S&P500 PER (보고이익)", f"{per:.1f}" if per else "—", chip)
            + cell("선행 PER (컨센서스)", f"{v.get('fper'):.1f}" if v.get("fper") else "—")
            + cell("EPS 성장률 (1년 예상)", f"{g:+.1f}%" if g is not None else "—", "", g_col)
            + cell("PEG (교과서식)", f"{peg:.2f}" if peg else "—", "", peg_col)
            + '</div>')
