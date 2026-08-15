"""
test_thirteenf.py — providers/thirteenf.py 13F 정보테이블 파싱 (무네트워크 순수 함수).

실측(2026-07): SEC 기술규격상 value 단위는 "천달러"지만 버크셔 실필링은 이미 달러
단위로 신고돼 있었다 — 파서는 배율 변환 없이 raw 값을 그대로 쓴다(회귀 방지 포인트).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import thirteenf  # noqa: E402

_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def test_filers_registry_includes_expanded_named_investors():
    """감사 후속 — 관심종목 기관허브에 이름만 있고 데이터 없던 8곳 중 7곳을 실 13F CIK로 연결.

    CIK 는 SEC EDGAR company search(data.sec.gov/submissions/CIK{cik}.json 의
    name 필드)로 실측 확인한 값 — founders_fund 는 펀드 빈티지별로 8개 별도
    필러(Growth/II~VII)라 단일 CIK 대표가 불가해 seed 로 남겨둠(2026-08-15)."""
    expected = {
        "citadel": "0001423053",
        "duquesne": "0001536411",
        "pershing_square": "0001336528",
        "point72": "0001603466",
        "third_point": "0001040273",
        "tudor": "0000923093",
        "nps": "0001608046",
    }
    for key, cik in expected.items():
        assert key in thirteenf.FILERS, f"{key} 가 FILERS 에 없음"
        assert thirteenf.FILERS[key]["cik"] == cik, f"{key} CIK 불일치"
    assert "founders_fund" not in thirteenf.FILERS


def test_latest_filing_meta_skip_walks_back_to_earlier_quarters(monkeypatch, tmp_path):
    """감사 후속 — return_proxy(보유종목 변동 기반 수익률 추정) 계산에 직전 분기 필링이
    필요해 latest_filing_meta 에 skip 파라미터 추가. 10-K 등 비13F 필링은 건너뛴다."""
    monkeypatch.setattr(thirteenf, "_CACHE_DIR", tmp_path)
    submissions = {
        "filings": {"recent": {
            "form": ["13F-HR", "10-K", "13F-HR", "13F-HR"],
            "accessionNumber": ["acc-q3", "acc-10k", "acc-q2", "acc-q1"],
            "filingDate": ["2026-08-14", "2026-07-01", "2026-05-15", "2026-02-14"],
            "primaryDocument": ["doc3.xml", "doc10k.xml", "doc2.xml", "doc1.xml"],
        }}
    }
    monkeypatch.setattr(thirteenf, "_get", lambda url: json.dumps(submissions).encode())

    assert thirteenf.latest_filing_meta("berkshire")["accession"] == "acc-q3"
    assert thirteenf.latest_filing_meta("berkshire", skip=1)["accession"] == "acc-q2"
    assert thirteenf.latest_filing_meta("berkshire", skip=2)["accession"] == "acc-q1"
    assert thirteenf.latest_filing_meta("berkshire", skip=3) is None


def _info_table_xml(rows: list[dict]) -> bytes:
    entries = []
    for r in rows:
        entries.append(f"""
  <infoTable>
    <nameOfIssuer>{r['issuer']}</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>{r['cusip']}</cusip>
    <value>{r['value']}</value>
    <shrsOrPrnAmt>
      <sshPrnamt>{r['shares']}</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
  </infoTable>""")
    return f'<informationTable xmlns="{_NS}">{"".join(entries)}</informationTable>'.encode()


def test_parse_info_table_reads_value_and_shares_without_scaling():
    """value·shares 는 파일 그대로(배율 변환 없음) — 실측 회귀 방지."""
    xml = _info_table_xml([
        {"issuer": "ALLY FINL INC", "cusip": "02005N100", "value": "498992850", "shares": "12719675"},
    ])
    rows = thirteenf._parse_info_table(xml)

    assert len(rows) == 1
    assert rows[0]["issuer"] == "ALLY FINL INC"
    assert rows[0]["value_usd"] == 498992850.0
    assert rows[0]["shares"] == 12719675.0


def test_parse_info_table_aggregates_duplicate_cusip_across_submanagers():
    """같은 종목이 복수 서브매니저 행으로 나뉘면 CUSIP 기준 합산돼야 한다."""
    xml = _info_table_xml([
        {"issuer": "APPLE INC", "cusip": "037833100", "value": "1000", "shares": "10"},
        {"issuer": "APPLE INC", "cusip": "037833100", "value": "2000", "shares": "20"},
        {"issuer": "APPLE INC", "cusip": "037833100", "value": "500", "shares": "5"},
    ])
    rows = thirteenf._parse_info_table(xml)

    assert len(rows) == 1
    assert rows[0]["value_usd"] == 3500.0
    assert rows[0]["shares"] == 35.0


def test_parse_info_table_sorts_by_value_descending():
    xml = _info_table_xml([
        {"issuer": "SMALL CO", "cusip": "111111111", "value": "100", "shares": "1"},
        {"issuer": "BIG CO", "cusip": "222222222", "value": "9000", "shares": "1"},
        {"issuer": "MID CO", "cusip": "333333333", "value": "500", "shares": "1"},
    ])
    rows = thirteenf._parse_info_table(xml)

    assert [r["issuer"] for r in rows] == ["BIG CO", "MID CO", "SMALL CO"]


def test_parse_info_table_skips_entries_without_cusip():
    xml = f"""<informationTable xmlns="{_NS}">
  <infoTable>
    <nameOfIssuer>NO CUSIP CO</nameOfIssuer>
    <value>100</value>
  </infoTable>
  <infoTable>
    <nameOfIssuer>VALID CO</nameOfIssuer>
    <cusip>444444444</cusip>
    <value>200</value>
    <shrsOrPrnAmt><sshPrnamt>2</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>""".encode()
    rows = thirteenf._parse_info_table(xml)

    assert len(rows) == 1
    assert rows[0]["issuer"] == "VALID CO"


def test_parse_info_table_empty_document_returns_empty_list():
    xml = f'<informationTable xmlns="{_NS}"></informationTable>'.encode()
    assert thirteenf._parse_info_table(xml) == []


def test_resolve_tickers_by_cusip_uses_openfigi_and_caches(monkeypatch, tmp_path):
    """CUSIP→티커는 OpenFIGI 응답을 읽고 디스크 캐시에 남겨 재조회를 피해야 한다."""
    cache_path = tmp_path / "cusip_ticker_map.json"
    monkeypatch.setattr(thirteenf, "_CUSIP_CACHE_PATH", cache_path)

    calls = []

    class _FakeResp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=20):
        calls.append(req.data)
        import json
        return _FakeResp(json.dumps([
            {"data": [{"ticker": "KO", "exchCode": "US"}]},
        ]).encode())

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    out = thirteenf._resolve_tickers_by_cusip(["191216100"])
    assert out["191216100"] == "KO"
    assert len(calls) == 1

    # 두 번째 호출은 캐시 히트 — OpenFIGI 재호출 없음
    out2 = thirteenf._resolve_tickers_by_cusip(["191216100"])
    assert out2["191216100"] == "KO"
    assert len(calls) == 1


def test_resolve_tickers_by_cusip_graceful_on_failure(monkeypatch, tmp_path):
    cache_path = tmp_path / "cusip_ticker_map.json"
    monkeypatch.setattr(thirteenf, "_CUSIP_CACHE_PATH", cache_path)

    import urllib.request

    def boom(req, timeout=20):
        raise RuntimeError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    out = thirteenf._resolve_tickers_by_cusip(["999999999"])
    assert out == {"999999999": None}
