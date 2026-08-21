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


def to_naive_days(series):
    """DatetimeIndex 를 **tz 제거 + 날짜 정규화**한 시계열 (거래일 단위 비교용).

    tz-aware/naive 가 섞이면 pandas 의 index 연산(intersection 등)이 조용히 빈 결과를
    내므로, 거래일 기준으로 비교하기 전 한쪽 표준(naive 자정)으로 통일한다.
    """
    try:
        import pandas as pd
    except Exception:
        return series
    if series is None or getattr(series, "empty", True):
        return series
    out = normalize_time_series(series)
    try:
        idx = pd.DatetimeIndex(out.index)
        if idx.tz is not None:
            # tz_convert(None) 은 UTC 로 옮긴 뒤 tz 를 떼서 **날짜가 하루 밀린다**
            # (서울 06-01 00:00+09 → UTC 05-31 15:00). 거래일 비교가 목적이므로
            # 벽시계 시각을 보존하는 tz_localize(None) 을 쓴다.
            idx = idx.tz_localize(None)
    except Exception:
        return out
    try:
        idx = idx.normalize()          # 자정으로 — 시각 차이로 인한 교집합 유실 방지
    except Exception:
        pass
    out = out.copy()
    out.index = idx
    return out


def align_common_index(a, b, start=None):
    """두 시계열을 **공통 거래일**로 정렬해 (a', b') 반환. 정렬 불가 시 (None, None).

    tz-aware/naive 혼재를 흡수한다 — 실측(2026-08): 모의 보상 백필에서 종목은
    tz-naive, 벤치마크는 tz-aware 로 캐시돼 `index.intersection()` 이 **0건**을
    반환, 결정이 20거래일을 훌쩍 넘겨도 영원히 미성숙으로 남던 버그의 근본 원인.
    캐시 재작성 타이밍에 따라 tz 유무가 갈려 비결정적으로 일부만 성숙했다.

    start: 지정 시 그 이후 구간만.
    """
    try:
        import pandas as pd
    except Exception:
        return None, None
    if a is None or b is None:
        return None, None
    x, y = to_naive_days(a), to_naive_days(b)
    if x is None or y is None or getattr(x, "empty", True) or getattr(y, "empty", True):
        return None, None
    try:
        common = x.index.intersection(y.index)
        if start is not None:
            try:
                s0 = pd.Timestamp(start)
                if s0.tzinfo is not None:
                    s0 = s0.tz_convert(None) if s0.tz is not None else s0
                s0 = s0.normalize()
                common = common[common >= s0]
            except Exception:
                pass
        if len(common) == 0:
            return None, None
        return x.reindex(common), y.reindex(common)
    except Exception:
        return None, None
