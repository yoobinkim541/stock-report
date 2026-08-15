"""naver_consensus 순수 파서 + KR 포워드 게이지 단위 테스트 (무네트워크 fixture)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from providers import naver_consensus as nc


_INTEGRATION = {"consensusInfo": {"itemCode": "000660", "createDate": "2026-07-08",
                                  "recommMean": "4.00", "priceTargetMean": "3,547,917"}}
_ANNUAL = {"financeInfo": {
    "trTitleList": [{"isConsensus": "N", "key": "202412"}, {"isConsensus": "N", "key": "202512"},
                    {"isConsensus": "Y", "key": "202612"}],
    "rowList": [
        {"title": "EPS", "columns": {"202512": {"value": "58,955"}, "202612": {"value": "316,656"},
                                     "202412": {"value": "27,182"}}},
        {"title": "ROE", "columns": {"202512": {"value": "44.15"}, "202612": {"value": "97.49"}}},
        {"title": "PER", "columns": {"202612": {"value": "6.56"}}},
    ]}}


def test_parse_integration():
    got = nc.parse_integration(_INTEGRATION)
    assert got["target_mean"] == 3547917.0 and got["recomm_mean"] == 4.0
    assert nc.parse_integration({}) == {}            # 결측 graceful


def test_parse_annual_actual_vs_consensus():
    got = nc.parse_annual(_ANNUAL)
    assert got["actual"]["year"] == "2025" and got["actual"]["eps"] == 58955.0
    assert got["fwd"]["year"] == "2026" and got["fwd"]["eps"] == 316656.0
    assert got["fwd"]["roe"] == 97.49 and got["fwd"]["per"] == 6.56
    assert nc.parse_annual({}) == {}


def test_code_normalization():
    assert nc._code("000660.KS") == "000660" and nc._code("000660") == "000660"
    assert nc._code("MSFT") is None and nc._code("") is None


# ── ETF 핵심지표 (감사 후속 — KIS 총보수 공백 보강) ──────────────────────────────

_ETF_INTEGRATION = {"etfKeyIndicator": {
    "issuerName": "삼성자산운용(ETF)", "marketValue": "26조 17억",
    "dividendYieldTtm": 0.79, "totalNav": "25조 4,325억", "nav": "107,650.84",
    "deviationSign": "-", "deviationRate": 0.35, "totalFee": 0.15,
    "returnRate1m": -1.23, "returnRate3m": -12.37, "returnRate1y": 147.59,
}}


def test_parse_korean_won_combines_jo_and_eok():
    """KODEX200 라이브 확인값 — 25조 4,325억 = 25e12 + 4325e8 = 25,432,500,000,000."""
    assert nc._parse_korean_won("25조 4,325억") == 25_432_500_000_000.0


def test_parse_korean_won_handles_eok_only_and_plain_number():
    assert nc._parse_korean_won("4,325억") == 432_500_000_000.0
    assert nc._parse_korean_won("123456") == 123456.0
    assert nc._parse_korean_won(None) is None
    assert nc._parse_korean_won("") is None


def test_parse_etf_indicator_normalizes_fee_to_fraction_and_signed_deviation():
    """totalFee 는 퍼센트(0.15=0.15%) → 분수(0.0015). deviationSign='-' 이면 음수 괴리율."""
    got = nc.parse_etf_indicator(_ETF_INTEGRATION)

    assert got["expense_ratio"] == pytest.approx(0.0015)
    assert got["issuer"] == "삼성자산운용"    # "(ETF)" 접미사는 family 관례와 맞춰 제거
    assert got["nav"] == 107650.84
    assert got["premium_pct"] == -0.35
    assert got["total_assets"] == 25_432_500_000_000.0


def test_parse_etf_indicator_positive_deviation_sign():
    d = {"etfKeyIndicator": {"totalFee": "0.30", "deviationSign": "+", "deviationRate": "0.12"}}
    got = nc.parse_etf_indicator(d)
    assert got["premium_pct"] == 0.12


def test_parse_etf_indicator_empty_for_non_etf():
    assert nc.parse_etf_indicator(_INTEGRATION) == {}
    assert nc.parse_etf_indicator({}) == {}


def test_valuation_score_kr_naver_forward_unlocks():
    """Naver 컨센서스 병합 시 KR 도 포워드 축 채점 — 고ROE 성장주 '고평가' 편향 해소."""
    from dashboard import data
    # 하이닉스형: 트레일링 PER 39(부정)·컨센서스 fwd EPS 5배(긍정) → 혼합 점수
    m = {"market_type": "kr", "per": 39.0, "pbr": 13.8, "roe": 0.36,
         "eps_ttm": 58955.0, "eps_fwd": 316656.0, "forward_pe": 6.8,
         "kr_consensus_source": "naver"}
    c = {"target_mean": 3547917.0, "source": "naver"}
    got = data.valuation_score(2160000, m, c, {})
    assert got is not None and got["n"] >= 5          # 트레일링 2 + 포워드 3+ 혼합
    assert got["score"] > 0                            # 컨센서스 반영 → 저평가 방향
    # Naver 없이 같은 값이 야후에서 왔다면(kr_consensus_source 없음) 포워드 축 배제
    m2 = dict(m); m2.pop("kr_consensus_source")
    c2 = {"target_mean": 3547917.0}                    # source 없음 = 야후 → 불신
    got2 = data.valuation_score(2160000, m2, c2, {})
    assert got2 is None or got2["n"] < got["n"]        # 축 감소(트레일링만)
