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
