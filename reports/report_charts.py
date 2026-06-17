"""reports/report_charts.py — 일일 리포트 시각화 (포트폴리오 대시보드 PNG)

텔레그램으로 sendPhoto 하는 한 장짜리 대시보드 이미지를 생성한다. 텍스트 게이지보다
직관적인 '그래프'를 원한다는 요구에 맞춰 matplotlib(Agg)로 4분할 패널을 그린다:

  ① 보유 종목 등락률 비교 (1일·1개월 diverging 막대)
  ② 1개월 정규화 추이 — 포트폴리오(동일가중) vs SPY vs QQQ (라인)
  ③ 종목별 RSI(14) — 30/70 과매도·과매수 밴드
  ④ 기관 매집 강도 (없으면 펀더멘털 점수로 폴백)

설계 원칙:
- 헤드리스 서버: matplotlib.use("Agg"). 한글 폰트(WenQuanYi Zen Hei 등) 자동 탐색·등록,
  없으면 영문 라벨로 폴백(tofu 방지).
- 각 패널은 독립 try/except — 한 패널이 실패해도 나머지는 그려진다.
- 데이터/그리기 전체 실패 시 None 반환 → 호출부(리포트)는 그래프 없이도 정상 발송.

공개 API:
    build_portfolio_dashboard(clean_data, market, out_path, *, price_history=None,
                              accum_picks=None, date_str=None) -> str | None
"""
from __future__ import annotations

import logging
import os
import sys

import matplotlib
matplotlib.use("Agg")   # 헤드리스 — pyplot import 전에 백엔드 고정
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 색상 팔레트 ───────────────────────────────────────────────────────────────
_UP = "#21a366"        # 상승/긍정 (녹)
_DOWN = "#e5484d"      # 하락/부정 (적)
_UP_SOFT = "#7bc8a4"
_DOWN_SOFT = "#f0a3a5"
_INK = "#1f2933"
_MUTED = "#8895a7"
_GRID = "#e3e8ef"
_BG = "#ffffff"
_ACCENT = "#2b6cb0"    # 포트폴리오 라인
_BENCH1 = "#dd8a2b"    # SPY
_BENCH2 = "#9b6dd6"    # QQQ
_VERDICT_COLOR = {"강한 매집": _UP, "매집": "#3a9d78", "중립": _MUTED, "분산": _DOWN}

_KO_OK = False   # 한글 폰트 등록 성공 여부 (라벨 폴백 결정)


def _setup_font() -> bool:
    """한글 지원 폰트를 찾아 matplotlib 기본 폰트로 등록. 성공 시 True."""
    global _KO_OK
    from matplotlib import font_manager as fm
    candidates = [
        "NanumGothic", "NanumBarunGothic", "Malgun Gothic", "AppleGothic",
        "Noto Sans CJK KR", "Noto Sans KR", "WenQuanYi Zen Hei", "Unifont",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False   # 마이너스 부호 깨짐 방지
            _KO_OK = name not in ("Unifont",)  # Unifont는 비트맵이라 품질 낮음→라벨 최소화
            logger.info("차트 폰트: %s (한글=%s)", name, _KO_OK)
            return True
    plt.rcParams["axes.unicode_minus"] = False
    logger.warning("한글 폰트 미발견 — 영문 라벨로 폴백")
    return False


def _ko(ko: str, en: str) -> str:
    """한글 폰트 가능하면 한글, 아니면 영문 라벨."""
    return ko if _KO_OK else en


def _rsi(close, period: int = 14):
    """Wilder RSI — pandas Series 입력, 마지막 값 반환 (실패 시 None)."""
    try:
        import pandas as pd
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        ag = gain.ewm(alpha=1 / period, min_periods=period).mean()
        al = loss.ewm(alpha=1 / period, min_periods=period).mean()
        # 손실=0(단조 상승)이면 rs=inf → RSI=100 (NaN 으로 만들지 않는다)
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = ag / al
            rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        if val == val:                       # not NaN
            return float(val)
        # 둘 다 0(완전 보합)인 경우만 NaN — 판정 불가
        last_gain, last_loss = ag.iloc[-1], al.iloc[-1]
        if last_loss == 0 and last_gain and last_gain > 0:
            return 100.0
        if last_gain == 0 and last_loss and last_loss > 0:
            return 0.0
        return None
    except Exception:
        return None


def _style_axes(ax):
    ax.set_facecolor(_BG)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.grid(axis="x", color=_GRID, linewidth=0.8, alpha=0.7)


# ── 패널 ① 보유 종목 등락률 비교 ──────────────────────────────────────────────
def _panel_returns(ax, holdings):
    rows = [(h["ticker"], h.get("change_1d_pct"), h.get("change_1mo_pct"))
            for h in holdings
            if h.get("change_1d_pct") is not None or h.get("change_1mo_pct") is not None]
    if not rows:
        raise ValueError("등락률 데이터 없음")
    rows.sort(key=lambda r: (r[2] if r[2] is not None else r[1] or 0))
    tickers = [r[0] for r in rows]
    d1 = [r[1] or 0 for r in rows]
    mo = [r[2] or 0 for r in rows]
    y = np.arange(len(tickers))
    h = 0.38
    ax.barh(y + h / 2, mo, height=h, color=[_UP if v >= 0 else _DOWN for v in mo],
            label=_ko("1개월", "1M"), zorder=3)
    ax.barh(y - h / 2, d1, height=h, color=[_UP_SOFT if v >= 0 else _DOWN_SOFT for v in d1],
            label=_ko("1일", "1D"), zorder=3)
    ax.axvline(0, color=_MUTED, linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(tickers, fontsize=9, color=_INK)
    ax.set_title(_ko("보유 종목 등락률 (1일·1개월)", "Holdings Return (1D / 1M)"),
                 fontsize=12, color=_INK, fontweight="bold", pad=10)
    ax.set_xlabel("%", color=_MUTED, fontsize=9)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    _style_axes(ax)
    # 1개월 값 라벨
    for yi, v in zip(y + h / 2, mo):
        ax.text(v + (0.4 if v >= 0 else -0.4), yi, f"{v:+.1f}",
                va="center", ha="left" if v >= 0 else "right",
                fontsize=7.5, color=_INK)


# ── 패널 ② 1개월 정규화 추이 (포트 vs 벤치마크) ───────────────────────────────
def _panel_benchmark(ax, price_history, holdings):
    import pandas as pd
    win = 22  # 약 1개월(거래일)
    held = [h["ticker"] for h in holdings if h.get("ticker") in (price_history or {})]
    # 동일가중 포트폴리오 정규화 지수
    norm_cols = []
    for t in held:
        s = price_history[t].get("Close")
        if s is None or len(s.dropna()) < win:
            continue
        s = s.dropna().iloc[-win:]
        norm_cols.append((s / s.iloc[0] - 1.0) * 100.0)
    if not norm_cols:
        raise ValueError("벤치마크용 가격 없음")
    port = pd.concat(norm_cols, axis=1, sort=True).mean(axis=1)
    ax.plot(range(len(port)), port.values, color=_ACCENT, linewidth=2.4,
            label=_ko("내 포트(동일가중)", "Portfolio (EW)"), zorder=5)
    for sym, color, label in (("SPY", _BENCH1, "SPY"), ("QQQ", _BENCH2, "QQQ")):
        s = (price_history or {}).get(sym, {})
        s = s.get("Close") if hasattr(s, "get") else None
        if s is None or len(s.dropna()) < win:
            continue
        s = s.dropna().iloc[-win:]
        norm = (s / s.iloc[0] - 1.0) * 100.0
        ax.plot(range(len(norm)), norm.values, color=color, linewidth=1.6,
                label=label, alpha=0.9)
    ax.axhline(0, color=_MUTED, linewidth=1, linestyle="--", alpha=0.6)
    ax.set_title(_ko("1개월 추이: 포트 vs 벤치마크", "1M Trend: Portfolio vs Benchmark"),
                 fontsize=12, color=_INK, fontweight="bold", pad=10)
    ax.set_ylabel("%", color=_MUTED, fontsize=9)
    ax.set_xlabel(_ko("최근 거래일", "trading days"), color=_MUTED, fontsize=9)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    _style_axes(ax)


# ── 패널 ③ 종목별 RSI(14) ────────────────────────────────────────────────────
def _panel_rsi(ax, price_history, holdings):
    rows = []
    for h in holdings:
        t = h.get("ticker")
        df = (price_history or {}).get(t)
        close = df.get("Close") if hasattr(df, "get") else None
        if close is None:
            continue
        val = _rsi(close.dropna())
        if val is not None:
            rows.append((t, val))
    if not rows:
        raise ValueError("RSI 데이터 없음")
    rows.sort(key=lambda r: r[1])
    tickers = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    y = np.arange(len(tickers))

    def _color(v):
        if v >= 70:
            return _DOWN          # 과매수 (주의)
        if v <= 30:
            return _UP            # 과매도 (기회)
        return _ACCENT

    ax.axvspan(0, 30, color=_UP, alpha=0.07, zorder=0)
    ax.axvspan(70, 100, color=_DOWN, alpha=0.07, zorder=0)
    ax.barh(y, vals, color=[_color(v) for v in vals], height=0.6, zorder=3)
    ax.axvline(30, color=_UP, linewidth=0.9, linestyle="--", alpha=0.6)
    ax.axvline(70, color=_DOWN, linewidth=0.9, linestyle="--", alpha=0.6)
    ax.set_xlim(0, 100)
    ax.set_yticks(y)
    ax.set_yticklabels(tickers, fontsize=9, color=_INK)
    ax.set_title(_ko("종목별 RSI(14) — 과매도30·과매수70", "RSI(14) — oversold 30 / overbought 70"),
                 fontsize=12, color=_INK, fontweight="bold", pad=10)
    _style_axes(ax)
    for yi, v in zip(y, vals):
        ax.text(v + 1.5, yi, f"{v:.0f}", va="center", ha="left", fontsize=7.5, color=_INK)


# ── 패널 ④ 기관 매집 강도 (없으면 펀더멘털 점수) ──────────────────────────────
def _panel_accum_or_score(ax, accum_picks, holdings):
    if accum_picks:
        # 라벨: 미국은 티커(간결), 한국은 한글명(가독). 길면 자른다.
        def _accum_label(e):
            t = e.get("ticker", "")
            if t.endswith(".KS"):
                return (e.get("company") or t)[:14]
            return t
        rows = [(_accum_label(e), e["accum_score"], e.get("verdict", "중립"))
                for e in accum_picks][:8]
        rows.sort(key=lambda r: r[1])
        labels = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        colors = [_VERDICT_COLOR.get(r[2], _MUTED) for r in rows]
        title = _ko("기관 매집 강도 (상위)", "Institutional Accumulation")
        xmax = 100
    else:
        rows = [(h["ticker"], h.get("score")) for h in holdings if h.get("score") is not None]
        if not rows:
            raise ValueError("점수 데이터 없음")
        rows.sort(key=lambda r: r[1])
        labels = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        colors = [_UP if v >= 70 else (_ACCENT if v >= 50 else _DOWN) for v in vals]
        title = _ko("종목별 펀더멘털 점수", "Fundamental Score")
        xmax = 100
    y = np.arange(len(labels))
    ax.barh(y, vals, color=colors, height=0.62, zorder=3)
    ax.set_xlim(0, xmax)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8.5, color=_INK)
    ax.set_title(title, fontsize=12, color=_INK, fontweight="bold", pad=10)
    _style_axes(ax)
    for yi, v in zip(y, vals):
        ax.text(v + 1.5, yi, f"{v:.0f}", va="center", ha="left", fontsize=7.5, color=_INK)


def build_portfolio_dashboard(clean_data, market, out_path, *, price_history=None,
                              accum_picks=None, date_str=None) -> str | None:
    """포트폴리오 대시보드 PNG 생성. 성공 시 경로, 실패 시 None."""
    try:
        holdings = (clean_data or {}).get("portfolio_summary") or []
        if not holdings:
            logger.warning("대시보드: 포트폴리오 데이터 없음 — 생성 생략")
            return None
        if accum_picks is None:
            accum_picks = (clean_data or {}).get("institutional_accumulation") or []

        _setup_font()

        # 가격 히스토리 미제공 시 직접 로드 (보유 + SPY/QQQ)
        if price_history is None:
            try:
                from ml.data_pipeline import fetch_prices
                tickers = [h["ticker"] for h in holdings if h.get("ticker")] + ["SPY", "QQQ"]
                price_history = fetch_prices(list(dict.fromkeys(tickers)), days=90)
            except Exception as e:
                logger.warning("대시보드: 가격 로드 실패(%s) — 가격 기반 패널 생략", e)
                price_history = {}

        fig, axes = plt.subplots(2, 2, figsize=(12.0, 9.2), dpi=110)
        fig.patch.set_facecolor(_BG)
        title = _ko("포트폴리오 대시보드", "Portfolio Dashboard")
        suffix = f" — {date_str}" if date_str else ""
        # matplotlib 폰트는 컬러 이모지 미지원 → 제목에 이모지 넣지 않음(tofu 방지)
        fig.suptitle(f"{title}{suffix}", fontsize=15, color=_INK,
                     fontweight="bold", y=0.985)

        panels = [
            (axes[0][0], _panel_returns, (holdings,)),
            (axes[0][1], _panel_benchmark, (price_history, holdings)),
            (axes[1][0], _panel_rsi, (price_history, holdings)),
            (axes[1][1], _panel_accum_or_score, (accum_picks, holdings)),
        ]
        drawn = 0
        for ax, fn, fnargs in panels:
            try:
                fn(ax, *fnargs)
                drawn += 1
            except Exception as e:
                logger.warning("대시보드 패널 %s 실패: %s", fn.__name__, e)
                ax.text(0.5, 0.5, _ko("데이터 부족", "no data"), ha="center", va="center",
                        color=_MUTED, fontsize=11, transform=ax.transAxes)
                _style_axes(ax)
        if drawn == 0:
            plt.close(fig)
            logger.warning("대시보드: 모든 패널 실패 — 생성 생략")
            return None

        fig.text(0.5, 0.005,
                 _ko("yfinance 기반 자동 생성 · 참고용", "auto-generated from yfinance · reference only"),
                 ha="center", color=_MUTED, fontsize=8)
        fig.tight_layout(rect=(0, 0.02, 1, 0.96))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, facecolor=_BG, bbox_inches="tight")
        plt.close(fig)
        logger.info("대시보드 저장: %s (패널 %d/4)", out_path, drawn)
        return out_path
    except Exception as e:
        logger.warning("대시보드 생성 실패: %s", e)
        try:
            plt.close("all")
        except Exception:
            pass
        return None


if __name__ == "__main__":   # 수동 점검: 최근 summary JSON 으로 대시보드 생성
    import json
    import glob
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    files = sorted(glob.glob(os.path.expanduser("~/reports/investment-summary-*.json")))
    if not files:
        print("summary JSON 없음 — 먼저 리포트를 생성하세요")
        sys.exit(1)
    cd = json.load(open(files[-1], encoding="utf-8"))
    out = os.path.expanduser("~/reports/_dashboard_test.png")
    p = build_portfolio_dashboard(cd, cd.get("market_summary", {}), out,
                                  date_str=cd.get("date"))
    print("생성:", p)
