"""providers/dart.py — KR 공시 (DART OpenAPI). DART_API_KEY 필요·키 없으면 graceful.

corpCode.xml(zip) 1회 다운·캐시 → stock_code(6) ↔ corp_code(8) 매핑 →
list.json 으로 최근 공시 목록. 키 미설정 시 모든 함수가 안내 error 반환.
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

_BASE = "https://opendart.fss.or.kr/api"
_CACHE = Path(os.path.expanduser("~/reports/ml-cache/dart_corpcode.xml"))


def _key() -> str | None:
    return os.getenv("DART_API_KEY")


def stock_code(ticker: str) -> str | None:
    """'005930.KS'/'005930' → '005930' (6자리 숫자). 아니면 None."""
    s = (ticker or "").upper().replace(".KS", "").replace(".KQ", "").strip()
    return s if (s.isdigit() and len(s) == 6) else None


def _parse_corpcode(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    out = {}
    for el in root.findall(".//list"):
        sc = (el.findtext("stock_code") or "").strip()
        cc = (el.findtext("corp_code") or "").strip()
        if sc and cc:
            out[sc] = cc
    return out


def corp_code_map(refresh: bool = False) -> dict:
    """{stock_code(6): corp_code(8)}. corpCode.xml zip 다운·캐시. 키 없으면 {}."""
    if not _key():
        return {}
    if not refresh and _CACHE.exists():
        try:
            return _parse_corpcode(_CACHE.read_bytes())
        except Exception:
            pass
    import requests
    try:
        r = requests.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": _key()}, timeout=30)
        if not r.ok:
            return {}
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        data = zf.read(zf.namelist()[0])
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_bytes(data)
        return _parse_corpcode(data)
    except Exception:
        return {}


def recent_disclosures(ticker: str, days: int = 30, limit: int = 15) -> dict:
    """종목 최근 공시 [{date,title,filer,url}]. 키/매핑 없으면 error."""
    if not _key():
        return {"error": "DART_API_KEY 미설정 — .env 에 추가하면 활성", "list": []}
    sc = stock_code(ticker)
    if not sc:
        return {"error": "KR 종목 아님 (예: 005930.KS)", "list": []}
    corp = corp_code_map().get(sc)
    if not corp:
        return {"error": f"corp_code 매핑 없음 ({sc})", "list": []}
    import requests
    e = date.today()
    s = e - timedelta(days=max(1, days))
    try:
        r = requests.get(f"{_BASE}/list.json", timeout=20, params={
            "crtfc_key": _key(), "corp_code": corp,
            "bgn_de": s.strftime("%Y%m%d"), "end_de": e.strftime("%Y%m%d"),
            "page_no": 1, "page_count": limit})
        d = r.json()
        if d.get("status") != "000":
            return {"error": d.get("message", "조회 실패"), "list": []}
        out = [{
            "date": x.get("rcept_dt"), "title": x.get("report_nm"),
            "filer": x.get("flr_nm"),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get('rcept_no')}",
        } for x in d.get("list", [])]
        return {"list": out}
    except Exception as ex:
        return {"error": str(ex), "list": []}
