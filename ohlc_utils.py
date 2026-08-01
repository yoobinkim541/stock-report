"""ohlc_utils.py — OHLC 시계열 정규화 유틸.

가격 히스토리에서 같은 타임스탬프가 여러 번 들어오거나 순서가 뒤섞이면
Plotly candlestick 이 같은 위치에 봉을 겹쳐 그린다. 이 모듈은 그 입력을
정렬하고 중복 타임스탬프를 한 봉으로 합쳐 시각화/리샘플/캐시 경로가 같은
정리 규칙을 쓰도록 한다.
"""
from __future__ import annotations


def normalize_ohlc_frame(df):
    """OHLC DataFrame 을 정렬하고 중복 인덱스를 합친다.

    규칙:
      - 인덱스가 이미 단조 증가 + 유일하면 원본을 그대로 반환한다.
      - 중복 타임스탬프는 하나의 봉으로 병합한다.
      - Open=첫 값, High=최대, Low=최소, Close=마지막, Volume=합계, 나머지=마지막.

    반환값은 원본이 깨끗하면 같은 객체를 돌려줘서 identity 의존 테스트를 해치지 않는다.
    """
    try:
        import pandas as pd
    except Exception:
        return df

    if df is None or getattr(df, "empty", True):
        return df
    try:
        idx = pd.DatetimeIndex(df.index)
    except Exception:
        return df
    if len(idx) == 0:
        return df
    if isinstance(df.index, pd.DatetimeIndex) and idx.is_monotonic_increasing and idx.is_unique:
        return df

    out = df.copy()
    out.index = idx
    if not out.index.is_unique:
        def _first(s):
            return s.iloc[0]

        def _last(s):
            return s.iloc[-1]

        agg = {}
        for col in out.columns:
            if col == "Open":
                agg[col] = _first
            elif col == "High":
                agg[col] = "max"
            elif col == "Low":
                agg[col] = "min"
            elif col == "Close":
                agg[col] = _last
            elif col == "Volume":
                agg[col] = "sum"
            else:
                agg[col] = _last
        out = out.groupby(level=0, sort=False).agg(agg)

    if not out.index.is_monotonic_increasing:
        out = out.sort_index(kind="mergesort")

    return out


def normalize_time_series(series):
    """DatetimeIndex 시계열을 정렬하고 중복 인덱스를 마지막 값 기준으로 합친다."""
    try:
        import pandas as pd
    except Exception:
        return series

    if series is None or getattr(series, "empty", True):
        return series
    try:
        idx = pd.DatetimeIndex(series.index)
    except Exception:
        return series
    if isinstance(series.index, pd.DatetimeIndex) and idx.is_monotonic_increasing and idx.is_unique:
        return series

    out = series.copy()
    out.index = idx
    if not out.index.is_unique:
        out = out.groupby(level=0, sort=False).agg(lambda s: s.iloc[-1])
    if not out.index.is_monotonic_increasing:
        out = out.sort_index(kind="mergesort")
    return out
