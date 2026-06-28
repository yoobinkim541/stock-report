"""providers/insider.py — 내부자거래(SEC Form 4) + 최근 공시 목록 (美·키 불요).

edgar._get(SEC 준수 UA)·_cik_map 재사용. submissions JSON 에서 Form 4 를 골라
원본 ownership XML(xsl 폴더 제거)을 파싱. parse_form4 는 순수함수(테스트 가능).
"""
from __future__ import annotations

import json
import time
from xml.etree import ElementTree as ET


def _num(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_form4(xml_bytes: bytes) -> list[dict]:
    """Form 4 ownership XML → 비파생 거래 리스트 (순수·무네트워크).

    code: P=매수 S=매도 A=무상취득(grant) M=옵션행사 등. ad: A=취득 D=처분.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []
    sym = (root.findtext(".//issuerTradingSymbol") or "").strip()
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    is_dir = (root.findtext(".//reportingOwnerRelationship/isDirector") or "0").strip()
    title = (root.findtext(".//reportingOwnerRelationship/officerTitle") or "").strip()
    role = title or ("Director" if is_dir in ("1", "true") else "—")
    out = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        sh = _num(tx.findtext(".//transactionAmounts/transactionShares/value"))
        px = _num(tx.findtext(".//transactionAmounts/transactionPricePerShare/value"))
        out.append({
            "symbol": sym, "owner": owner, "role": role,
            "date": (tx.findtext(".//transactionDate/value") or "").strip(),
            "code": (tx.findtext(".//transactionCoding/transactionCode") or "").strip(),
            "ad": (tx.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip(),
            "shares": sh, "price": px, "value": sh * px,
        })
    return out


def _cik(ticker: str) -> str | None:
    from providers import edgar
    return edgar._cik_map().get((ticker or "").upper())


def _submissions(cik: str) -> dict:
    from providers import edgar
    return json.loads(edgar._get(f"https://data.sec.gov/submissions/CIK{cik}.json"))


def _raw_form4_url(cik: str, acc_nodash: str, primary_doc: str) -> str:
    base = primary_doc.split("/")[-1]          # xslF345X06/form4.xml → form4.xml (원본)
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{base}"


def recent_insider(ticker: str, limit: int = 10) -> dict:
    """최근 내부자 거래 + 순매수 요약 (키 불요). 美 외 종목은 error."""
    from providers import edgar
    cik = _cik(ticker)
    if not cik:
        return {"error": "CIK 없음 (美 상장 아님?)", "transactions": []}
    try:
        rec = _submissions(cik).get("filings", {}).get("recent", {})
    except Exception as e:
        return {"error": str(e), "transactions": []}
    forms = rec.get("form", [])
    txs: list[dict] = []
    n = 0
    for i, f in enumerate(forms):
        if f != "4":
            continue
        if n >= limit:
            break
        n += 1
        try:
            url = _raw_form4_url(cik, rec["accessionNumber"][i].replace("-", ""),
                                 rec.get("primaryDocument", [""])[i])
            txs.extend(parse_form4(edgar._get(url)))
        except Exception:
            continue
        time.sleep(0.12)                        # SEC 10req/s 친화
    buys = sum(t["shares"] for t in txs if t["code"] == "P")
    sells = sum(t["shares"] for t in txs if t["code"] == "S")
    return {"transactions": txs, "net_buy_shares": buys - sells,
            "n_buys": sum(1 for t in txs if t["code"] == "P"),
            "n_sells": sum(1 for t in txs if t["code"] == "S")}


def recent_filings(ticker: str, limit: int = 15,
                   forms: tuple = ("8-K", "10-Q", "10-K", "S-1", "6-K", "DEF 14A")) -> dict:
    """최근 SEC 공시 목록 (美·키 불요)."""
    from providers import edgar
    cik = _cik(ticker)
    if not cik:
        return {"error": "CIK 없음 (美 상장 아님?)", "filings": []}
    try:
        rec = _submissions(cik).get("filings", {}).get("recent", {})
    except Exception as e:
        return {"error": str(e), "filings": []}
    out = []
    for i, f in enumerate(rec.get("form", [])):
        if forms and f not in forms:
            continue
        if len(out) >= limit:
            break
        acc = rec["accessionNumber"][i].replace("-", "")
        doc = rec.get("primaryDocument", [""])[i]
        out.append({
            "form": f, "date": rec.get("filingDate", [""])[i],
            "title": rec.get("primaryDocDescription", [""])[i] or f,
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}",
        })
    return {"filings": out}
