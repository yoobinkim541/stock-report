#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/thirteenf.py — SEC EDGAR 13F-HR 필러(기관 포트폴리오) 홀딩스.

기존 providers/edgar.py 의 fetch_13f 는 "이 종목을 누가 보유하나"(종목 기준)였다면,
이 모듈은 "이 기관이 뭘 보유하나"(필러 기준) — 워런 버핏(버크셔 해서웨이) 같은 유명 투자자의
분기 포트폴리오 전체를 가져온다. 무료·API 키 불필요(SEC EDGAR 공개 데이터).

13F-HR 정보테이블 XML 스키마 실측(2026-07):
  https://www.sec.gov/Archives/edgar/data/{cik}/{accession-nodash}/{infotable}.xml
  <infoTable><nameOfIssuer>·<cusip>·<value>·<shrsOrPrnAmt><sshPrnamt> 반복.
  ⚠️ SEC 기술규격상 value 단위는 "천달러"지만 실측(버크셔 필링)은 shares×value 역산 결과
  이미 달러 단위로 신고돼 있었다(단위 신고 오류는 13F 필러 사이에 흔한 데이터 품질 이슈) —
  달러로 그대로 사용. 새 필러 추가 시 shares 대비 value 비율(주당가)이 상식적인지 검증할 것.
같은 종목이 복수 서브매니저 행으로 나뉠 수 있어 CUSIP 기준 합산한다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache/thirteenf"))
_FILING_LIST_TTL_H = 24
_HOLDINGS_TTL_H = 24 * 3   # 13F는 분기 1회라 캐시 오래 둬도 무방 — 재조회 부담만 줄임

# 필러 레지스트리 — 필요 시 추가만 하면 됨(CIK 10자리)
# CIK 는 SEC EDGAR company search(data.sec.gov/submissions/CIK{cik}.json 의 name 필드로
# 실측 확인)로 조회한 값(2026-08-15). Founders Fund 는 펀드 빈티지별로 별도 필러(Growth
# II~VII 등 8개)가 갈려 있어 단일 CIK 대표가 안 되므로 여기 넣지 않고 seed 로 남겨둠
# (data/institution_watch_seed.json).
FILERS: dict[str, dict] = {
    "berkshire": {"cik": "0001067983", "name": "Berkshire Hathaway (Warren Buffett)"},
    "bridgewater": {"cik": "0001350694", "name": "Bridgewater Associates"},
    "scion": {"cik": "0001649339", "name": "Scion Asset Management (Michael Burry)"},
    "citadel": {"cik": "0001423053", "name": "Citadel Advisors (Ken Griffin)"},
    "duquesne": {"cik": "0001536411", "name": "Duquesne Family Office (Stanley Druckenmiller)"},
    "pershing_square": {"cik": "0001336528", "name": "Pershing Square Capital Management (Bill Ackman)"},
    "point72": {"cik": "0001603466", "name": "Point72 Asset Management (Steve Cohen)"},
    "third_point": {"cik": "0001040273", "name": "Third Point LLC (Dan Loeb)"},
    "tudor": {"cik": "0000923093", "name": "Tudor Investment Corp. (Paul Tudor Jones)"},
    "nps": {"cik": "0001608046", "name": "National Pension Service"},
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
        logger.warning("13F 캐시 조회 실패 (%s): %s", url, e)
        return None


def latest_filing_meta(filer_key: str) -> dict | None:
    """가장 최근 13F-HR 필링 메타 {cik, accession, filing_date, primary_doc}. 실패 시 None."""
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
    for i, form in enumerate(forms):
        if form == "13F-HR":
            return {
                "cik": cik,
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "primary_doc": recent["primaryDocument"][i],
            }
    return None


def _info_table_url(cik: str, accession: str, primary_doc: str) -> str | None:
    """필링 폴더 index.json 에서 정보테이블 XML(primary_doc 아닌 나머지 .xml) 찾기."""
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}"
    idx = _cached_json(
        _CACHE_DIR / f"idx_{cik}_{acc_nodash}.json", _HOLDINGS_TTL_H, f"{base}/index.json",
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
    """infoTable XML → [{issuer, cusip, value_usd, shares}] (CUSIP 기준 합산)."""
    root = ElementTree.fromstring(xml_bytes)
    agg: dict[str, dict] = {}
    for entry in root.findall(".//t:infoTable", _NS) or root.findall(".//infoTable"):
        def _text(tag, ns_tag):
            el = entry.find(ns_tag, _NS) if ns_tag.startswith("t:") else None
            if el is None:
                el = entry.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        cusip = _text("cusip", "t:cusip")
        if not cusip:
            continue
        issuer = _text("nameOfIssuer", "t:nameOfIssuer")
        try:
            value = float(_text("value", "t:value") or 0)
        except ValueError:
            value = 0.0
        sh_el = entry.find("t:shrsOrPrnAmt/t:sshPrnamt", _NS)
        if sh_el is None:
            sh_el = entry.find("shrsOrPrnAmt/sshPrnamt")
        try:
            shares = float(sh_el.text) if sh_el is not None and sh_el.text else 0.0
        except ValueError:
            shares = 0.0
        a = agg.setdefault(cusip, {"issuer": issuer, "cusip": cusip, "value_usd": 0.0, "shares": 0.0})
        a["value_usd"] += value
        a["shares"] += shares
    return sorted(agg.values(), key=lambda x: -x["value_usd"])


_CUSIP_CACHE_PATH = _CACHE_DIR / "cusip_ticker_map.json"
_CUSIP_CACHE_TTL_H = 24 * 30   # CUSIP↔티커는 사실상 불변 — 재조회 낭비만 줄이면 됨


def _load_cusip_cache() -> dict:
    try:
        return json.loads(_CUSIP_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cusip_cache(cache: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CUSIP_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("CUSIP 캐시 저장 실패(무시): %s", e)


def _resolve_tickers_by_cusip(cusips: list[str]) -> dict[str, str | None]:
    """CUSIP 목록 → {cusip: ticker|None}. 발행사명(법인 축약 표기)은 티커명과 편차가 커서
    발행사명 대신 CUSIP 로 OpenFIGI(무료·무인증 매핑 API) 조회 — 이름 fuzzy-match 보다 정확.

    디스크 캐시(30일)로 재조회 최소화. 실패해도 전체 파이프라인은 계속(티커 None 허용).
    """
    from lib.file_cache import is_fresh
    cache = _load_cusip_cache() if is_fresh(_CUSIP_CACHE_PATH, _CUSIP_CACHE_TTL_H) else {}
    missing = [c for c in cusips if c not in cache]
    # 무인증 요청당 한도 실측: 10건 OK, 15건부터 413 — 10으로 보수적 청킹
    for i in range(0, len(missing), 10):
        chunk = missing[i:i + 10]
        if i > 0:
            time.sleep(0.3)
        try:
            import urllib.request
            payload = json.dumps([{"idType": "ID_CUSIP", "idValue": c} for c in chunk]).encode()
            req = urllib.request.Request(
                "https://api.openfigi.com/v3/mapping", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                results = json.loads(resp.read())
            for cusip, item in zip(chunk, results):
                data = item.get("data") or []
                us = [x for x in data if x.get("exchCode") == "US"]
                pick = (us or data or [None])[0]
                cache[cusip] = pick.get("ticker") if pick else None
        except Exception as e:
            logger.warning("OpenFIGI CUSIP 매핑 실패(티커 없이 진행): %s", e)
            for c in chunk:
                cache.setdefault(c, None)
    if missing:
        _save_cusip_cache(cache)
    return {c: cache.get(c) for c in cusips}


def latest_holdings(filer_key: str) -> dict | None:
    """필러의 최신 13F 보유 스냅샷. {filer, cik, filing_date, accession, holdings:[...]}. 실패 시 None."""
    meta = latest_filing_meta(filer_key)
    if not meta:
        return None
    url = _info_table_url(meta["cik"], meta["accession"], meta["primary_doc"])
    if not url:
        return None
    cache_path = _CACHE_DIR / f"holdings_{meta['cik']}_{meta['accession']}.xml"
    from lib.file_cache import is_fresh
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not is_fresh(cache_path, _HOLDINGS_TTL_H):
            cache_path.write_bytes(_get(url))
        rows = _parse_info_table(cache_path.read_bytes())
    except Exception as e:
        logger.warning("13F 정보테이블 파싱 실패 (%s): %s", filer_key, e)
        return None
    total = sum(r["value_usd"] for r in rows) or 1.0
    tickers = _resolve_tickers_by_cusip([r["cusip"] for r in rows])
    for r in rows:
        r["ticker"] = tickers.get(r["cusip"])
        r["weight_pct"] = round(r["value_usd"] / total * 100, 2)
    return {
        "filer": filer_key, "filer_name": FILERS[filer_key]["name"],
        "cik": meta["cik"], "accession": meta["accession"], "filing_date": meta["filing_date"],
        "holdings": rows, "total_value_usd": total,
    }
