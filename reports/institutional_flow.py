"""reports/institutional_flow.py — 기관 매집 추적 (Institutional Accumulation Tracker)

가격·거래량 방향성 기반 '매집 강도' 점수 + 미국 종목 13F 기관 지분 변동 교차검증.

배경:
    직접 기관 순매수(원/주) 데이터가 레포에 없다(yfinance 단일 소스, KRX/pykrx 미설치).
    그래서 '기관 매집'은 거래량 방향성 지표로 *추정*하고, 미국 종목은 분기 13F
    지분 변동으로 *교차검증*한다. KOSPI(.KS)는 13F가 없어 기술적 신호만 사용한다.

매집 강도 점수 (0~100, 기술적):
    - OBV(누적거래량) 20일 정규화 기울기      가중 0.35
    - CMF(차이킨 머니플로우) 20일             가중 0.30
    - 상승/하락일 거래량비 20일               가중 0.25
    - A/D(매집분산선) 50일 정규화 기울기      가중 0.10
    - 거래량 급증 동반 시 소폭 보너스 (+최대 6)
    네 지표 모두 "거래량이 매수 방향으로 쏠렸는가"를 서로 다른 각도로 측정한다.

미국 13F 보정 (상위 픽만):
    분기 지분 순증이면 가점(+최대 8), 순감이면 감점(-최대 6).
    신규 편입(pctChange=1.0)이 평균을 왜곡하지 않도록 ±0.5로 클립.

공개 API:
    compute_accumulation(ticker, hist) -> dict | None     # OHLCV → 기술적 매집 지표
    fetch_13f(ticker) -> dict | None                      # 미국 13F 기관 지분 변동
    rank_accumulation(tickers, ...) -> list[dict]         # 매집 강도 내림차순 랭킹
    accumulation_line(entry) -> str                       # 마크다운 표 한 줄
    accumulation_mobile_block(entries, title, limit) -> list[str]  # 모바일 .txt 블록
"""
from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 프로젝트 루트를 sys.path에 (단독 실행/테스트 대비)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 임계값/가중치 (한 곳에 모아 캘리브레이션 용이) ──────────────────────────
LOOKBACK = 20          # 단기 윈도우 (거래일)
LONG_LOOKBACK = 50     # A/D 추세 윈도우
MIN_BARS = LONG_LOOKBACK + 5
# 연 변동성 이 값 미만이면 현금성/초단기채(SGOV·SHY 등)로 보고 매집 분석 제외 —
# 매일 NAV가 우상향할 뿐이라 거래량 방향성 지표가 의미 없는 거짓 양성을 낸다.
MIN_VOL_ANNUAL = 0.03

_W_OBV, _W_CMF, _W_UD, _W_AD = 0.35, 0.30, 0.25, 0.10

# 매집 강도 → 판정 라벨 (점수 내림차순으로 평가)
VERDICT_STRONG = 75    # 강한 매집
VERDICT_ACCUM = 60     # 매집
VERDICT_NEUTRAL = 45   # 중립 (그 아래는 분산/매도)


# ── 지표 계산 헬퍼 ────────────────────────────────────────────────────────────
def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume — 종가 방향으로 부호 부여한 누적 거래량."""
    sign = np.sign(close.diff().fillna(0.0))
    return (sign * volume).cumsum()


def _money_flow_volume(high, low, close, volume) -> pd.Series:
    """Money Flow Volume — 일중 종가 위치 가중 거래량 (A/D·CMF 공통 입력)."""
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng   # -1(저가 마감) ~ +1(고가 마감)
    return mfm.fillna(0.0) * volume


def _map(x: float, lo: float, hi: float) -> float:
    """x ∈ [lo, hi] → [0, 100] 선형 매핑 (범위 밖은 클립)."""
    if hi == lo:
        return 50.0
    return float(np.clip((x - lo) / (hi - lo) * 100.0, 0.0, 100.0))


def _verdict(score: float) -> str:
    if score >= VERDICT_STRONG:
        return "강한 매집"
    if score >= VERDICT_ACCUM:
        return "매집"
    if score >= VERDICT_NEUTRAL:
        return "중립"
    return "분산"


# ── 기술적 매집 지표 ──────────────────────────────────────────────────────────
def compute_accumulation(ticker: str, hist: pd.DataFrame,
                         *, lookback: int = LOOKBACK,
                         long_lookback: int = LONG_LOOKBACK) -> dict | None:
    """OHLCV DataFrame → 기술적 매집 지표 dict. 데이터 부족 시 None.

    반환:
        {
          "ticker", "accum_score" (0~100), "verdict",
          "signals": {obv_norm, cmf, updown_ratio, ad_norm, vol_surge, price_chg_20d},
          "stealth" (bool),            # 가격 정체/하락 중 매집 = 조용한 매집
          "vol_surge_flag" (bool),
          "institutional": None,       # rank_accumulation 에서 미국 종목만 채움
        }
    """
    if hist is None or len(hist) < long_lookback + 5:
        return None
    df = hist.copy()
    if "Close" not in df or "Volume" not in df:
        return None
    df = df.dropna(subset=["Close", "Volume"])
    if len(df) < long_lookback + 5:
        return None

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    high = df["High"].astype(float) if "High" in df else close
    low = df["Low"].astype(float) if "Low" in df else close

    # 거래량이 0뿐이면 (상장폐지/데이터 결손) 판정 불가
    vol_recent = volume.iloc[-lookback:]
    if vol_recent.sum() <= 0:
        return None

    # 현금성/초단기채 제외 — 변동성이 사실상 없는 상품은 매집 분석 대상 아님
    daily_ret = close.pct_change().iloc[-long_lookback:]
    vol_annual = float(daily_ret.std() * np.sqrt(252)) if len(daily_ret.dropna()) > 5 else 0.0
    if vol_annual < MIN_VOL_ANNUAL:
        return None

    # 1) OBV 20일 정규화 변화 — 누적 거래량이 매수 방향으로 얼마나 쌓였나
    obv = _obv(close, volume)
    obv_chg = obv.iloc[-1] - obv.iloc[-1 - lookback]
    obv_norm = float(obv_chg / vol_recent.sum())

    # 2) CMF 20일 — 일중 종가 위치 가중 머니플로우
    mfv = _money_flow_volume(high, low, close, volume)
    cmf = float(mfv.iloc[-lookback:].sum() / vol_recent.sum())

    # 3) 상승/하락일 거래량비 20일 — 반드시 '최근 윈도우로 자른 뒤' 부호 필터.
    #    (필터 후 슬라이스하면 상승일·하락일이 서로 다른 달력 구간에서 뽑혀 비율이 무의미)
    chg = close.diff()
    recent_chg = chg.iloc[-lookback:]
    recent_vol = volume.iloc[-lookback:]
    up_vol = float(recent_vol[recent_chg > 0].sum())
    dn_vol = float(recent_vol[recent_chg < 0].sum())
    updown_ratio = (up_vol / dn_vol) if dn_vol > 0 else (3.0 if up_vol > 0 else 1.0)

    # 4) A/D 50일 정규화 기울기 — 중기 매집/분산 추세
    ad = mfv.cumsum()
    vol_long = volume.iloc[-long_lookback:].sum()
    ad_norm = float((ad.iloc[-1] - ad.iloc[-1 - long_lookback]) / vol_long) if vol_long > 0 else 0.0

    # 5) 거래량 급증 — 최근 5일 평균 vs 직전 기간 평균 (양방향 conviction 태그용)
    recent5 = float(volume.iloc[-5:].mean())
    base = float(volume.iloc[-long_lookback:-5].mean())
    vol_surge = (recent5 / base) if base > 0 else 1.0

    price_chg_20d = float((close.iloc[-1] / close.iloc[-1 - lookback] - 1.0) * 100.0)

    # ── 0~100 매핑 + 가중합 ──
    obv_s = _map(obv_norm, -0.25, 0.25)
    cmf_s = _map(cmf, -0.20, 0.20)
    ud_s = float(np.clip(50.0 + 50.0 * np.tanh(np.log(max(updown_ratio, 1e-6)) * 1.2), 0, 100))
    ad_s = _map(ad_norm, -0.15, 0.15)
    accum = _W_OBV * obv_s + _W_CMF * cmf_s + _W_UD * ud_s + _W_AD * ad_s

    # 거래량 급증이 매수 방향과 동반될 때만 소폭 가점 (분산/투매 급증은 가점 안 함)
    vol_surge_flag = vol_surge >= 1.3 and accum >= 55
    if vol_surge_flag:
        accum = min(100.0, accum + min(6.0, (vol_surge - 1.3) * 10.0))

    accum = round(float(accum), 1)
    # 조용한 매집: 매집 신호가 강한데 가격은 정체/하락 (기관이 조용히 모으는 패턴)
    stealth = accum >= VERDICT_ACCUM and price_chg_20d <= 2.0

    return {
        "ticker": ticker,
        "accum_score": accum,
        "verdict": _verdict(accum),
        "signals": {
            "obv_norm": round(obv_norm, 3),
            "cmf": round(cmf, 3),
            "updown_ratio": round(updown_ratio, 2),
            "ad_norm": round(ad_norm, 3),
            "vol_surge": round(vol_surge, 2),
            "price_chg_20d": round(price_chg_20d, 1),
        },
        "stealth": bool(stealth),
        "vol_surge_flag": bool(vol_surge_flag),
        "institutional": None,
    }


# ── 미국 13F 기관 지분 변동 ───────────────────────────────────────────────────
def fetch_13f(ticker: str) -> dict | None:
    """미국 종목 13F 기관 지분율·분기 순변동. .KS(한국) 및 실패 시 None.

    반환:
        {"held_pct", "net_change", "buyers", "sellers", "top_buyer", "as_of"}
        net_change: 상위 보유기관 주식수 가중 평균 분기 변동률 (±0.5 클립)
    """
    if not ticker or ticker.endswith(".KS"):
        return None
    try:
        import yfinance as yf
    except Exception:
        return None
    tk = yf.Ticker(ticker)

    held = None
    try:
        info = tk.info or {}
        held = info.get("heldPercentInstitutions")
    except Exception:
        info = {}

    ih = None
    try:
        ih = tk.institutional_holders
    except Exception:
        ih = None

    if ih is None or len(ih) == 0 or "pctChange" not in getattr(ih, "columns", []):
        if held is None:
            return None
        return {"held_pct": held, "net_change": None, "buyers": None, "sellers": None,
                "new_entrants": None, "top_buyer": None, "as_of": None}

    df = ih.copy()
    pc_raw = pd.to_numeric(df["pctChange"], errors="coerce").fillna(0.0)
    shares = pd.to_numeric(df.get("Shares"), errors="coerce").fillna(0.0)

    # yfinance 아티팩트: 신규/첫보고 기관은 pctChange=1.0 → 100% 증가는 의미 없는
    # 크기라 순변동 평균에서 제외하고 '신규 편입' 카운트로만 반영한다.
    new_mask = pc_raw >= 0.99
    exist = ~new_mask
    pc_exist = pc_raw[exist].clip(-0.5, 0.5)
    sh_exist = shares[exist]
    wsum = float(sh_exist.sum())
    if wsum > 0:
        net_change = float((pc_exist * sh_exist).sum() / wsum)
    elif len(pc_exist):
        net_change = float(pc_exist.mean())
    else:
        net_change = 0.0

    buyers = int((pc_raw[exist] > 0.005).sum())
    sellers = int((pc_raw < -0.005).sum())
    new_entrants = int(new_mask.sum())
    top_buyer = None
    try:
        cand = pc_raw[exist]
        if len(cand) and cand.max() > 0.005:
            top_buyer = str(df.loc[cand.idxmax(), "Holder"])
    except Exception:
        top_buyer = None
    as_of = None
    try:
        as_of = str(df["Date Reported"].iloc[0])[:10]
    except Exception:
        pass

    return {
        "held_pct": held,
        "net_change": round(net_change, 4),
        "buyers": buyers,
        "sellers": sellers,
        "new_entrants": new_entrants,
        "top_buyer": top_buyer,
        "as_of": as_of,
    }


def _institutional_adjustment(inst: dict | None) -> float:
    """13F 순변동 → 매집 점수 보정값 (+8 ~ -6).

    기존 보유기관의 주식수 가중 순변동(net_change)이 주축이고, 신규 대형
    기관 편입(new_entrants)은 소폭 가점한다. (신규 1.0 아티팩트는 fetch_13f
    에서 이미 net_change 평균에서 제외됨)
    """
    if not inst or inst.get("net_change") is None:
        return 0.0
    nc = inst["net_change"]
    new = inst.get("new_entrants") or 0
    base = nc * 120.0
    if nc >= 0:
        base += min(2.0, new * 1.0)           # 신규 대형 기관 편입 소폭 가점
    return float(np.clip(base, -6.0, 8.0))


# ── 유니버스 랭킹 ─────────────────────────────────────────────────────────────
def rank_accumulation(tickers, *, days: int = 160, enrich_top: int = 10,
                      limit: int = 12, min_score: float = VERDICT_ACCUM,
                      enrich: bool = True, price_fetcher=None) -> list[dict]:
    """티커 유니버스 → 매집 강도 내림차순 랭킹.

    1) 전 종목 기술적 매집 점수 산출 (가격은 fetch_prices 배치 캐시 재사용)
    2) 상위 enrich_top 개 미국 종목만 13F 교차검증 → 점수 보정 후 재정렬
    3) min_score 이상만 limit 개 반환

    Args:
        price_fetcher: {ticker: OHLCV DataFrame} 반환 함수 (테스트 주입용).
                       기본값은 ml.data_pipeline.fetch_prices.
        enrich: False 면 13F 호출 생략 (무네트워크 테스트/빠른 경로).
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]   # 중복 제거, 순서 유지
    if not tickers:
        return []

    if price_fetcher is None:
        from ml.data_pipeline import fetch_prices as price_fetcher

    try:
        prices = price_fetcher(tickers, days=days)
    except Exception as e:
        logger.warning("가격 로드 실패: %s", e)
        return []

    scored: list[dict] = []
    for t in tickers:
        try:
            m = compute_accumulation(t, prices.get(t))
        except Exception as e:
            logger.debug("매집 계산 실패 %s: %s", t, e)
            m = None
        if m:
            scored.append(m)

    scored.sort(key=lambda x: x["accum_score"], reverse=True)

    # 상위 픽만 13F 교차검증 (네트워크 비용 제한)
    if enrich:
        for m in scored[:enrich_top]:
            if m["ticker"].endswith(".KS"):
                continue
            try:
                inst = fetch_13f(m["ticker"])
            except Exception as e:
                logger.debug("13F 실패 %s: %s", m["ticker"], e)
                inst = None
            if inst:
                m["institutional"] = inst
                adj = _institutional_adjustment(inst)
                if adj:
                    m["accum_score"] = round(float(np.clip(m["accum_score"] + adj, 0, 100)), 1)
                    m["verdict"] = _verdict(m["accum_score"])
                    m["inst_adj"] = round(adj, 1)
        # 13F 보정으로 점수가 바뀌었으니 전체 재정렬 (슬라이스만 정렬하면 그룹 경계가 어긋남)
        scored.sort(key=lambda x: x["accum_score"], reverse=True)

    return [m for m in scored if m["accum_score"] >= min_score][:limit]


# ── 표기 헬퍼 ─────────────────────────────────────────────────────────────────
_VERDICT_EMOJI = {"강한 매집": "🟢", "매집": "🔵", "중립": "⚪", "분산": "🔴"}


def _accum_bar(score, width: int = 10) -> str:
    try:
        s = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return "▱" * width
    filled = round(s / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def _inst_phrase(inst: dict | None) -> str:
    """13F 기관 지분 한 줄 요약 (없으면 빈 문자열)."""
    if not inst:
        return ""
    parts = []
    if inst.get("held_pct") is not None:
        parts.append(f"기관지분 {inst['held_pct'] * 100:.0f}%")
    nc = inst.get("net_change")
    if nc is not None:
        arrow = "▲" if nc > 0.002 else ("▼" if nc < -0.002 else "—")
        seg = f"분기 {arrow}{abs(nc) * 100:.1f}%"
        if inst.get("buyers") is not None:
            seg += f"(매수 {inst['buyers']}·매도 {inst['sellers']}"
            if inst.get("new_entrants"):
                seg += f"·신규 {inst['new_entrants']}"
            seg += ")"
        parts.append(seg)
    return " · ".join(parts)


def accumulation_line(entry: dict, *, name_fn=None) -> str:
    """마크다운 표 한 줄: | 종목 | 매집 | 강도 | OBV | CMF | 상승/하락 | 13F |"""
    t = entry["ticker"]
    name = name_fn(t) if name_fn else t
    sig = entry.get("signals", {})
    label = f"{t} — {name}" if name and name != t else t
    tag = " 🤫조용한매집" if entry.get("stealth") else ""
    tag += " 📊거래량급증" if entry.get("vol_surge_flag") else ""
    inst = _inst_phrase(entry.get("institutional")) or "—"
    return (f"| {label}{tag} | {_VERDICT_EMOJI.get(entry['verdict'], '⚪')} {entry['verdict']} "
            f"| {entry['accum_score']:.0f} | {sig.get('obv_norm', 0):+.2f} | {sig.get('cmf', 0):+.2f} "
            f"| {sig.get('updown_ratio', 0):.1f} | {inst} |")


def accumulation_mobile_block(entries, title: str = "🏛️ 기관 매집", limit: int = 3,
                              *, name_fn=None) -> list[str]:
    """모바일 .txt 블록: 제목 + 종목별 게이지 바 + 판정/근거 멀티라인."""
    lines = [title]
    if not entries:
        lines.append("  없음")
        return lines
    for e in entries[:limit]:
        t = e["ticker"]
        name = name_fn(t) if name_fn else None
        label = f"{t}({name})" if name and name != t else t
        emoji = _VERDICT_EMOJI.get(e["verdict"], "⚪")
        lines.append(f"{emoji} {label} {e['accum_score']:.0f}점 {_accum_bar(e['accum_score'])}")
        bits = [e["verdict"]]
        if e.get("stealth"):
            bits.append("🤫조용한매집")
        if e.get("vol_surge_flag"):
            bits.append("📊거래량급증")
        inst = _inst_phrase(e.get("institutional"))
        if inst:
            bits.append(inst)
        lines.append("    " + " · ".join(bits))
    return lines


def clean_entry(entry: dict, *, name_fn=None) -> dict:
    """clean_data/JSON 페이로드용 슬림 dict."""
    t = entry["ticker"]
    return {
        "ticker": t,
        "company": name_fn(t) if name_fn else t,
        "accum_score": entry["accum_score"],
        "verdict": entry["verdict"],
        "stealth": entry.get("stealth", False),
        "vol_surge": entry.get("vol_surge_flag", False),
        "signals": entry.get("signals", {}),
        "institutional": entry.get("institutional"),
    }


if __name__ == "__main__":   # 수동 점검: python reports/institutional_flow.py MSFT NVDA ...
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    universe = sys.argv[1:] or ["MSFT", "NVDA", "GOOGL", "ORCL", "AMD", "INTC", "PFE"]  # ticker-ok 수동 점검용 데모
    ranked = rank_accumulation(universe, limit=20, min_score=0)
    print(f"\n매집 강도 랭킹 ({len(ranked)}종목):\n")
    for e in ranked:
        print(accumulation_line(e))
