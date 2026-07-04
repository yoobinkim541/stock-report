#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/kr_market_data.py — KR 생존편향 제거 데이터층 (Phase A / §A).

데이터 실현성(2026.06 이 환경에서 실측):
  ✅ marcap (FinanceData/marcap 연도별 parquet, raw GitHub fetch) — 1995~ 전종목 시점별 시총패널.
     생존편향 0(상폐 종목도 거래 연도에 존재). OHLC·Marcap·Rank 포함. 연 5~23MB·즉시 캐시.
  ✅ FinanceDataReader `KRX-DELISTING` — 상폐 마스터(Symbol·Name·DelistingDate·**Reason**·Kind).
  ❌ pykrx (live KRX 스크레이프) — **이 서버에서 KRX 도달 불가**(빈 응답) → 투자자 수급·KOSPI200
     시점멤버십·KRX 펀더멘털 사용 불가. 펀더멘털은 yfinance .KS(부분)로 폴백.

→ 유니버스는 **marcap 시총 상위 N**(point-in-time)로 정의(사용자 승인). 코어(생존편향제거 30년 +
  퇴출 라벨/사유)는 marcap+FDR 로 완전 동작.

네트워크 함수(_marcap_year·delisting_master)는 캐시 + graceful. 파싱·분류·랭킹은 순수(테스트 가능).
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_MARCAP_DIR = Path(os.path.expanduser("~/reports/ml-cache/marcap"))
_MARCAP_RAW = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"
_DELIST_CACHE = Path(os.path.expanduser("~/reports/ml-cache/kr_delisting.parquet"))

MARCAP_MIN_YEAR = 1995


# ── 코드 정규화 ─────────────────────────────────────────────────────────────────

def norm_code(code) -> str:
    """marcap 구년도는 leading-zero 없는 코드(5930) → FDR/yfinance 호환 6자리(005930)."""
    s = str(code).strip().split(".")[0]
    return s.zfill(6) if s.isdigit() else s


def to_yf(code: str) -> str:
    """6자리 코드 → yfinance KR 티커(.KS). (KOSDAQ 은 .KQ 이나 marcap MarketId 로 구분 가능)"""
    c = norm_code(code)
    return f"{c}.KS"


# ── marcap 연도별 패널 (네트워크 + 캐시) ────────────────────────────────────────

_CURRENT_YEAR_MAX_AGE_S = 3 * 86400   # 당해연도 파일은 3일 지나면 재수신(주기 재검증 최신성)


def _marcap_year(year: int):
    """연도별 marcap parquet (캐시). 실패 시 None. (테스트는 이 함수를 monkeypatch.)

    과거 연도 = 불변이라 영구 캐시. **당해연도**는 3일 초과 시 재수신(kr_axes_eval 주간
    재검증이 갱신 데이터를 보도록) — 재수신 실패 시 기존 캐시 유지(graceful).
    """
    try:
        import time
        from datetime import datetime
        import pandas as pd
        _MARCAP_DIR.mkdir(parents=True, exist_ok=True)
        path = _MARCAP_DIR / f"marcap-{year}.parquet"
        stale_current = (path.exists() and year >= datetime.now().year
                         and time.time() - path.stat().st_mtime > _CURRENT_YEAR_MAX_AGE_S)
        if not path.exists() or stale_current:
            url = _MARCAP_RAW.format(y=year)
            tmp = path.with_suffix(".tmp")
            try:
                urllib.request.urlretrieve(url, tmp)
                os.replace(tmp, path)
                logger.info("marcap %s fetched (%dMB)", year, path.stat().st_size // 1048576)
            except Exception as fe:
                if not path.exists():
                    raise
                logger.warning("marcap %s 재수신 실패 — 기존 캐시 사용: %s", year, fe)
        df = pd.read_parquet(path)
        df["Code"] = df["Code"].map(norm_code)
        return df
    except Exception as e:
        logger.warning("marcap %s 로드 실패: %s", year, e)
        return None


def marcap_asof(date: str, *, market: str = "KOSPI"):
    """date('YYYY-MM-DD') 이하 마지막 거래일의 전종목 시총 스냅샷 DataFrame. 실패 시 None.

    survivorship-free: 그 시점에 거래되던 모든 종목(이후 상폐분 포함)을 반환.
    """
    try:
        import pandas as pd
        year = int(str(date)[:4])
        df = _marcap_year(year)
        if df is None or len(df) == 0:
            return None
        if market and "Market" in df.columns:
            df = df[df["Market"] == market]
        ts = pd.Timestamp(date)
        d = df[pd.to_datetime(df["Date"]) <= ts]
        if len(d) == 0:
            return None
        last_day = d["Date"].max()
        return d[d["Date"] == last_day].copy()
    except Exception as e:
        logger.warning("marcap_asof %s 실패: %s", date, e)
        return None


def top_n_by_marcap(date: str, n: int = 200, *, market: str = "KOSPI") -> list[str]:
    """시점 t 시총 상위 N 종목코드(6자리) — point-in-time 유니버스. 실패 시 []."""
    snap = marcap_asof(date, market=market)
    if snap is None or len(snap) == 0:
        return []
    snap = snap.dropna(subset=["Marcap"])
    return [norm_code(c) for c in snap.nlargest(n, "Marcap")["Code"].tolist()]


def ohlcv_from_marcap(code: str, start_year: int, end_year: int):
    """marcap 다년 종가 Series(상폐주 포함). 실패 시 None."""
    try:
        import pandas as pd
        code = norm_code(code)
        frames = []
        for y in range(max(MARCAP_MIN_YEAR, start_year), end_year + 1):
            df = _marcap_year(y)
            if df is None:
                continue
            sub = df[df["Code"] == code][["Date", "Close"]]
            if len(sub):
                frames.append(sub)
        if not frames:
            return None
        s = pd.concat(frames)
        s["Date"] = pd.to_datetime(s["Date"])
        return s.set_index("Date")["Close"].sort_index()
    except Exception as e:
        logger.warning("ohlcv_from_marcap %s 실패: %s", code, e)
        return None


# ── 상폐 마스터 (FDR — 라벨·사유) ───────────────────────────────────────────────

def delisting_master(*, force: bool = False):
    """FDR KRX-DELISTING — 상폐 마스터 DataFrame(Symbol·Name·DelistingDate·Reason·Kind). 캐시."""
    try:
        import pandas as pd
        if _DELIST_CACHE.exists() and not force:
            return pd.read_parquet(_DELIST_CACHE)
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX-DELISTING")
        try:
            _DELIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(_DELIST_CACHE)
        except Exception:
            pass
        return df
    except Exception as e:
        logger.warning("상폐 마스터 로드 실패: %s", e)
        return None


# 사유 분류 키워드 (퇴출예측 라벨링 — 부실 퇴출만 회피 대상; M&A 피흡수는 호재라 제외)
_MERGER_KW = ("합병", "피흡수", "인수", "지주회사", "분할", "완전자회사", "주식교환", "주식의포괄적교환")
_DISTRESS_KW = ("관리종목", "상장폐지", "감사의견", "자본잠식", "부도", "파산", "부실", "횡령",
                "배임", "회생", "거래정지", "정리매매", "기준미달", "미제출", "부적정", "의견거절", "한정",
                "해산", "청산", "유예기간종료")
# 주의: FDR Reason 의 ~64%는 nan/공란(특히 구·소형 상폐) → 라벨 희소. distress(명확)만 양성 라벨로,
#       reason 없는 'other'는 학습 시 제외(불확실)하는 게 안전. merger(M&A 호재) 오회피는 확실히 차단.
_VOLUNTARY_KW = ("자진",)   # 자진상장폐지(대주주 공개매수 등) — 부실 아님. '신청'은 너무 광범위해 제외


def classify_delisting_reason(reason: str) -> str:
    """상폐 사유 → 'merger'(호재·회피X) / 'voluntary'(자진·회피X) / 'distress'(부실·회피O) / 'other'.

    순서 중요: 자진상장폐지는 '상장폐지' 문자열을 포함하나 부실이 아니므로 distress 보다 먼저 판정.
    """
    r = str(reason or "").replace(" ", "")
    if any(k in r for k in _MERGER_KW):
        return "merger"
    if any(k in r for k in _VOLUNTARY_KW):
        return "voluntary"
    if any(k in r for k in _DISTRESS_KW):
        return "distress"
    return "other"


def distress_delistings() -> dict:
    """부실 퇴출 종목 {code(6자리): {name, date, reason}} — 퇴출예측 양성 라벨. 실패 시 {}."""
    df = delisting_master()
    if df is None or len(df) == 0:
        return {}
    out = {}
    for _, r in df.iterrows():
        reason = r.get("Reason")
        if classify_delisting_reason(reason) != "distress":
            continue
        sym = norm_code(r.get("Symbol"))
        out[sym] = {"name": r.get("Name"), "date": str(r.get("DelistingDate"))[:10], "reason": reason}
    return out
