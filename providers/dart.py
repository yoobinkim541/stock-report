"""providers/dart.py — KR 공시·재무제표 (DART OpenAPI). DART_API_KEY 필요·키 없으면 graceful.

corpCode.xml(zip) 1회 다운·캐시 → stock_code(6) ↔ corp_code(8) 매핑 →
list.json 으로 최근 공시 목록. fnlttSinglAcnt.json 으로 주요 재무계정 조회.
키 미설정 시 모든 함수가 안내 error 반환.
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


# ── 재무제표 주요계정 (PER/PBR/ROE/EPS 계산 원천) ───────────────────────────

ANNUAL_REPORT = "11011"

REPORT_CODES = {
    "annual": "11011",  # 사업보고서
    "q1": "11013",      # 1분기보고서
    "half": "11012",    # 반기보고서
    "q3": "11014",      # 3분기보고서
}


def _amount(v):
    """DART 금액 문자열('1,234', '(1,234)', '-') → float|None. 테스트 가능한 순수 파서."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def _normalize_account_row(row: dict) -> dict:
    return {
        "account_nm": (row.get("account_nm") or "").strip(),
        "fs_div": (row.get("fs_div") or "").strip(),      # CFS=연결, OFS=별도
        "fs_nm": (row.get("fs_nm") or "").strip(),
        "sj_div": (row.get("sj_div") or "").strip(),      # BS/IS/CIS/CF/SCE
        "sj_nm": (row.get("sj_nm") or "").strip(),
        "thstrm_amount": _amount(row.get("thstrm_amount")),
        "frmtrm_amount": _amount(row.get("frmtrm_amount")),
        "bfefrmtrm_amount": _amount(row.get("bfefrmtrm_amount")),
        "currency": (row.get("currency") or "").strip(),
        "raw": row,
    }


def _prefer_statement_rows(rows: list[dict]) -> list[dict]:
    """연결(CFS) 우선, 없으면 별도(OFS)."""
    cfs = [r for r in rows if r.get("fs_div") == "CFS"]
    return cfs or [r for r in rows if r.get("fs_div") == "OFS"] or rows


def _compact_name(name: str) -> str:
    return "".join(str(name or "").replace(" ", "").split()).lower()


_ACCOUNT_ALIASES = {
    "revenue": ("매출액", "수익(매출액)", "영업수익", "매출", "수익"),
    "operating_income": ("영업이익", "영업손익"),
    "net_income": ("당기순이익", "당기순손익", "분기순이익", "반기순이익"),
    "controlling_net_income": ("지배기업의소유주에게귀속되는당기순이익", "지배기업소유주지분순이익",
                               "지배기업소유주에게귀속되는당기순이익"),
    "equity": ("자본총계",),
    "controlling_equity": ("지배기업의소유주에게귀속되는자본", "지배기업소유주지분",
                           "지배기업의소유주지분"),
    "assets": ("자산총계",),
    "liabilities": ("부채총계",),
    "eps": ("기본주당이익", "기본주당순이익"),
}


def _find_account(rows: list[dict], aliases: tuple[str, ...], *, sj_div: str | None = None):
    """계정명 alias 첫 매칭. sj_div 주입 시 해당 재무제표 구분 우선."""
    pool = [r for r in rows if not sj_div or r.get("sj_div") == sj_div]
    alias_keys = {_compact_name(a) for a in aliases}
    for r in pool:
        if _compact_name(r.get("account_nm")) in alias_keys:
            return r
    for r in pool:
        nm = _compact_name(r.get("account_nm"))
        if any(a in nm for a in alias_keys):
            return r
    return None


def _extract_major_accounts(rows: list[dict]) -> dict:
    """정규화 row list → KR 펀더멘털 핵심 계정. 연결 우선."""
    rows = _prefer_statement_rows(rows)

    def val(key, sj=None):
        r = _find_account(rows, _ACCOUNT_ALIASES[key], sj_div=sj)
        return r.get("thstrm_amount") if r else None

    net_income = val("controlling_net_income", "IS")
    if net_income is None:
        net_income = val("controlling_net_income", "CIS")
    if net_income is None:
        net_income = val("net_income", "IS")
    if net_income is None:
        net_income = val("net_income", "CIS")

    equity = val("controlling_equity", "BS")
    if equity is None:
        equity = val("equity", "BS")

    return {
        "revenue": val("revenue", "IS") or val("revenue", "CIS"),
        "operating_income": val("operating_income", "IS") or val("operating_income", "CIS"),
        "net_income": net_income,
        "equity": equity,
        "assets": val("assets", "BS"),
        "liabilities": val("liabilities", "BS"),
        "eps": val("eps", "IS") or val("eps", "CIS"),
        "fs_div": (rows[0].get("fs_div") if rows else None),
        "fs_nm": (rows[0].get("fs_nm") if rows else None),
    }


def financial_accounts(ticker: str, year: int | None = None, reprt_code: str = ANNUAL_REPORT) -> dict:
    """DART 단일회사 주요계정 원천 row. 실패 시 {"list": [], "error": ...}.

    reprt_code: 11011 사업보고서, 11013 1분기, 11012 반기, 11014 3분기.
    """
    if not _key():
        return {"error": "DART_API_KEY 미설정 — .env 에 추가하면 활성", "list": []}
    sc = stock_code(ticker)
    if not sc:
        return {"error": "KR 종목 아님 (예: 005930.KS)", "list": []}
    corp = corp_code_map().get(sc)
    if not corp:
        return {"error": f"corp_code 매핑 없음 ({sc})", "list": []}
    if year is None:
        year = date.today().year - 1
    import requests
    try:
        r = requests.get(f"{_BASE}/fnlttSinglAcnt.json", timeout=20, params={
            "crtfc_key": _key(), "corp_code": corp, "bsns_year": str(year), "reprt_code": reprt_code})
        d = r.json()
        if d.get("status") != "000":
            return {"error": d.get("message", "조회 실패"), "list": [], "status": d.get("status"),
                    "year": year, "reprt_code": reprt_code}
        rows = [_normalize_account_row(x) for x in d.get("list", [])]
        return {"ticker": ticker, "stock_code": sc, "corp_code": corp, "year": year,
                "reprt_code": reprt_code, "list": rows, "source": "DART"}
    except Exception as ex:
        return {"error": str(ex), "list": [], "year": year, "reprt_code": reprt_code}


def major_financials(ticker: str, year: int | None = None, reprt_code: str = ANNUAL_REPORT) -> dict:
    """DART 주요계정 요약. PER/PBR/ROE/EPS 계산용 원천."""
    acc = financial_accounts(ticker, year=year, reprt_code=reprt_code)
    if acc.get("error"):
        return {**acc, "financials": {}}
    fin = _extract_major_accounts(acc.get("list") or [])
    return {k: v for k, v in acc.items() if k != "list"} | {"financials": fin}
