"""tests/test_insider_dart.py — QT2b 내부자(Form4)·DART 파싱·graceful (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import dart, insider

_FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerTradingSymbol>MSFT</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe John</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>400</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4():
    txs = insider.parse_form4(_FORM4)
    assert len(txs) == 1
    t = txs[0]
    assert t["symbol"] == "MSFT" and t["owner"] == "Doe John" and t["role"] == "CFO"
    assert t["date"] == "2026-06-15" and t["code"] == "P" and t["ad"] == "A"
    assert t["shares"] == 100.0 and t["price"] == 400.0 and t["value"] == 40000.0


def test_parse_form4_director_role():
    xml = _FORM4.replace(b"<officerTitle>CFO</officerTitle>", b"").replace(
        b"<isDirector>0</isDirector>", b"<isDirector>1</isDirector>")
    assert insider.parse_form4(xml)[0]["role"] == "Director"


def test_parse_form4_garbage():
    assert insider.parse_form4(b"not xml") == []
    assert insider.parse_form4(b"<x/>") == []


def test_dart_stock_code():
    assert dart.stock_code("005930.KS") == "005930"
    assert dart.stock_code("005930") == "005930"
    assert dart.stock_code("AAPL") is None
    assert dart.stock_code("00593") is None


def test_dart_parse_corpcode():
    xml = (b"<result><list><corp_code>00126380</corp_code>"
           b"<corp_name>Samsung</corp_name><stock_code>005930</stock_code></list>"
           b"<list><corp_code>00164779</corp_code><corp_name>NoStock</corp_name>"
           b"<stock_code> </stock_code></list></result>")
    m = dart._parse_corpcode(xml)
    assert m == {"005930": "00126380"}        # 종목코드 없는 항목 제외


def test_dart_graceful_without_key(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    out = dart.recent_disclosures("005930.KS")
    assert out["list"] == [] and "미설정" in out["error"]


def test_dart_graceful_non_kr(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "dummy")
    out = dart.recent_disclosures("AAPL")
    assert out["list"] == [] and "KR" in out["error"]


def test_dart_amount_parser():
    assert dart._amount("1,234") == 1234.0
    assert dart._amount("(1,234)") == -1234.0
    assert dart._amount("-") is None


def test_dart_extract_major_accounts_prefers_consolidated():
    raw = [
        {"account_nm": "자본총계", "fs_div": "OFS", "fs_nm": "재무제표", "sj_div": "BS", "thstrm_amount": "90"},
        {"account_nm": "자본총계", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "BS", "thstrm_amount": "100"},
        {"account_nm": "지배기업의 소유주에게 귀속되는 자본", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "BS", "thstrm_amount": "80"},
        {"account_nm": "매출액", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "IS", "thstrm_amount": "1,000"},
        {"account_nm": "영업이익", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "IS", "thstrm_amount": "120"},
        {"account_nm": "지배기업의 소유주에게 귀속되는 당기순이익", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "IS", "thstrm_amount": "75"},
        {"account_nm": "기본주당이익", "fs_div": "CFS", "fs_nm": "연결재무제표", "sj_div": "IS", "thstrm_amount": "5,432"},
    ]
    rows = [dart._normalize_account_row(r) for r in raw]

    fin = dart._extract_major_accounts(rows)

    assert fin["fs_div"] == "CFS"
    assert fin["revenue"] == 1000.0
    assert fin["operating_income"] == 120.0
    assert fin["net_income"] == 75.0
    assert fin["equity"] == 80.0
    assert fin["eps"] == 5432.0


def test_dart_financial_accounts_graceful_without_key(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    out = dart.financial_accounts("005930.KS", year=2025)
    assert out["list"] == [] and "미설정" in out["error"]


def test_dart_financial_accounts_request(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "dummy")
    monkeypatch.setattr(dart, "corp_code_map", lambda refresh=False: {"005930": "00126380"})
    calls = []

    class FakeResp:
        def json(self):
            return {
                "status": "000",
                "list": [
                    {"account_nm": "매출액", "fs_div": "CFS", "fs_nm": "연결재무제표",
                     "sj_div": "IS", "thstrm_amount": "1,000", "currency": "KRW"},
                ],
            }

    class FakeRequests:
        @staticmethod
        def get(url, timeout, params):
            calls.append((url, timeout, params))
            return FakeResp()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    out = dart.financial_accounts("005930.KS", year=2025)

    assert out["stock_code"] == "005930"
    assert out["corp_code"] == "00126380"
    assert out["list"][0]["account_nm"] == "매출액"
    assert out["list"][0]["thstrm_amount"] == 1000.0
    assert calls[0][2]["bsns_year"] == "2025"
    assert calls[0][2]["reprt_code"] == dart.ANNUAL_REPORT
