"""tests/test_ranker_scores_by_ticker.py — US 랭커 점수 주입 경로 (무네트워크).

감사 후속(2026-08-21): US 모의 선택정책에서 **가중치 0.40 (최대) 인 `ranker` 축이
단 한 번도 주입된 적이 없었다.** crons/us_mock_track.py 는

    raw = ranker.scores_by_ticker([...]) if hasattr(ranker, "scores_by_ticker") else {}

로 호출하는데 `ml/ranker.py` 에 그 함수가 **없어서** hasattr 이 False → raw={} →
features 에 'ranker' 키 자체가 안 들어갔다. hasattr 가드라 예외도 안 나고 except 로그도
안 찍혀 **완전히 조용히** 실패했다(실측: 섀도 37종목 전부 ranker 부재).

그 결과 us_policy.score 의 분모 재정규화가 남은 축으로 쏠려, 레버리지 ETF 처럼
value/quality 가 기본값(0.5)인 종목들은 **상위 4개가 완전 동점**(0.583333=7/12)이 되어
편입 순서가 사실상 임의였다. KR 은 kr_ranker.kr_scores_by_ticker 가 있어 정상 동작.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pd = pytest.importorskip("pandas")

from ml import ranker  # noqa: E402


def test_scores_by_ticker_exists():
    """us_mock_track 이 hasattr 로 찾는 이름 — 없으면 랭커가 통째로 죽는다."""
    assert hasattr(ranker, "scores_by_ticker"), "US 랭커 주입 진입점 부재 — 회귀"


def test_scores_by_ticker_maps_ticker_to_score(monkeypatch):
    df = pd.DataFrame({"ticker": ["AAPL", "MSFT", "NVDA"], "score": [0.9, 0.5, 0.1]})
    monkeypatch.setattr(ranker, "rank_today", lambda **k: df)

    out = ranker.scores_by_ticker(["AAPL", "NVDA"])

    assert out == {"AAPL": 0.9, "NVDA": 0.1}     # 요청한 티커만


def test_scores_by_ticker_returns_all_when_no_filter(monkeypatch):
    df = pd.DataFrame({"ticker": ["AAPL", "MSFT"], "score": [0.7, 0.3]})
    monkeypatch.setattr(ranker, "rank_today", lambda **k: df)

    assert ranker.scores_by_ticker() == {"AAPL": 0.7, "MSFT": 0.3}


def test_scores_by_ticker_requests_enough_rows(monkeypatch):
    """top_n 이 요청 티커 수보다 작으면 대부분이 누락된다 — 충분히 요청해야 함."""
    seen = {}

    def _rank(**k):
        seen.update(k)
        return pd.DataFrame({"ticker": [], "score": []})

    monkeypatch.setattr(ranker, "rank_today", _rank)
    ranker.scores_by_ticker([f"T{i}" for i in range(40)])
    assert seen.get("top_n", 0) >= 40


def test_scores_by_ticker_graceful_on_failure(monkeypatch):
    def _boom(**k):
        raise RuntimeError("model missing")
    monkeypatch.setattr(ranker, "rank_today", _boom)

    assert ranker.scores_by_ticker(["AAPL"]) == {}     # 예외 전파 금지(best-effort)


def test_scores_by_ticker_empty_frame(monkeypatch):
    monkeypatch.setattr(ranker, "rank_today", lambda **k: pd.DataFrame())
    assert ranker.scores_by_ticker(["AAPL"]) == {}


def test_scores_by_ticker_skips_unusable_rows(monkeypatch):
    df = pd.DataFrame({"ticker": ["AAPL", None, "MSFT"], "score": [0.9, 0.5, None]})
    monkeypatch.setattr(ranker, "rank_today", lambda **k: df)
    assert ranker.scores_by_ticker() == {"AAPL": 0.9}
