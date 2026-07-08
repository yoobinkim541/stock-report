"""etf_meta.py — ETF 동종(피어) 그룹 큐레이트 시드 (비교·점수의 단일 진실원).

그룹 = 같은 지수/전략을 추종해 직접 비교가 유의미한 ETF 묶음. 수작업 큐레이션
(sp500_meta 패턴·소규모라 생성기 불필요). 레버리지/인버스는 배율이 달라 비교
무의미 → 그룹 제외(피어 섹션 자연 생략).

- strategy: "index"(패시브 지수추종) | "covered_call"(커버드콜 인컴) | "dividend"(배당)
- bench: 그룹 대표 ETF — "추종지수 대비"의 벤치마크 프록시.
  TR 지수 원천(^XNDX 등)이 yfinance 에서 불안정해 대표 ETF TR 로 측정(정직 라벨 병기).
- KR(.KS) 그룹은 yfinance 데이터 라이브 검증을 거친 코드만 편입.
"""
from __future__ import annotations

ETF_GROUPS: dict[str, dict] = {
    "nasdaq100": {
        "name": "나스닥 100", "strategy": "index", "bench": "QQQ",
        "etfs": ["QQQ", "QQQM"],
    },
    "sp500": {
        "name": "S&P 500", "strategy": "index", "bench": "SPY",
        "etfs": ["SPY", "VOO", "IVV", "SPLG"],
    },
    "total_us": {
        "name": "미국 전체시장", "strategy": "index", "bench": "VTI",
        "etfs": ["VTI", "ITOT", "SCHB"],
    },
    "ndx_covered_call": {
        "name": "나스닥100 커버드콜", "strategy": "covered_call",
        "bench": "QQQ", "underlying": "nasdaq100",
        "etfs": ["QYLD", "QQQI", "JEPQ", "GPIQ"],
    },
    "spx_covered_call": {
        "name": "S&P500 커버드콜", "strategy": "covered_call",
        "bench": "SPY", "underlying": "sp500",
        "etfs": ["XYLD", "JEPI", "GPIX", "SPYI"],
    },
    "us_dividend": {
        "name": "미국 배당", "strategy": "dividend", "bench": "SPY",
        "etfs": ["SCHD", "VYM", "DGRO", "HDV"],
    },
    "semis": {
        "name": "반도체", "strategy": "index", "bench": "SMH",
        "etfs": ["SMH", "SOXX"],
    },
    "cash_tbill": {
        "name": "초단기 국채·현금성", "strategy": "index", "bench": "SGOV",
        "etfs": ["SGOV", "BIL", "SHV"],
    },
    "gold": {
        "name": "금", "strategy": "index", "bench": "GLD",
        "etfs": ["GLD", "IAU", "GLDM"],
    },
    "kr_kospi200": {
        "name": "코스피 200", "strategy": "index", "bench": "069500.KS",
        "etfs": ["069500.KS", "102110.KS", "278530.KS"],
    },
    "kr_us_sp500": {
        "name": "미국 S&P500 (국내상장)", "strategy": "index", "bench": "360750.KS",
        "etfs": ["360750.KS", "379800.KS"],
    },
}

# 역맵 (import 시 1회) — 티커는 정확히 한 그룹에만 속해야 한다(테스트 강제)
TICKER_GROUP: dict[str, str] = {}
for _k, _g in ETF_GROUPS.items():
    for _t in _g["etfs"]:
        TICKER_GROUP[_t] = _k

# etf_data._KNOWN_ETFS union 용 (오프라인 ETF 감지 폴백 — US 티커 베이스만)
US_TICKERS: frozenset = frozenset(
    t for g in ETF_GROUPS.values() for t in g["etfs"] if not t.endswith(".KS"))


def group_of(ticker: str) -> str | None:
    """티커 → 그룹키 (069500·A069500 등 KR 표기 정규화). 없으면 None."""
    from providers.etf_data import normalize_ticker
    return TICKER_GROUP.get(normalize_ticker(ticker))


def peers_of(ticker: str) -> list[str]:
    """같은 그룹의 다른 ETF (자신 제외). 그룹 없으면 []."""
    from providers.etf_data import normalize_ticker
    key = group_of(ticker)
    if not key:
        return []
    me = normalize_ticker(ticker)
    return [t for t in ETF_GROUPS[key]["etfs"] if t != me]
