"""reports/report_pdf_charts.py — 데일리 PDF 리포트의 그림(벡터 SVG).

모든 그림은 숫자 입력만 받아 같은 양식으로 그린다. 글자는 번들 폰트(Pretendard)를 곡선으로
바꿔 넣으므로 서버에 한글 폰트가 없어도 같은 결과가 나온다. 반환값은 <img src> 에 그대로 넣는
data URI 이고, 그릴 데이터가 없으면 None 이다(템플릿은 그 그림 자리를 비운다).

색은 국내 관행을 따른다: 상승 빨강, 하락 파랑.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager, rc_context
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_FAMILY = "Pretendard"

UP = "#d92d20"
DOWN = "#1d5fd1"
INK = "#1b2733"
MUTED = "#6b7785"
GRID = "#e6eaef"
NAVY = "#0b2545"
ACCENT = "#c8963e"
SERIES = ("#0b2545", "#c8963e", "#5b8def", "#7a8794")
PALETTE = ("#0b2545", "#1d4e89", "#3a7ca5", "#6ba3c8", "#9cc5dd", "#c8963e", "#dcb46f",
           "#8a9aa8", "#b4bfc9", "#566270", "#2f6f5e", "#77a892")

_fonts_ready = False


def _ensure_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    for path in sorted(FONT_DIR.glob("*.otf")):
        font_manager.fontManager.addfont(str(path))
    _fonts_ready = True


def _rc() -> dict:
    return {
        "font.family": [FONT_FAMILY, "DejaVu Sans"], "font.size": 7.0,
        "svg.fonttype": "path", "svg.hashsalt": "stock-report-pdf",
        "axes.unicode_minus": False, "axes.edgecolor": GRID, "axes.linewidth": 0.6,
        "axes.labelcolor": MUTED, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 6.5, "ytick.labelsize": 6.5,
        "xtick.major.size": 0, "ytick.major.size": 0, "xtick.major.pad": 3, "ytick.major.pad": 3,
        "grid.color": GRID, "grid.linewidth": 0.5, "legend.fontsize": 6.5, "legend.frameon": False,
    }


def _figure(width_mm: float, height_mm: float) -> Figure:
    return Figure(figsize=(width_mm / 25.4, height_mm / 25.4))


def _encode(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", transparent=True, metadata={"Date": None})
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _clean_axes(ax, *, grid: str = "y") -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.set_axisbelow(True)
    if grid:
        ax.grid(True, axis=grid)


def _margins(size: tuple[float, float], *, left: float = 9.0, right: float = 3.0,
             top: float = 5.5, bottom: float = 5.0) -> dict:
    """여백을 mm 로 받아 figure 비율로. 그림 크기가 달라도 눈금·범례 자리가 같다."""
    width, height = size
    return {"left": left / width, "right": 1 - right / width, "top": 1 - top / height, "bottom": bottom / height}


def _rect(size: tuple[float, float], **mm) -> tuple[float, float, float, float]:
    m = _margins(size, **mm)
    return (m["left"], m["bottom"], m["right"] - m["left"], m["top"] - m["bottom"])


def _legend_above(ax, *, ncols: int) -> None:
    ax.legend(loc="lower left", bbox_to_anchor=(-0.01, 1.0), ncols=ncols, handlelength=1.3,
              columnspacing=1.0, handletextpad=0.4, borderaxespad=0.15)


def _month_ticks(index) -> tuple[list[int], list[str]]:
    """거래일 순번 x축에 월 첫 거래일만 눈금으로. 주말 공백이 생기지 않는다."""
    positions, labels, seen = [], [], None
    for i, ts in enumerate(index):
        key = (ts.year, ts.month)
        if key != seen:
            seen = key
            if i:
                positions.append(i)
                labels.append(f"{ts.year % 100:02d}.{ts.month:02d}")
    step = max(1, len(positions) // 7 + (1 if len(positions) % 7 else 0)) if len(positions) > 7 else 1
    return positions[::step], labels[::step]


def _sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out


def price_chart(frame, *, sessions: int = 126, averages: tuple[int, ...] = (20, 60),
                reference: tuple[str, float] | None = None, size: tuple[float, float] = (88, 52)) -> str | None:
    """종가 + 이동평균 + 거래량. reference 는 (라벨, 가격) 수평선(평균단가 등)."""
    try:
        closes_all = frame["Close"].dropna()
    except Exception:
        return None
    if len(closes_all) < 20:
        return None
    _ensure_fonts()
    values_all = [float(v) for v in closes_all.tolist()]
    closes = closes_all.iloc[-sessions:]
    values = values_all[-sessions:]
    offset = len(values_all) - len(values)
    x = list(range(len(values)))
    with rc_context(_rc()):
        fig = _figure(*size)
        grid = fig.add_gridspec(2, 1, height_ratios=(4, 1), hspace=0.06, **_margins(size, right=10.5))
        ax = fig.add_subplot(grid[0])
        vol_ax = fig.add_subplot(grid[1], sharex=ax)
        ax.fill_between(x, values, min(values), color=NAVY, alpha=0.06, linewidth=0)
        ax.plot(x, values, color=NAVY, linewidth=1.1, label="종가")
        for window, color in zip(averages, (ACCENT, "#5b8def", "#8a9aa8")):
            line = _sma(values_all, window)[offset:]
            if any(v is not None for v in line):
                ax.plot(x, line, color=color, linewidth=0.8, label=f"MA{window}")
        if reference and reference[1] and min(values) * 0.7 <= reference[1] <= max(values) * 1.3:
            ax.axhline(reference[1], color=MUTED, linewidth=0.7, linestyle=(0, (3, 2)), label=reference[0])
        ax.scatter([x[-1]], [values[-1]], s=9, color=NAVY, zorder=5)
        ax.annotate(f"{values[-1]:,.2f}", (x[-1], values[-1]), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=6.2, fontweight="bold", color="white", annotation_clip=False,
                    bbox={"boxstyle": "square,pad=0.28", "fc": NAVY, "ec": "none"})
        ax.margins(x=0.01)
        _clean_axes(ax)
        ax.tick_params(labelbottom=False)
        _legend_above(ax, ncols=5)
        try:
            volumes = [float(v or 0.0) for v in frame["Volume"].reindex(closes.index).fillna(0.0).tolist()]
        except Exception:
            volumes = []
        if any(volumes):
            colors = [UP if i == 0 or values[i] >= values[i - 1] else DOWN for i in range(len(values))]
            vol_ax.bar(x, volumes, color=colors, width=0.8, alpha=0.55, linewidth=0)
        vol_ax.set_yticks([])
        _clean_axes(vol_ax, grid="")
        ticks, labels = _month_ticks(closes.index)
        vol_ax.set_xticks(ticks, labels)
        return _encode(fig)


def relative_chart(series: list[tuple[str, list[float]]], index, *, size: tuple[float, float] = (88, 52)) -> str | None:
    """여러 시계열을 시작=100 으로 맞춘 비교선. series 는 (라벨, 값) — 길이는 index 와 같다."""
    series = [(label, values) for label, values in series if len(values) == len(index) and len(values) >= 5 and values[0]]
    if not series:
        return None
    _ensure_fonts()
    x = list(range(len(index)))
    with rc_context(_rc()):
        fig = _figure(*size)
        ax = fig.add_axes(_rect(size, right=3))
        ax.axhline(100, color=MUTED, linewidth=0.5)
        for (label, values), color in zip(series, SERIES):
            norm = [v / values[0] * 100.0 for v in values]
            ax.plot(x, norm, color=color, linewidth=1.3 if color == NAVY else 0.95,
                    label=f"{label}  {norm[-1] - 100:+.1f}%")
            ax.scatter([x[-1]], [norm[-1]], s=8, color=color, zorder=5)
        ax.margins(x=0.01)
        _clean_axes(ax)
        _legend_above(ax, ncols=3)
        ticks, labels = _month_ticks(index)
        ax.set_xticks(ticks, labels)
        return _encode(fig)


def bar_chart(rows: list[tuple[str, float]], *, size: tuple[float, float] = (88, 52), unit: str = "%",
              signed: bool = True, limit: float | None = None) -> str | None:
    """가로 막대. signed 면 부호로 색을 정하고 0 기준선을 그린다."""
    rows = [(label, float(value)) for label, value in rows if value is not None]
    if not rows:
        return None
    _ensure_fonts()
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    y = list(range(len(rows)))
    span = max([abs(v) for v in values] + [1e-9])
    with rc_context(_rc()):
        fig = _figure(*size)
        label_mm = min(30.0, 3.0 + max(sum(2.3 if ord(ch) > 0x2E7F else 1.25 for ch in label) for label in labels))
        ax = fig.add_axes(_rect(size, left=label_mm, right=3, top=1.5, bottom=1.5))
        ax.barh(y, values, color=[(UP if v >= 0 else DOWN) if signed else NAVY for v in values],
                height=0.62, linewidth=0)
        pad = span * 0.03
        for i, v in enumerate(values):
            ax.text(v + (pad if v >= 0 else -pad), i, f"{v:+.1f}{unit}" if signed else f"{v:.0f}{unit}",
                    va="center", ha="left" if v >= 0 else "right", fontsize=6.3, color=INK)
        ax.set_yticks(y, labels, fontsize=6.8, color=INK)
        ax.invert_yaxis()
        if signed:
            ax.axvline(0, color=MUTED, linewidth=0.6)
            ax.set_xlim(-span * 1.28 if min(values) < 0 else 0, span * 1.28)
        else:
            ax.set_xlim(0, limit or span * 1.15)
        ax.set_xticks([])
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        return _encode(fig)


def donut_chart(rows: list[tuple[str, float]], *, size: tuple[float, float] = (88, 56),
                center: tuple[str, str] | None = None, max_slices: int = 9) -> str | None:
    """비중 도넛. 작은 조각은 '기타'로 묶고 범례에 비중을 적는다."""
    rows = sorted(((label, float(v)) for label, v in rows if v and v > 0), key=lambda r: -r[1])
    if not rows:
        return None
    if len(rows) > max_slices:
        rows = rows[:max_slices - 1] + [("기타", sum(v for _, v in rows[max_slices - 1:]))]
    total = sum(v for _, v in rows)
    _ensure_fonts()
    with rc_context(_rc()):
        fig = _figure(*size)
        side = size[1] / size[0]
        ax = fig.add_axes((0.01, 0.04, side * 0.92, 0.92))
        colors = [PALETTE[i % len(PALETTE)] for i in range(len(rows))]
        ax.pie([v for _, v in rows], colors=colors, startangle=90, counterclock=False,
               wedgeprops={"width": 0.36, "edgecolor": "white", "linewidth": 0.8})
        if center:
            ax.text(0, 0.10, center[0], ha="center", va="center", fontsize=6.5, color=MUTED)
            ax.text(0, -0.14, center[1], ha="center", va="center", fontsize=9.5, fontweight="bold", color=INK)
        legend = fig.add_axes((side * 0.95 + 0.02, 0.04, 1 - side * 0.95 - 0.03, 0.92))
        legend.set_axis_off()
        legend.set_xlim(0, 1)
        legend.set_ylim(0, 1)
        step = min(0.105, 0.96 / len(rows))
        top = 0.5 + step * (len(rows) - 1) / 2
        for i, ((label, value), color) in enumerate(zip(rows, colors)):
            yy = top - i * step
            legend.add_patch(Rectangle((0.0, yy - 0.022), 0.05, 0.044, color=color, linewidth=0))
            legend.text(0.09, yy, label, va="center", fontsize=6.8, color=INK)
            legend.text(1.0, yy, f"{value / total * 100:.1f}%", va="center", ha="right",
                        fontsize=6.8, fontweight="bold", color=INK)
        return _encode(fig)


def equity_chart(history: list[dict], *, size: tuple[float, float] = (88, 52)) -> str | None:
    """평가액 추이(입출금 포함). history 는 {date, total_usd} 목록."""
    points = [(str(r["date"]), float(r["total_usd"])) for r in history if r.get("total_usd")]
    if len(points) < 5:
        return None
    _ensure_fonts()
    x = list(range(len(points)))
    values = [p[1] for p in points]
    with rc_context(_rc()):
        fig = _figure(*size)
        ax = fig.add_axes(_rect(size, right=3, top=3))
        floor = min(values) - (max(values) - min(values)) * 0.08
        ax.fill_between(x, values, floor, color=NAVY, alpha=0.08, linewidth=0)
        ax.plot(x, values, color=NAVY, linewidth=1.2)
        ax.scatter([x[-1]], [values[-1]], s=9, color=NAVY, zorder=5)
        ax.annotate(f"${values[-1]:,.0f}", (x[-1], values[-1]), xytext=(-2, 6), textcoords="offset points",
                    ha="right", fontsize=6.8, fontweight="bold", color=NAVY)
        ax.set_ylim(bottom=floor)
        ax.yaxis.set_major_formatter(lambda v, _pos: f"{v:,.0f}")
        ax.margins(x=0.01)
        _clean_axes(ax)
        ticks, labels, seen = [], [], None
        for i, (day, _value) in enumerate(points):
            if day[:7] != seen:
                seen = day[:7]
                if i:
                    ticks.append(i)
                    labels.append(f"{day[2:4]}.{day[5:7]}")
        ax.set_xticks(ticks, labels)
        return _encode(fig)


def histogram_chart(values: list[float], *, bins: tuple[int, ...] = (0, 20, 30, 40, 50, 60, 70, 80, 101),
                    size: tuple[float, float] = (88, 46)) -> str | None:
    """점수 분포. 구간은 왼쪽 닫힘."""
    values = [float(v) for v in values if v is not None]
    if len(values) < 5:
        return None
    _ensure_fonts()
    counts = [sum(1 for v in values if lo <= v < hi) for lo, hi in zip(bins, bins[1:])]
    labels = [f"{lo}–{min(hi, 100) - (0 if hi > 100 else 1)}" for lo, hi in zip(bins, bins[1:])]
    with rc_context(_rc()):
        fig = _figure(*size)
        ax = fig.add_axes((0.03, 0.14, 0.94, 0.80))
        x = list(range(len(counts)))
        colors = [NAVY if lo >= 70 else "#3a7ca5" if lo >= 50 else "#b4bfc9" for lo in bins[:-1]]
        ax.bar(x, counts, color=colors, width=0.72, linewidth=0)
        for i, count in enumerate(counts):
            ax.text(i, count + max(counts) * 0.02, str(count), ha="center", va="bottom", fontsize=6.5, color=INK)
        ax.set_xticks(x, labels)
        ax.set_yticks([])
        ax.set_ylim(0, max(counts) * 1.16 or 1)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        return _encode(fig)


def basket_series(prices: dict, weights: dict[str, float], *, sessions: int = 63, calendar: str = "SPY"):
    """현재 보유 비중을 고정한 바스켓의 과거 궤적 — (index, 값). 실제 계좌 수익률이 아니다.

    거래일은 calendar 심볼(비교 대상인 SPY)을 따른다. 시작값 100, 길이는 sessions + 1.
    """
    usable = {t: w for t, w in weights.items() if w and t in prices and len(prices[t]) > sessions}
    if not usable or calendar not in prices:
        return None, []
    index = prices[calendar]["Close"].dropna().index[-sessions - 1:]
    total_weight = sum(usable.values())
    values = [0.0] * len(index)
    for ticker, weight in usable.items():
        closes = prices[ticker]["Close"].reindex(index).ffill().bfill()
        base = float(closes.iloc[0])
        if not base:
            return None, []
        for i, close in enumerate(closes.tolist()):
            values[i] += weight / total_weight * float(close) / base * 100.0
    return index, values


def aligned(prices: dict, symbol: str, index) -> list[float]:
    if symbol not in prices or index is None:
        return []
    return [float(v) for v in prices[symbol]["Close"].reindex(index).ffill().bfill().tolist()]
