"""ticker_names.py — 종목 티커 ↔ 회사명 단일 진실원 (표시·검색 공용).

파편화됐던 이름 소스(order_generator.COMPANY_NAMES 영문·attachment_parser.KNOWN_TICKERS
한글·data_pipeline.KR_TOP10_META KOSPI)를 하나로 통합한다. 표시(`label`)와 검색(`resolve`)
양쪽이 이 모듈을 쓴다.

- 표시명: US=영문(EN), 한국 상장주(.KS)=한글(KR). 큐레이트 시드 미스 시 yfinance longName
  디스크캐시(offline-safe·graceful None) 폴백.
- 검색: 한글명·영문명·티커 어느 것으로도 → 티커. 역인덱스(KO 별칭 + EN + KR).
- 경량: import-time 순수 stdlib. yfinance 는 캐시 미스 시에만 lazy import(봇 hermes/대시보드
  .venv 모두 보유). 네트워크 실패·미설치도 예외 없이 None.

포맷 규칙(CLAUDE.md): `회사명 (티커)` — 예 `NVIDIA (NVDA)`·`삼성전자 (005930.KS)`.
"""
from __future__ import annotations

import json
import os
import re

# ── 큐레이트 시드 ─────────────────────────────────────────────────────
# US 영문 표시명 (보유 + 인기 US·ETF). 값이 티커와 같으면 병기 생략(label).
EN: dict[str, str] = {
    # 보유 종목
    "MSFT": "Microsoft", "NVDA": "NVIDIA", "ORCL": "Oracle", "SAP": "SAP",
    "UNH": "UnitedHealth", "GOOGL": "Alphabet", "GOOG": "Alphabet",
    "SPMO": "Invesco S&P 500 Momentum", "SGOV": "iShares 0-3M Treasury",
    "QQQI": "NEOS Nasdaq-100 Income",
    # 지수·ETF·레버리지
    "QQQ": "Invesco QQQ", "SPY": "SPDR S&P 500", "TLT": "iShares 20+ Treasury",
    "QLD": "ProShares Ultra QQQ", "TQQQ": "ProShares UltraPro QQQ",
    "SOXL": "Direxion Semi Bull 3x", "UPRO": "ProShares UltraPro S&P500",
    "QYLD": "Global X Nasdaq Covered Call",
    # 인기 US (검색·표시)
    "AAPL": "Apple", "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla",
    "MU": "Micron Technology", "AMD": "AMD", "AVGO": "Broadcom", "NFLX": "Netflix",
    "INTC": "Intel", "QCOM": "Qualcomm", "TXN": "Texas Instruments", "ADBE": "Adobe",
    "CSCO": "Cisco", "PEP": "PepsiCo", "COST": "Costco", "CRM": "Salesforce",  # ticker-ok
    "NOW": "ServiceNow", "CPNG": "Coupang", "PLTR": "Palantir", "ARM": "Arm Holdings",  # ticker-ok
    "SMCI": "Super Micro", "TSM": "TSMC", "ASML": "ASML", "MRVL": "Marvell",
    "PANW": "Palo Alto Networks", "SNOW": "Snowflake", "SHOP": "Shopify",
    # ── 대형주 확장: 금융 ──
    "BRK-B": "Berkshire Hathaway", "JPM": "JPMorgan Chase", "BAC": "Bank of America",
    "WFC": "Wells Fargo", "GS": "Goldman Sachs", "MS": "Morgan Stanley",
    "V": "Visa", "MA": "Mastercard", "AXP": "American Express", "C": "Citigroup",
    # ── 헬스케어 ──
    "JNJ": "Johnson & Johnson", "LLY": "Eli Lilly", "ABBV": "AbbVie", "MRK": "Merck",
    "PFE": "Pfizer", "TMO": "Thermo Fisher", "ABT": "Abbott", "DHR": "Danaher",
    # ── 소비재 ──
    "WMT": "Walmart", "KO": "Coca-Cola", "PG": "Procter & Gamble", "HD": "Home Depot",
    "MCD": "McDonald's", "NKE": "Nike", "SBUX": "Starbucks", "DIS": "Disney",
    "TGT": "Target", "LOW": "Lowe's",
    # ── 산업·에너지 ──
    "XOM": "Exxon Mobil", "CVX": "Chevron", "CAT": "Caterpillar", "BA": "Boeing",
    "GE": "GE Aerospace", "HON": "Honeywell", "UPS": "UPS", "RTX": "RTX",
    "LMT": "Lockheed Martin",
    # ── 테크·통신 ──
    "IBM": "IBM", "UBER": "Uber", "ABNB": "Airbnb", "PYPL": "PayPal",
    "COIN": "Coinbase", "RIVN": "Rivian", "F": "Ford", "GM": "General Motors",
    "T": "AT&T", "VZ": "Verizon", "CMCSA": "Comcast",
    # ── 반도체 확장 ──
    "AMAT": "Applied Materials", "ADI": "Analog Devices", "KLAC": "KLA",
    "LRCX": "Lam Research", "NXPI": "NXP Semiconductors",
    # ── 인기 ETF ──
    "VOO": "Vanguard S&P 500", "VTI": "Vanguard Total Market", "SCHD": "Schwab US Dividend",
    "JEPI": "JPMorgan Equity Premium", "JEPQ": "JPMorgan Nasdaq Premium",
    "DIA": "SPDR Dow Jones", "IWM": "iShares Russell 2000",
    "GLD": "SPDR Gold", "SLV": "iShares Silver", "ARKK": "ARK Innovation",
}

# 검색 별칭 (한글 대표명 + 추가 영문 별칭). 첫 항목이 대표 한글명(있으면).
KO: dict[str, tuple[str, ...]] = {
    "MSFT": ("마이크로소프트", "MICROSOFT"),
    "NVDA": ("엔비디아",),
    "ORCL": ("오라클",),
    "SAP": ("에스에이피",),
    "UNH": ("유나이티드헬스", "UNITEDHEALTH"),
    "GOOGL": ("알파벳", "구글", "GOOGLE"),
    "GOOG": ("알파벳", "구글", "GOOGLE"),
    "SPMO": ("S&P500 모멘텀",),
    "SGOV": ("초단기 국채",),
    "QQQI": ("나스닥100 인컴",),
    "QQQ": ("나스닥100", "나스닥"),
    "SPY": ("S&P500", "에스앤피"),
    "TLT": ("미국 장기국채",),
    "AAPL": ("애플", "APPLE"),
    "AMZN": ("아마존", "AMAZON"),
    "META": ("메타", "페이스북", "FACEBOOK"),
    "TSLA": ("테슬라", "TESLA"),
    "MU": ("마이크론", "MICRON"),
    "AMD": ("에이엠디",),
    "AVGO": ("브로드컴", "BROADCOM"),
    "NFLX": ("넷플릭스", "NETFLIX"),
    "INTC": ("인텔", "INTEL"),
    "QCOM": ("퀄컴", "QUALCOMM"),
    "TXN": ("텍사스인스트루먼트",),
    "ADBE": ("어도비", "ADOBE"),
    "CSCO": ("시스코", "CISCO"),
    "PEP": ("펩시코", "펩시", "PEPSI"),
    "COST": ("코스트코", "COSTCO"),
    "CRM": ("세일스포스", "SALESFORCE"),  # ticker-ok
    "NOW": ("서비스나우", "SERVICENOW"),  # ticker-ok
    "CPNG": ("쿠팡", "COUPANG"),  # ticker-ok
    "PLTR": ("팔란티어", "PALANTIR"),
    "ARM": ("암홀딩스",),
    "SMCI": ("슈퍼마이크로",),
    "TSM": ("티에스엠씨", "타이완반도체"),
    "ASML": ("에이에스엠엘",),
    "MRVL": ("마벨",),
    "PANW": ("팔로알토",),
    "SNOW": ("스노우플레이크",),
    "SHOP": ("쇼피파이",),
    # ── 대형주 확장: 금융 ──
    "BRK-B": ("버크셔", "버크셔해서웨이", "버크셔 해서웨이", "BERKSHIRE"),
    "JPM": ("제이피모건", "JP모건", "JPMORGAN"),
    "BAC": ("뱅크오브아메리카", "뱅오아", "BANK OF AMERICA"),
    "WFC": ("웰스파고", "WELLS FARGO"),
    "GS": ("골드만삭스", "골드만", "GOLDMAN"),
    "MS": ("모건스탠리", "MORGAN STANLEY"),
    "V": ("비자", "VISA"),
    "MA": ("마스터카드", "MASTERCARD"),
    "AXP": ("아메리칸익스프레스", "아멕스", "AMEX"),
    "C": ("씨티그룹", "씨티", "CITI"),
    # ── 헬스케어 ──
    "JNJ": ("존슨앤존슨", "존슨", "J&J"),
    "LLY": ("일라이릴리", "릴리", "ELI LILLY"),
    "ABBV": ("애브비", "ABBVIE"),
    "MRK": ("머크", "MERCK"),
    "PFE": ("화이자", "PFIZER"),
    "TMO": ("써모피셔", "THERMO FISHER"),
    "ABT": ("애벗", "ABBOTT"),
    "DHR": ("다나허", "DANAHER"),
    # ── 소비재 ──
    "WMT": ("월마트", "WALMART"),
    "KO": ("코카콜라", "코크", "COCA COLA"),
    "PG": ("프록터앤갬블", "P&G", "PROCTER"),
    "HD": ("홈디포", "HOME DEPOT"),
    "MCD": ("맥도날드", "맥날", "MCDONALD"),
    "NKE": ("나이키", "NIKE"),
    "SBUX": ("스타벅스", "스벅", "STARBUCKS"),
    "DIS": ("디즈니", "DISNEY"),
    "TGT": ("타겟", "TARGET"),
    "LOW": ("로우스", "LOWES"),
    # ── 산업·에너지 ──
    "XOM": ("엑슨모빌", "엑슨", "EXXON"),
    "CVX": ("셰브런", "CHEVRON"),
    "CAT": ("캐터필러", "CATERPILLAR"),
    "BA": ("보잉", "BOEING"),
    "GE": ("제너럴일렉트릭", "GENERAL ELECTRIC"),
    "HON": ("허니웰", "HONEYWELL"),
    "UPS": ("유피에스", "UPS"),
    "RTX": ("레이시온", "RAYTHEON"),
    "LMT": ("록히드마틴", "록히드", "LOCKHEED"),
    # ── 테크·통신 ──
    "IBM": ("아이비엠",),
    "UBER": ("우버",),
    "ABNB": ("에어비앤비", "AIRBNB"),
    "PYPL": ("페이팔", "PAYPAL"),
    "COIN": ("코인베이스", "COINBASE"),
    "RIVN": ("리비안", "RIVIAN"),
    "F": ("포드", "FORD"),
    "GM": ("제너럴모터스", "지엠", "GENERAL MOTORS"),
    "T": ("에이티앤티", "AT&T"),
    "VZ": ("버라이즌", "VERIZON"),
    "CMCSA": ("컴캐스트", "COMCAST"),
    # ── 반도체 확장 ──
    "AMAT": ("어플라이드머티리얼즈", "어플라이드", "APPLIED MATERIALS"),
    "ADI": ("아나로그디바이스", "ANALOG DEVICES"),
    "KLAC": ("케이엘에이",),
    "LRCX": ("램리서치", "LAM RESEARCH"),
    "NXPI": ("엔엑스피", "NXP"),
    # ── 인기 ETF ──
    "VOO": ("뱅가드 S&P500",),
    "VTI": ("뱅가드 전체시장",),
    "SCHD": ("슈왑 배당",),
    "JEPI": ("JP모건 프리미엄인컴",),
    "JEPQ": ("JP모건 나스닥 프리미엄",),
    "DIA": ("다우존스", "다우"),
    "IWM": ("러셀2000",),
    "GLD": ("금 ETF", "골드"),
    "SLV": ("은 ETF", "실버"),
    "ARKK": ("아크 이노베이션", "ARK"),
}

# 한국 상장주(.KS) 한글 표시명 (KOSPI 시총 상위 — data_pipeline.KR_TOP10_META 와 동일값).
KR: dict[str, str] = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "005490.KS": "포스코홀딩스",
    "035420.KS": "NAVER", "035720.KS": "카카오", "000270.KS": "기아", "006400.KS": "삼성SDI",
}

_CACHE_PATH = os.path.expanduser("~/reports/ml-cache/ticker_names.json")
_CACHE_TTL = 30 * 86400  # 30일
_yf_cache: dict | None = None


# ── 정규화·역인덱스 ───────────────────────────────────────────────────
def _norm(s: str) -> str:
    return " ".join((s or "").upper().split())


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for t, nm in EN.items():
        idx.setdefault(_norm(nm), t)
    for t, aliases in KO.items():
        for a in aliases:
            idx.setdefault(_norm(a), t)
    for t, nm in KR.items():
        idx.setdefault(_norm(nm), t)
    return idx


_INDEX = _build_index()
_ALL_TICKERS = set(EN) | set(KR) | set(KO)


# ── yfinance 디스크캐시 (graceful) ────────────────────────────────────
def _load_cache() -> dict:
    global _yf_cache
    if _yf_cache is None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                _yf_cache = json.load(f)
        except Exception:
            _yf_cache = {}
    return _yf_cache


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


def _yf_name(ticker: str, allow_net: bool = True) -> str | None:
    """yfinance longName/shortName — 디스크캐시 우선, 미스 시 lazy fetch. 실패 None."""
    import time
    cache = _load_cache()
    ent = cache.get(ticker)
    now = time.time()
    if ent and (now - ent.get("ts", 0)) < _CACHE_TTL:
        return ent.get("name") or None
    if not allow_net:
        return (ent or {}).get("name") or None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        nm = info.get("shortName") or info.get("longName")
        nm = str(nm) if nm else None
    except Exception:
        nm = None
    if nm:
        cache[ticker] = {"name": nm, "ts": now}
        _save_cache(cache)
    return nm


# ── 공개 API ─────────────────────────────────────────────────────────
def display_name(ticker: str, allow_net: bool = True) -> str | None:
    """표시용 회사명. US=영문(EN)·한국 상장주(.KS)=한글(KR). 미스 시 yfinance 캐시. 없으면 None."""
    t = (ticker or "").strip()
    if not t:
        return None
    tu = t.upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return KR.get(t) or KR.get(tu) or _yf_name(t, allow_net)
    return EN.get(tu) or _yf_name(tu, allow_net)


def label(ticker: str, name: str | None = None, maxlen: int | None = None,
          allow_net: bool = False) -> str:
    """`회사명 (티커)` 통일. name 없으면 resolve. 이름 없거나 티커와 같으면 티커만.

    maxlen: 회사명 최대 표시 길이(초과 시 절단·좁은칸/등폭표용).
    allow_net: 기본 False(렌더 경로 무네트워크 — 큐레이트+디스크캐시만). 리포트 크론만 True.
    """
    t = (ticker or "").strip()
    if not t:
        return ""
    if name is None:
        name = display_name(t, allow_net=allow_net)
    if name and name.strip() and name.strip() != t:
        nm = name.strip()
        if maxlen and len(nm) > maxlen:
            nm = nm[:max(1, maxlen - 1)].rstrip() + "…"
        return f"{nm} ({t})"
    return t


def resolve(query: str, allow_net: bool = False) -> str | None:
    """검색어(한글명·영문명·티커) → 티커. 없으면 None. (기본 무네트워크)"""
    q = (query or "").strip()
    if not q:
        return None
    qu = q.upper()
    # 1) 정확 티커 (US 대문자 or .KS 원형)
    if qu in _ALL_TICKERS:
        return qu
    if q in KR:
        return q
    # 2) 정확 이름/별칭
    nq = _norm(q)
    if nq in _INDEX:
        return _INDEX[nq]
    # 3) 부분일치 (별칭이 검색어를 포함하거나 그 역). startswith 우선.
    best = None
    for alias, tk in _INDEX.items():
        if nq and (alias.startswith(nq) or nq in alias):
            if alias.startswith(nq):
                return tk
            best = best or tk
    if best:
        return best
    # 4) 캐시된 yfinance 이름에서 부분일치 (allow_net 시 확장 안 함 — 인덱스 한정 정직)
    if allow_net:
        for tk, ent in _load_cache().items():
            if nq and nq in _norm(ent.get("name", "")):
                return tk
    return None


_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


def normalize_input(query: str) -> str | None:
    """자유 입력(한글명·영문명·티커) → 정규 티커. 못 찾으면 None.

    대시보드 검색 셀렉트박스 `accept_new_options` 경로용(시드 밖 임의 US 티커 지원).
    티커 형태 입력(예: RIVN·BRK-B·NET)은 **정확 매칭만** 취하고 실패 시 리터럴 티커로 통과 —
    부분매칭 오염(예: 'NET'→NFLX) 방지. 비티커형(한글 등)은 기존 resolve(부분매칭 포함).
    무네트워크·순수.
    """
    q = (query or "").strip()
    if not q:
        return None
    qu = q.upper()
    if _US_TICKER_RE.match(qu):
        if qu in _ALL_TICKERS:
            return qu
        return _INDEX.get(_norm(q)) or qu   # 이름 정확매칭 없으면 리터럴 티커로 통과
    return resolve(q)


def search(query: str, limit: int = 8) -> list[tuple[str, str]]:
    """부분일치 후보 [(티커, 표시명)] (autocomplete용·무네트워크)."""
    q = (query or "").strip()
    nq = _norm(q)
    if not nq:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for alias, tk in _INDEX.items():
        if tk in seen:
            continue
        if nq in alias or nq in tk.upper():
            out.append((tk, display_name(tk, allow_net=False) or tk))
            seen.add(tk)
        if len(out) >= limit:
            break
    return out


def universe() -> list[str]:
    """검색 가능한 전체 티커(EN∪KR∪KO 큐레이트 키), 티커 정렬. 대시보드 통합 검색용."""
    return sorted(_ALL_TICKERS)


def search_label(ticker: str) -> str:
    """검색 셀렉트박스용 라벨 `회사명 (티커) · 한글별칭` — 한/영/티커 타입어헤드 지원.

    한글 별칭을 덧붙여 네이티브 selectbox 필터가 한글 입력도 매칭하게 한다.
    """
    t = (ticker or "").strip()
    base = label(t)  # "회사명 (티커)" or 티커
    ko = KO.get(t.upper())
    if ko and ko[0] and ko[0] not in base:
        return f"{base} · {ko[0]}"
    return base
