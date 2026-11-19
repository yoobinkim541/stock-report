#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/thirteenf.py — SEC EDGAR 13F-HR 필러(기관 포트폴리오) 홀딩스.

기존 providers/edgar.py 의 fetch_13f 는 "이 종목을 누가 보유하나"(종목 기준)였다면,
이 모듈은 "이 기관이 뭘 보유하나"(필러 기준) — 워런 버핏(버크셔 해서웨이) 같은 유명 투자자의
분기 포트폴리오 전체를 가져온다. 무료·API 키 불필요(SEC EDGAR 공개 데이터).

13F-HR 정보테이블 XML 스키마 실측(2026-07):
  https://www.sec.gov/Archives/edgar/data/{cik}/{accession-nodash}/{infotable}.xml
  <infoTable><nameOfIssuer>·<titleOfClass>·<cusip>·<value>·<shrsOrPrnAmt><sshPrnamt>·<putCall> 반복.
  ⚠️ value 단위는 2023 이후 "달러"(버크셔 실측). 일부 필러는 여전히 천달러로 신고한다
  (Duquesne 실측: 주당 역산가 중앙값 $0.0x) — _value_scale 이 주당가 중앙값으로 감지해 ×1000.
같은 종목이 복수 서브매니저 행으로 나뉠 수 있어 (CUSIP, putCall) 기준 합산한다.
풋·콜 행은 기초자산 CUSIP 을 그대로 쓰므로 주식과 섞으면 버리(Scion)의 풋이 롱처럼 보인다 —
보유(holdings)는 주식·채권 롱만, 옵션은 options 로 따로 낸다(2026-11 실측: Scion 신고액의 95%,
Citadel 80% 가 옵션).
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache/thirteenf"))
_FILING_LIST_TTL_H = 24
_IMMUTABLE_TTL_H = 24 * 365 * 10   # accession 별 index·정보테이블은 한 번 접수되면 바뀌지 않는다

# 필러 레지스트리 — 필요 시 추가만 하면 됨(CIK 10자리). short 는 화면용 한글 인물명.
# CIK 는 data.sec.gov/submissions/CIK{cik}.json 의 name·최근 13F-HR 접수로 실측 확인
# (기존 10곳 2026-08-15, 추가 11곳 2026-11-19). Founders Fund 는 펀드 빈티지별로 별도 필러
# (Growth II~VII 등 8개)가 갈려 있어 단일 CIK 대표가 안 되므로 seed 로 남겨둠
# (data/institution_watch_seed.json). Greenlight(2024-02 이후)·Elliott(2020 이후)은
# 13F 신고가 끊겨 제외.
FILERS: dict[str, dict] = {
    "berkshire": {"cik": "0001067983", "name": "Berkshire Hathaway (Warren Buffett)", "short": "워런 버핏"},
    "bridgewater": {"cik": "0001350694", "name": "Bridgewater Associates", "short": "브리지워터"},
    "scion": {"cik": "0001649339", "name": "Scion Asset Management (Michael Burry)", "short": "마이클 버리"},
    "citadel": {"cik": "0001423053", "name": "Citadel Advisors (Ken Griffin)", "short": "켄 그리핀"},
    "duquesne": {"cik": "0001536411", "name": "Duquesne Family Office (Stanley Druckenmiller)",
                 "short": "드러켄밀러"},
    # 2026-Q2 부터 Capital Management(0001336528)는 13F-NT 만 내고 보유는 PERSHING SQUARE INC. 가 신고
    "pershing_square": {"cik": "0002026053", "name": "Pershing Square (Bill Ackman)", "short": "빌 애크먼"},
    "point72": {"cik": "0001603466", "name": "Point72 Asset Management (Steve Cohen)", "short": "스티브 코헨"},
    "third_point": {"cik": "0001040273", "name": "Third Point LLC (Dan Loeb)", "short": "댄 로브"},
    "tudor": {"cik": "0000923093", "name": "Tudor Investment Corp. (Paul Tudor Jones)", "short": "폴 튜더 존스"},
    "nps": {"cik": "0001608046", "name": "National Pension Service", "short": "국민연금"},
    "ark": {"cik": "0001697748", "name": "ARK Investment Management (Cathie Wood)", "short": "캐시 우드"},
    "soros": {"cik": "0001029160", "name": "Soros Fund Management (George Soros)", "short": "조지 소로스"},
    "appaloosa": {"cik": "0001656456", "name": "Appaloosa LP (David Tepper)", "short": "데이비드 테퍼"},
    "baupost": {"cik": "0001061768", "name": "Baupost Group (Seth Klarman)", "short": "세스 클라먼"},
    "himalaya": {"cik": "0001709323", "name": "Himalaya Capital Management (Li Lu)", "short": "리루"},
    "tiger_global": {"cik": "0001167483", "name": "Tiger Global Management (Chase Coleman)",
                     "short": "체이스 콜먼"},
    "icahn": {"cik": "0000921669", "name": "Carl Icahn", "short": "칼 아이칸"},
    "fundsmith": {"cik": "0001569205", "name": "Fundsmith LLP (Terry Smith)", "short": "테리 스미스"},
    "gates_foundation": {"cik": "0001166559", "name": "Gates Foundation Trust (Bill Gates)", "short": "빌 게이츠 재단"},
    "pabrai": {"cik": "0001549575", "name": "Dalal Street LLC (Mohnish Pabrai)", "short": "모니시 파브라이"},
    "coatue": {"cik": "0001135730", "name": "Coatue Management (Philippe Laffont)", "short": "필립 라퐁"},
}


def _get(url: str) -> bytes:
    from lib.http_utils import http_get, EDGAR_UA
    return http_get(url, timeout=30, ua=EDGAR_UA)


def _cached_json(path: Path, ttl_h: float, url: str) -> dict | None:
    from lib.file_cache import is_fresh
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not is_fresh(path, ttl_h):
            path.write_bytes(_get(url))
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("SEC EDGAR 조회 실패 (%s): %s", url, e)
        return None


def latest_filing_meta(filer_key: str, *, skip: int = 0) -> dict | None:
    """13F-HR 필링 메타 {cik, accession, filing_date, report_date, primary_doc, amendments}. 실패 시 None.

    skip=0 이 최신, skip=1 이 그 직전 분기(return_proxy 계산용) — 13F-HR 이 아닌
    필링(10-K 등)은 건너뛰고 세도록 카운트. report_date 는 기준 분기말(없으면 None).
    amendments 는 같은 분기말의 13F-HR/A [{accession, filing_date, primary_doc}] (오래된 순)."""
    filer = FILERS.get(filer_key)
    if not filer:
        return None
    cik = filer["cik"]
    sub = _cached_json(
        _CACHE_DIR / f"submissions_{cik}.json", _FILING_LIST_TTL_H,
        f"https://data.sec.gov/submissions/CIK{cik}.json",
    )
    if not sub:
        return None
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    report_dates = recent.get("reportDate") or []

    def _at(i: int) -> dict:
        return {"accession": recent["accessionNumber"][i], "filing_date": recent["filingDate"][i],
                "primary_doc": recent["primaryDocument"][i]}

    seen = 0
    for i, form in enumerate(forms):
        if form != "13F-HR":
            continue
        if seen < skip:
            seen += 1
            continue
        report_date = (report_dates[i] if i < len(report_dates) else None) or None
        amendments = [_at(j) for j, f in enumerate(forms)
                      if f == "13F-HR/A" and report_date and j < len(report_dates) and report_dates[j] == report_date]
        return {"cik": cik, **_at(i), "report_date": report_date,
                "amendments": sorted(amendments, key=lambda a: (a["filing_date"], a["accession"]))}
    return None


def _info_table_url(cik: str, accession: str, primary_doc: str) -> str | None:
    """필링 폴더 index.json 에서 정보테이블 XML(primary_doc 아닌 나머지 .xml) 찾기."""
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}"
    idx = _cached_json(
        _CACHE_DIR / f"idx_{cik}_{acc_nodash}.json", _IMMUTABLE_TTL_H, f"{base}/index.json",
    )
    if not idx:
        return None
    primary_base = os.path.basename(primary_doc)
    for item in (idx.get("directory") or {}).get("item") or []:
        name = item.get("name", "")
        if name.endswith(".xml") and name != primary_base and os.path.basename(primary_doc) not in name:
            return f"{base}/{name}"
    return None


_NS = {"t": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def _parse_info_table(xml_bytes: bytes) -> list[dict]:
    """infoTable XML → [{issuer, cusip, title, put_call, share_type, value_usd, shares}].

    (CUSIP, putCall) 기준 합산 — put_call 은 ""(현물)·"Put"·"Call". value 는 파일 그대로."""
    root = ElementTree.fromstring(xml_bytes)
    agg: dict[tuple[str, str], dict] = {}
    for entry in root.findall(".//t:infoTable", _NS) or root.findall(".//infoTable"):
        def _text(tag, ns_tag):
            el = entry.find(ns_tag, _NS)
            if el is None:
                el = entry.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        cusip = _text("cusip", "t:cusip")
        if not cusip:
            continue
        try:
            value = float(_text("value", "t:value") or 0)
        except ValueError:
            value = 0.0
        try:
            shares = float(_text("shrsOrPrnAmt/sshPrnamt", "t:shrsOrPrnAmt/t:sshPrnamt") or 0)
        except ValueError:
            shares = 0.0
        put_call = _text("putCall", "t:putCall").capitalize()
        a = agg.setdefault((cusip, put_call), {
            "issuer": _text("nameOfIssuer", "t:nameOfIssuer"), "cusip": cusip,
            "title": _text("titleOfClass", "t:titleOfClass"), "put_call": put_call,
            "share_type": _text("shrsOrPrnAmt/sshPrnamtType", "t:shrsOrPrnAmt/t:sshPrnamtType") or "SH",
            "value_usd": 0.0, "shares": 0.0,
        })
        a["value_usd"] += value
        a["shares"] += shares
    return sorted(agg.values(), key=lambda x: -x["value_usd"])


_THOUSANDS_PRICE_CEILING = 1.0   # 현물 주당 역산가 중앙값이 $1 미만이면 천달러 신고로 본다


def _value_scale(rows: list[dict]) -> int:
    """value 신고 단위 배율 — 달러면 1, 천달러면 1000 (현물 주식 행 주당 역산가 중앙값으로 판정)."""
    prices = [r["value_usd"] / r["shares"] for r in rows
              if not r.get("put_call") and r.get("share_type", "SH") == "SH"
              and r.get("shares", 0) > 0 and r.get("value_usd", 0) > 0]
    if len(prices) >= 3 and statistics.median(prices) < _THOUSANDS_PRICE_CEILING:
        return 1000
    return 1


# ── CUSIP → 티커 (OpenFIGI) ────────────────────────────────────────────────
# 캐시 값: "TICKER" = 매핑됨, "" = 확정 미매핑(OpenFIGI 'No identifier found' 또는 주식 아님),
# null = 아직 확인 못 함(과거 버전이 네트워크 실패·한도 초과를 null 로 저장해 미국 종목
# 3,780개가 티커 없이 굳어 있었다 — 다시 조회 대상). 공백이 든 값은 채권 티커
# (예: 'NBIS 1 09/15/30')라 다시 조회해 주식만 고른다.
_CUSIP_CACHE_PATH = _CACHE_DIR / "cusip_ticker_map.json"
_FIGI_URL = "https://api.openfigi.com/v3/mapping"
_FIGI_BATCH = 10            # 무인증 요청당 한도 실측: 10건 OK, 15건부터 413
_FIGI_SPACING_S = 2.5       # 무인증 한도 분당 25회
_FIGI_PAGE_REQUESTS = 3     # 화면 경로 한 번에 쓸 요청 수 — 나머지는 warm_ticker_map(주간 크론)
_FIGI_PAGE_INTERVAL_S = 60  # 화면 경로는 분당 한 번만 조회(기관 21곳×2분기 호출이 줄줄이 기다리지 않게)
_FIGI_SAVE_EVERY = 10       # 긴 워밍 중간에도 진행분을 저장
_CACHE_LOCK = threading.Lock()
_last_page_figi = 0.0


def _load_cusip_cache() -> dict:
    try:
        return json.loads(_CUSIP_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cusip_cache(new_entries: dict) -> None:
    """새 매핑을 디스크 캐시에 병합 저장 — 다른 프로세스(크론 워밍·대시보드)가 그사이 쓴 것을 지우지 않게
    저장 직전에 다시 읽어 합친 뒤 원자적으로 교체."""
    try:
        _CUSIP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache = _load_cusip_cache()
        cache.update(new_entries)
        tmp = _CUSIP_CACHE_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CUSIP_CACHE_PATH)
    except Exception as e:
        logger.warning("CUSIP 캐시 저장 실패(무시): %s", e)


def _settled(value) -> bool:
    return isinstance(value, str) and " " not in value


def _norm_ticker(value) -> str | None:
    """캐시 값 → 화면 티커. 미매핑·채권은 None, 'BRK/B' 는 yfinance 표기 'BRK-B',
    OpenFIGI 의 when-issued 표기 'WBD*' 는 'WBD'."""
    if not _settled(value) or not value:
        return None
    return value.replace("/", "-").rstrip("*") or None


def _pick_equity_ticker(item: dict) -> str:
    """OpenFIGI 응답 1건 → 주식 티커 | ""(확정 미매핑 — 'No identifier found'·잘못된 CUSIP·주식 아님).
    한도 초과·네트워크 오류는 응답 전체가 예외라 여기 오지 않는다(호출부가 저장 없이 중단)."""
    data = item.get("data") or []
    equity = [x for x in data if x.get("marketSector") in (None, "Equity") and x.get("ticker")
              and " " not in x["ticker"]]
    us = [x for x in equity if x.get("exchCode") == "US"]
    pick = (us or equity or [None])[0]
    return pick["ticker"] if pick else ""


def _resolve(cusips: list[str], max_requests: int | None) -> tuple[dict[str, str | None], int, int]:
    """→ ({cusip: ticker|None}, 아직 확인 못 한 CUSIP 수, 보낸 요청 수).
    max_requests=None 이면 끝까지 조회."""
    import urllib.request

    with _CACHE_LOCK:
        cache = _load_cusip_cache()
        missing = [c for c in dict.fromkeys(cusips) if not _settled(cache.get(c))]
        sent = 0
        fresh: dict[str, str] = {}
        for i in range(0, len(missing), _FIGI_BATCH):
            if max_requests is not None and sent >= max_requests:
                break
            chunk = missing[i:i + _FIGI_BATCH]
            if sent:
                time.sleep(_FIGI_SPACING_S)
            sent += 1
            try:
                payload = json.dumps([{"idType": "ID_CUSIP", "idValue": c} for c in chunk]).encode()
                req = urllib.request.Request(_FIGI_URL, data=payload,
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    results = json.loads(resp.read())
            except Exception as e:
                # 한도 초과(429)·네트워크 실패는 저장하지 않는다 — 다음 호출이 이어서 조회
                logger.warning("OpenFIGI CUSIP 매핑 중단(티커 없이 진행): %s", e)
                break
            for cusip, item in zip(chunk, results):
                ticker = _pick_equity_ticker(item if isinstance(item, dict) else {})
                if ticker is not None:
                    fresh[cusip] = ticker
            if sent % _FIGI_SAVE_EVERY == 0 and fresh:
                _save_cusip_cache(fresh)
        if fresh:
            _save_cusip_cache(fresh)
            cache.update(fresh)
    pending = sum(1 for c in set(cusips) if not _settled(cache.get(c)))
    return {c: _norm_ticker(cache.get(c)) for c in cusips}, pending, sent


def _resolve_tickers_by_cusip(cusips: list[str], *, max_requests: int | None = None) -> dict[str, str | None]:
    """CUSIP 목록 → {cusip: ticker|None}. 발행사명(법인 축약 표기)은 티커명과 편차가 커서
    발행사명 대신 CUSIP 로 OpenFIGI(무료·무인증 매핑 API) 조회 — 이름 fuzzy-match 보다 정확.
    주식(Equity) 결과만 쓴다. 디스크 캐시는 만료 없음(CUSIP↔티커는 사실상 불변).
    실패해도 전체 파이프라인은 계속(티커 None 허용)."""
    return _resolve(cusips, max_requests)[0]


# ── 보유 스냅샷 ────────────────────────────────────────────────────────────
# accession 은 불변이라 같은 프로세스에서 한 번만 파싱한다(Citadel 정보테이블 8MB).
# 티커가 덜 풀린 결과는 메모하지 않아 다음 호출이 이어서 채운다.
_HOLDINGS_MEMO: dict[tuple[str, ...], dict] = {}   # 키: (cik, accession, 정정 accession…)
_ROWS_MEMO: dict[tuple[str, ...], list[dict]] = {}   # 티커가 덜 풀린 분기의 파싱 결과(재파싱 방지)
_FETCH_LOCK = threading.Lock()


def _load_rows(meta: dict) -> list[dict] | None:
    url = _info_table_url(meta["cik"], meta["accession"], meta["primary_doc"])
    if not url:
        return None
    cache_path = _CACHE_DIR / f"holdings_{meta['cik']}_{meta['accession']}.xml"
    from lib.file_cache import is_fresh
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not is_fresh(cache_path, _IMMUTABLE_TTL_H):
            cache_path.write_bytes(_get(url))
        return _parse_info_table(cache_path.read_bytes())
    except Exception as e:
        logger.warning("13F 정보테이블 파싱 실패 (%s): %s", meta.get("accession"), e)
        return None


def _amendment_type(meta: dict) -> str:
    """13F-HR/A 표지(primary_doc.xml)의 amendmentType — 'RESTATEMENT'·'NEW HOLDINGS', 못 읽으면 ''."""
    acc_nodash = meta["accession"].replace("-", "")
    cache_path = _CACHE_DIR / f"primary_{meta['cik']}_{acc_nodash}.xml"
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/{acc_nodash}/"
           f"{os.path.basename(meta['primary_doc'])}")
    from lib.file_cache import is_fresh
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not is_fresh(cache_path, _IMMUTABLE_TTL_H):
            cache_path.write_bytes(_get(url))
        m = re.search(rb"<(?:\w+:)?amendmentType>\s*([^<]+?)\s*<", cache_path.read_bytes())
    except Exception as e:
        logger.warning("13F 정정 표지 조회 실패 (%s): %s", meta.get("accession"), e)
        return ""
    return m.group(1).decode().upper() if m else ""


def _quarter_rows(meta: dict) -> list[dict] | None:
    """원 13F-HR 정보테이블에 같은 분기 정정을 반영한 행.

    'NEW HOLDINGS' 정정은 원본에 빠진 행을 더하고(퍼싱스퀘어 2026-06-30: 자사주 PS 1행 추가),
    'RESTATEMENT' 는 그 분기 보유 전체를 대체한다. 정정 순서는 제출 순."""
    rows = _load_rows(meta)
    if rows is None:
        return None
    for amd in meta.get("amendments") or []:
        am = {**meta, **amd}
        kind = _amendment_type(am)
        if kind not in ("RESTATEMENT", "NEW HOLDINGS"):
            continue
        extra = _load_rows(am)
        if extra is None:
            continue
        if kind == "RESTATEMENT":
            rows = extra
            continue
        agg = {(r["cusip"], r["put_call"]): dict(r) for r in rows}
        for r in extra:
            a = agg.setdefault((r["cusip"], r["put_call"]), {**r, "value_usd": 0.0, "shares": 0.0})
            a["value_usd"] += r["value_usd"]
            a["shares"] += r["shares"]
        rows = sorted(agg.values(), key=lambda x: -x["value_usd"])
    return rows


def _build_snapshot(filer_key: str, meta: dict, rows: list[dict], tickers: dict) -> dict:
    scale = _value_scale(rows)
    longs, options = [], []
    for r in rows:
        r = {**r, "value_usd": r["value_usd"] * scale, "ticker": tickers.get(r["cusip"])}
        (options if r["put_call"] else longs).append(r)
    long_total = sum(r["value_usd"] for r in longs)
    put_total = sum(r["value_usd"] for r in options if r["put_call"] == "Put")
    call_total = sum(r["value_usd"] for r in options if r["put_call"] == "Call")
    reported = long_total + put_total + call_total
    for r in longs:
        r["weight_pct"] = round(r["value_usd"] / (long_total or 1.0) * 100, 2)
    for r in options:   # 옵션은 롱 대비가 아니라 전체 신고액 대비(기초자산 명목) 비중
        r["weight_pct"] = round(r["value_usd"] / (reported or 1.0) * 100, 2)
    return {
        "filer": filer_key, "filer_name": FILERS[filer_key]["name"],
        "cik": meta["cik"], "accession": meta["accession"], "filing_date": meta["filing_date"],
        "report_date": meta.get("report_date"),
        "holdings": longs, "options": options, "total_value_usd": long_total or 1.0,
        "put_value_usd": put_total, "call_value_usd": call_total, "value_scale": scale,
    }


def latest_holdings(filer_key: str, *, skip: int = 0) -> dict | None:
    """필러의 13F 보유 스냅샷. 실패 시 None.

    {filer, filer_name, cik, accession, filing_date, report_date,
     holdings: 현물 롱 [{issuer, cusip, ticker, title, value_usd, shares, weight_pct(롱 대비 %)}],
     options: 풋·콜 [{..., put_call, weight_pct(전체 신고액 대비 %)}],
     total_value_usd(롱 합계), put_value_usd, call_value_usd, value_scale}
    skip=1 이면 직전 분기(return_proxy·분기 변화 계산용 — reports/institution_watch.py)."""
    global _last_page_figi
    meta = latest_filing_meta(filer_key, skip=skip)
    if not meta:
        return None
    memo_key = (meta["cik"], meta["accession"], *(a["accession"] for a in meta.get("amendments") or []))
    if memo_key in _HOLDINGS_MEMO:
        return _HOLDINGS_MEMO[memo_key]
    with _FETCH_LOCK:
        if memo_key in _HOLDINGS_MEMO:
            return _HOLDINGS_MEMO[memo_key]
        rows = _ROWS_MEMO.get(memo_key)
        if rows is None:
            rows = _quarter_rows(meta)
            if rows is None:
                return None
            _ROWS_MEMO[memo_key] = rows
        budget = _FIGI_PAGE_REQUESTS if time.monotonic() - _last_page_figi >= _FIGI_PAGE_INTERVAL_S else 0
        tickers, pending, sent = _resolve([r["cusip"] for r in rows], budget)
        if sent:
            _last_page_figi = time.monotonic()
        snap = _build_snapshot(filer_key, meta, rows, tickers)
        if not pending:
            _HOLDINGS_MEMO[memo_key] = snap
            _ROWS_MEMO.pop(memo_key, None)
        return snap


def warm_ticker_map(filer_keys=None, *, skips=(0, 1)) -> int:
    """등록 필러의 최근 분기 정보테이블 CUSIP 을 한도 안에서 끝까지 티커로 매핑(주간 크론용).

    화면 경로는 분당 요청 수를 묶어 두므로 신규 필러·새 분기의 미매핑분은 여기서 채운다.
    반환: 아직 못 푼 CUSIP 수(네트워크 실패·한도 초과로 중단되면 남는다)."""
    cusips: list[str] = []
    for key in filer_keys or list(FILERS):
        for skip in skips:
            meta = latest_filing_meta(key, skip=skip)
            rows = _quarter_rows(meta) if meta else None
            cusips.extend(r["cusip"] for r in rows or [])
    pending = _resolve(cusips, None)[1]
    _HOLDINGS_MEMO.clear()
    return pending
